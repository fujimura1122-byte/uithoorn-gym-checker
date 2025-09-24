# uithoorn_checker.py
# Gymzaal A（Uithoorn）の2週間後の空き枠をチェックし、見つかったらDiscordに通知します。

from __future__ import annotations

import os
import re
import json
import time
import traceback
import unicodedata
from datetime import datetime, timedelta

import pytz
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, NoSuchElementException

# =========================================================
# 設定
# =========================================================

TARGET_URL = "https://avo.hta.nl/uithoorn/Accommodation/Book/106"

# Discord Webhook（GitHub Actions の Secrets から注入）
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# 監視する曜日と時間帯
SCHEDULE = {
    "Monday":   ["20:00 - 21:30"],
    "Thursday": ["20:00 - 21:30"],
    "Saturday": ["17:00 - 18:30"],
    "Sunday":   ["15:30 - 17:00", "14:00 - 15:30"],
}

JP_DAY = {"Monday": "月曜日", "Thursday": "木曜日", "Saturday": "土曜日", "Sunday": "日曜日"}

# デバッグログを少し出したいときは True
DEBUG = False


# =========================================================
# ユーティリティ
# =========================================================

def send_discord_message(message: str) -> None:
    if not WEBHOOK_URL:
        print("[WARN] DISCORD_WEBHOOK_URL is not set")
        return
    try:
        requests.post(
            WEBHOOK_URL,
            data=json.dumps({"content": message}),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
    except Exception as e:
        print(f"[WARN] Discord通知エラー: {e}")


def normalize_timeslot(s: str) -> str:
    """
    '20:00 - 21:30' のような表示揺れ（NBSP, 全角/半角, en/em dash, ゼロ幅空白など）を吸収。
    比較はこの正規化後の文字列で行う。
    """
    s = unicodedata.normalize("NFKC", s)
    # NBSP \u00A0、ゼロ幅や全角空白 \u2000-\u200B \u3000、通常の空白 \s、各種ダッシュをまとめて処理
    return re.sub(r"[\u00A0\u2000-\u200B\u3000\s–—--]+", "", s)  # -(U+2011)も含む


_time_pair_re = re.compile(r"(\d{1,2}:\d{2}).*?(\d{1,2}:\d{2})")


def parse_time_pair(s: str) -> tuple[str, str] | None:
    """
    '20:00 - 21:30' -> ('20:00','21:30') のように時刻ペアを抽出。
    """
    s = unicodedata.normalize("NFKC", s)
    m = _time_pair_re.search(s)
    return m.groups() if m else None


def build_chrome() -> webdriver.Chrome:
    """
    GitHub Actions（setup-chrome + setup-chromedriver）が入っている前提。
    それでも見つからない場合は webdriver_manager でフォールバック。
    """
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    # setup-chrome が渡すことのある環境変数を利用（なければデフォルトパス）
    chrome_path = os.getenv("CHROME_PATH") or "/usr/bin/google-chrome"
    if os.path.exists(chrome_path):
        chrome_options.binary_location = chrome_path

    # まずは PATH 上の chromedriver を使う
    try:
        service = Service()  # PATH解決に任せる
        return webdriver.Chrome(service=service, options=chrome_options)
    except Exception as e:
        # ローカル実行用のフォールバック（webdriver_manager）
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            return webdriver.Chrome(service=service, options=chrome_options)
        except Exception:
            # 最初の例外も含めて再送出
            raise e


def accept_cookie_if_any(driver: webdriver.Chrome) -> None:
    """
    Cookie同意が出ている場合だけ、無視しても問題ない程度にクリックを試みる。
    （言語バリエーションも少し広めにカバー）
    """
    try:
        btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//button[contains(translate(., 'ACEPTRODVK', 'aceptroDVK'), 'accept') "
                " or contains(., 'Accepteer') "
                " or contains(., 'Accepteren') "
                " or contains(., 'Akkoord') "
                " or contains(., 'Alles accepteren')]"
            ))
        )
        btn.click()
    except TimeoutException:
        pass
    except Exception as e:
        if DEBUG:
            print(f"[DEBUG] Cookie同意クリック失敗: {e}")


