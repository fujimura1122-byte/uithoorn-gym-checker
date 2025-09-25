from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
import time
from datetime import datetime, timedelta
import requests
import json
import pytz
import re
import os
import unicodedata
import traceback

# Discord Webhook URLを環境変数から取得
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

def send_discord_message(message: str):
    if not WEBHOOK_URL:
        print("[WARN] DISCORD_WEBHOOK_URL is not set")
        return
    try:
        requests.post(WEBHOOK_URL, data=json.dumps({"content": message}),
                      headers={"Content-Type": "application/json"}, timeout=10)
    except Exception as e:
        print(f"Discord通知エラー: {e}")

# NBSPやダッシュの揺れを吸収して安全に比較
def normalize_timeslot(s: str) -> str:
    s = unicodedata.normalize('NFKC', s)
    return re.sub(r'[\u00A0\u2000-\u200B\u3000\s–—-]+', '', s)

def check_availability():
    driver = None
    try:
        # ---- Chrome (headless) 起動 ----
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        driver.get("https://avo.hta.nl/uithoorn/Accommodation/Book/106")
        
        # クッキー同意バナーに対応
        try:
            cookie_accept_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, '//button[contains(text(),"Accepteer")]'))
            )
            cookie_accept_button.click()
        except TimeoutException:
            pass
        
        # ---- 1.5 uur を選択（テキストで安全に）----
        reservation_duration_dropdown = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.ID, "selectedTimeLength"))
        )
        select_len = Select(reservation_duration_dropdown)
        picked = False
        for opt in select_len.options:
            if "1,5 uur" in opt.text:
                select_len.select_by_value(opt.get_attribute("value"))
                picked = True
                break
        if not picked:
            raise RuntimeError("1.5時間の選択肢が見つかりません")

        # ---- チェックしたい枠 ----
        schedule = {
            'Monday': ['20:00 - 21:30'],
            'Thursday': ['20:00 - 21:30'],
            'Saturday': ['17:00 - 18:30'],
            'Sunday': ['15:30 - 17:00', '14:00 - 15:30']
        }

        # ---- NLの今日から 2週間後の対象曜日を算出 ----
        nl_tz = pytz.timezone('Europe/Amsterdam')
        today_nl = datetime.now(nl_tz).date()
        targets = []
        for dow in [1, 4, 6, 7]:  # Mon=1, Thu=4, Sat=6, Sun=7
            date_to_check = today_nl + timedelta(weeks=2) + timedelta(days=(dow - today_nl.isoweekday()) % 7)
            targets.append(date_to_check)

        for future_date in targets:
            for attempt in range(3):
                try:
                    # ---- カレンダーを開く ----
                    calendar_input = WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable((By.ID, "datepicker"))
                    )
                    calendar_input.click()

                    # 年→月の順で指定（年またぎ対策）
                    years = driver.find_elements(By.CLASS_NAME, "ui-datepicker-year")
                    if years:
                        Select(years[0]).select_by_value(str(future_date.year))
                    month_dropdown = WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable((By.CLASS_NAME, "ui-datepicker-month"))
                    )
                    Select(month_dropdown).select_by_value(str(future_date.month - 1))

                    # 当月セルのみをクリック
                    day_xpath = ("//table[contains(@class,'ui-datepicker-calendar')]"
                                 "//td[not(contains(@class,'ui-datepicker-other-month'))]"
                                 f"/a[text()='{future_date.day}']")
                    
                    old_select = driver.find_element(By.ID, "customSelectedTimeSlot")
                    WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.XPATH, day_xpath))).click()
                    
                    try:
                        WebDriverWait(driver, 20).until(EC.staleness_of(old_select))
                    except TimeoutException:
                        pass
                    
                    new_select = WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.ID, "customSelectedTimeSlot"))
                    )
                    WebDriverWait(driver, 20).until(
                        lambda d: len(new_select.find_elements(By.TAG_NAME, "option")) > 1
                    )

                    # 利用可能な枠を正規化して取得
                    time_options = new_select.find_elements(By.TAG_NAME, "option")
                    available = [normalize_timeslot(o.text) for o in time_options if o.get_attribute('value')]

                    # 判定
                    dow_en = future_date.strftime("%A")
                    req_times = schedule.get(dow_en, [])
                    JP = {"Monday":"月曜日","Thursday":"木曜日","Saturday":"土曜日","Sunday":"日曜日"}
                    dow_jp = JP.get(dow_en, "")

                    found = False
                    for t in req_times:
                        if normalize_timeslot(t) in available:
                            found = True
                            msg = f"体育館に空きがあります！\n日付: {future_date.strftime('%Y年%m月%d日')}（{dow_jp}）\n時間: {t}"
                            print(msg)
                            send_discord_message(msg)

                    if not found:
                        print(f"{future_date.strftime('%Y年%m月%d日')}（{dow_jp}）の枠は空いていません。")

                    break  # 成功したらリトライを終了

                except (StaleElementReferenceException, TimeoutException) as e:
                    if attempt < 2:
                        print(f"[WARN] 一時エラー({type(e).__name__})。{future_date.strftime('%Y年%m月%d日')}（{dow_jp}）を再試行 {attempt+1}/3")
                        time.sleep(1)
                        continue
                    else:
                        print(f"[ERROR] {type(e).__name__} が連続発生。{future_date.strftime('%Y年%m月%d日')}（{dow_jp}）をスキップ")
                        raise  # 3回失敗したら例外を再発生させる

            time.sleep(2)

    except Exception as e:
        print(f"エラーが発生しました: {e}")
        send_discord_message(f"🚨 スクリプト実行中にエラーが発生しました: {e}")
        print(traceback.format_exc())
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    check_availability()
