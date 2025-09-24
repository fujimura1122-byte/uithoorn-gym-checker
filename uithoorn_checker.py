from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException
import time
from datetime import datetime, timedelta
import requests
import json
import pytz
import re
import os
import unicodedata

# Discord Webhook URLを環境変数から取得
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

def send_discord_message(message):
    data = {"content": message}
    headers = {"Content-Type": "application/json"}
    if not WEBHOOK_URL:
        print("[WARN] DISCORD_WEBHOOK_URL is not set")
        return
    try:
        requests.post(WEBHOOK_URL, data=json.dumps(data), headers=headers, timeout=10)
    except Exception as e:
        print(f"Discord通知の送信中にエラーが発生しました: {e}")

def normalize_timeslot(s: str) -> str:
    # Unicode正規化で文字種の揺れを吸収
    s = unicodedata.normalize('NFKC', s)
    # 半角/全角スペース、各種ダッシュ記号をハイフンに統一し、それ以外の空白も除去
    return re.sub(r'[\s–—-]+', '', s)

def check_availability():
    driver = None
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

        service = webdriver.chrome.service.Service(ChromeDriverManager().install())
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
        
        reservation_duration_dropdown = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.ID, "selectedTimeLength"))
        )
        select = Select(reservation_duration_dropdown)
        
        found_duration = False
        for option in select.options:
            if "1,5 uur" in option.text:
                select.select_by_value(option.get_attribute("value"))
                found_duration = True
                break
        if not found_duration:
            raise RuntimeError("1.5時間の選択肢が見つかりません")

        schedule = {
            'Monday': ['20:00 - 21:30'],
            'Thursday': ['20:00 - 21:30'],
            'Saturday': ['17:00 - 18:30'],
            'Sunday': ['15:30 - 17:00', '14:00 - 15:30']
        }

        nl_timezone = pytz.timezone('Europe/Amsterdam')
        today_nl = datetime.now(nl_timezone).date()
        
        future_dates_to_check = []
        for day in [1, 4, 6, 7]:
            date_to_check = today_nl + timedelta(weeks=2) + timedelta(days=(day - today_nl.isoweekday()) % 7)
            future_dates_to_check.append(date_to_check)
        
        for future_date in future_dates_to_check:
            calendar_input = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.ID, "datepicker"))
            )
            calendar_input.click()

            month_dropdown = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.CLASS_NAME, "ui-datepicker-month"))
            )
            select_month = Select(month_dropdown)
            select_month.select_by_value(str(future_date.month - 1))

            year_dropdowns = driver.find_elements(By.CLASS_NAME, "ui-datepicker-year")
            if year_dropdowns:
                select_year = Select(year_dropdowns[0])
                select_year.select_by_value(str(future_date.year))

            day_xpath = f"//table[contains(@class,'ui-datepicker-calendar')]//td[not(contains(@class,'ui-datepicker-other-month'))]/a[text()='{future_date.day}']"
            WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, day_xpath))
            ).click()

            try:
                time_dropdown = WebDriverWait(driver, 20).until(
                    EC.element_to_be_clickable((By.ID, "customSelectedTimeSlot"))
                )
                time_options = time_dropdown.find_elements(By.TAG_NAME, "option")
                
                available_times_cleaned = [normalize_timeslot(o.text) for o in time_options if o.get_attribute('value')]

                day_of_week_en = future_date.strftime("%A")
                required_times = schedule.get(day_of_week_en, [])

                day_of_week_jp = ""
                if day_of_week_en == 'Monday': day_of_week_jp = "月曜日"
                elif day_of_week_en == 'Thursday': day_of_week_jp = "木曜日"
                elif day_of_week_en == 'Saturday': day_of_week_jp = "土曜日"
                elif day_of_week_en == 'Sunday': day_of_week_jp = "日曜日"
                
                found_availability = False
                for required_time in required_times:
                    required_time_cleaned = normalize_timeslot(required_time)
                    if required_time_cleaned in available_times_cleaned:
                        found_availability = True
                        message = f"体育館に空きがあります！\n日付: {future_date.strftime('%Y年%m月%d日')}（{day_of_week_jp}）\n時間: {required_time}"
                        print(message)
                        send_discord_message(message)

                if not found_availability:
                    print(f"{future_date.strftime('%Y年%m月%d日')}（{day_of_week_jp}）の枠は空いていません。")
            
            except TimeoutException:
                print(f"時間スロットがロードされませんでした。{future_date.strftime('%Y年%m月%d日')}（{day_of_week_jp}）の枠は空いていません。")

            time.sleep(2) # 次の確認のために少し待機

    except Exception as e:
        print(f"エラーが発生しました: {e}")
        send_discord_message(f"🚨 スクリプト実行中にエラーが発生しました: {e}")
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    check_availability()
