# uithoorn_checker.py

import os
import re
import json
import time
import unicodedata
from datetime import datetime, timedelta

import pytz
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, NoSuchElementException

TARGET_URL = "https://avo.hta.nl/uithoorn/Accommodation/Book/106"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")  # ← Secrets から注入

SCHEDULE = {
    "Monday":   ["20:00 - 21:30"],
    "Thursday": ["20:00 - 21:30"],
    "Saturday": ["17:00 - 18:30"],
    "Sunday":   ["15:30 - 17:00", "14:00 - 15:30"],
}
JP_DAY = {"Monday": "月曜日", "Thursday": "木曜日", "Saturday": "土曜日", "Sunday": "日曜日"}

def send_discord_message(message: str):
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
    """NBSP, 全角/半角, 各種ダッシュ, 余分な空白の揺れを吸収"""
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"[\u00A0\u2000-\u200B\u3000\s–—-]+", "", s)

def build_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--start-maximized")
    # Selenium Manager にドライバ解決を任せる（ChromeはActions側でインストール）
    return webdriver.Chrome(options=opts)

def check_availability():
    driver = None
    try:
        driver = build_driver()
        driver.get(TARGET_URL)

        # 1) 1.5 uur を選択（テキストに "1,5" を含むものを選ぶ）
        reservation_duration_dropdown = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.ID, "selectedTimeLength"))
        )
        select_len = Select(reservation_duration_dropdown)
        picked = False
        for opt in select_len.options:
            if "1,5" in unicodedata.normalize("NFKC", opt.text):
                select_len.select_by_value(opt.get_attribute("value"))
                picked = True
                break
        if not picked:
            raise RuntimeError("1.5時間の選択肢が見つかりません")

        # 2) NLの今日から2週間後の対象曜日を算出（Mon=1, Thu=4, Sat=6, Sun=7）
        nl_tz = pytz.timezone("Europe/Amsterdam")
        today_nl = datetime.now(nl_tz).date()
        dows = [1, 4, 6, 7]
        targets = [
            today_nl + timedelta(weeks=2) + timedelta(days=(dow - today_nl.isoweekday()) % 7)
            for dow in dows
        ]

        for future_date in targets:
            # 先に曜日名などを決めておく（例外時も正しく出すため）
            day_of_week_en = future_date.strftime("%A")
            day_of_week_jp = JP_DAY.get(day_of_week_en, "")
            required_times = SCHEDULE.get(day_of_week_en, [])

            for attempt in range(3):  # 軽いリトライでstale/遅延に強く
                try:
                    # 3) カレンダーを開く
                    calendar_input = WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable((By.ID, "datepicker"))
                    )
                    calendar_input.click()

                    # 年 → 月 の順に指定（年またぎ対策）
                    years = driver.find_elements(By.CLASS_NAME, "ui-datepicker-year")
                    if years:
                        Select(years[0]).select_by_value(str(future_date.year))
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

                    # 差し替え検出用に旧selectを掴む（無い実装もある）
                    try:
                        old_select = driver.find_element(By.ID, "customSelectedTimeSlot")
                    except NoSuchElementException:
                        old_select = None

                    WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.XPATH, day_xpath))).click()

                    # 旧selectがあればstaleまで待機＝差し替え完了の目安
                    if old_select is not None:
                        try:
                            WebDriverWait(driver, 20).until(EC.staleness_of(old_select))
                        except TimeoutException:
                            pass

                    # 新しいselectの出現と、optionが十分並ぶまで待機
                    WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.ID, "customSelectedTimeSlot"))
                    )
                    WebDriverWait(driver, 20).until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, "#customSelectedTimeSlot option")) > 1
                    )

                    # 最終取得して比較
                    time_dropdown = driver.find_element(By.ID, "customSelectedTimeSlot")
                    time_options = time_dropdown.find_elements(By.TAG_NAME, "option")
                    available_norm = [normalize_timeslot(o.text) for o in time_options if o.get_attribute("value")]

                    found = False
                    for t in required_times:
                        if normalize_timeslot(t) in available_norm:
                            found = True
                            msg = (
                                "体育館に空きがあります！\n"
                                f"日付: {future_date.strftime('%Y年%m月%d日')}（{day_of_week_jp}）\n"
                                f"時間: {t}"
                            )
                            print(msg)
                            send_discord_message(msg)

                    if not found:
                        print(f"{future_date.strftime('%Y年%m月%d日')}（{day_of_week_jp}）の枠は空いていません。")

                    break  # 成功したらその日付のリトライは終了

                except (StaleElementReferenceException, TimeoutException) as e:
                    if attempt < 2:
                        print(f"[WARN] 一時エラー({type(e).__name__})。{future_date.strftime('%Y年%m月%d日')}（{day_of_week_jp}）を再試行 {attempt+1}/3")
                        time.sleep(1)
                        continue
                    else:
                        print(f"[ERROR] {type(e).__name__} が連続発生。{future_date.strftime('%Y年%m月%d日')}（{day_of_week_jp}）をスキップ")
                        break

            time.sleep(2)

    except Exception as e:
        print(f"エラーが発生しました: {repr(e)}")
        send_discord_message(f"🚨 スクリプト実行中にエラーが発生しました: {repr(e)}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

if __name__ == "__main__":
    check_availability()