# =========================================================
# メインロジック
# =========================================================

def check_availability() -> None:
    driver = None
    try:
        driver = build_chrome()
        driver.get(TARGET_URL)
        accept_cookie_if_any(driver)

        # ---- 1.5 uur を選択（テキストに "1,5" を含むものを選ぶ）----
        dropdown = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.ID, "selectedTimeLength"))
        )
        select_len = Select(dropdown)
        picked = False
        for opt in select_len.options:
            if "1,5" in unicodedata.normalize("NFKC", opt.text):
                select_len.select_by_value(opt.get_attribute("value"))
                picked = True
                break
        if not picked:
            raise RuntimeError("1.5時間の選択肢が見つかりません")

        # ---- 2週間後の対象曜日を算出 ----
        nl_tz = pytz.timezone("Europe/Amsterdam")
        today_nl = datetime.now(nl_tz).date()

        # Mon=1, Thu=4, Sat=6, Sun=7
        dows = [1, 4, 6, 7]
        future_dates = [
            today_nl + timedelta(weeks=2) + timedelta(days=(dow - today_nl.isoweekday()) % 7)
            for dow in dows
        ]

        for future_date in future_dates:
            # ---- カレンダー操作 ----
            date_input = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.ID, "datepicker"))
            )
            date_input.click()

            # 年→月の順に設定（年またぎ安全）
            year_elems = driver.find_elements(By.CLASS_NAME, "ui-datepicker-year")
            if year_elems:
                Select(year_elems[0]).select_by_value(str(future_date.year))

            month_dropdown = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.CLASS_NAME, "ui-datepicker-month"))
            )
            Select(month_dropdown).select_by_value(str(future_date.month - 1))

            # 当月セルのみクリック
            day_xpath = (
                "//table[contains(@class,'ui-datepicker-calendar')]"
                "//td[not(contains(@class,'ui-datepicker-other-month'))]"
                f"/a[text()='{future_date.day}']"
            )

            # 差し替え検知のために古い select を掴んでおく
            try:
                old_select = driver.find_element(By.ID, "customSelectedTimeSlot")
            except NoSuchElementException:
                old_select = None

            WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.XPATH, day_xpath))).click()

            # select が差し替わる/出現するまで待つ
            if old_select is not None:
                try:
                    WebDriverWait(driver, 20).until(EC.staleness_of(old_select))
                except TimeoutException:
                    # 差し替わらない実装の場合もあるので無視
                    pass

            new_select = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.ID, "customSelectedTimeSlot"))
            )
            WebDriverWait(driver, 20).until(
                lambda d: len(new_select.find_elements(By.TAG_NAME, "option")) > 1
            )

            # ---- オプション解析（テキスト＆valueの両対応）----
            options = new_select.find_elements(By.TAG_NAME, "option")

            # 正規化済みテキスト一覧（テキスト一致用）
            available_norm = [normalize_timeslot(o.text) for o in options if o.get_attribute("value")]

            # value マップ（将来 value 判定に切り替えたいとき用）
            value_map = {}
            for o in options:
                p = parse_time_pair(o.text)
                if p:
                    value_map[p] = o.get_attribute("value")

            if DEBUG:
                print("[DEBUG] available (normalized):", available_norm)

            # ---- 必要枠と照合 ----
            dow_en = future_date.strftime("%A")
            req_times = SCHEDULE.get(dow_en, [])
            dow_jp = JP_DAY.get(dow_en, "")

            found = False
            for t in req_times:
                if normalize_timeslot(t) in available_norm:
                    found = True
                    msg = (
                        "体育館に空きがあります！\n"
                        f"日付: {future_date.strftime('%Y年%m月%d日')}（{dow_jp}）\n"
                        f"時間: {t}"
                    )
                    print(msg)
                    send_discord_message(msg)

            if not found:
                print(f"{future_date.strftime('%Y年%m月%d日')}（{dow_jp}）の枠は空いていません。")

            time.sleep(2)

    except Exception as e:
        print(f"エラーが発生しました: {repr(e)}")
        print(traceback.format_exc())
        send_discord_message(f"🚨 スクリプト実行中にエラーが発生しました: {repr(e)}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    check_availability()
