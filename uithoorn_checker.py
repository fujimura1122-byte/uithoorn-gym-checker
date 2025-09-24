from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import time
from datetime import datetime, timedelta
import requests
import json
import pytz
import re

# DiscordのWebhook URLを設定
# **必ず、あなたが取得した実際のWebhook URLに置き換えてください。**
WEBHOOK_URL = "https://discord.com/api/webhooks/1420476314225344572/MItQXAd9c0w3_kRT6tbGUpZOJpmOX-eR1Mvddq_C3sAwKunDFKyUzRXsoaRpMmr5jG2X"

def send_discord_message(message):
    data = {
        "content": message
    }
    headers = {
        "Content-Type": "application/json"
    }
    try:
        requests.post(WEBHOOK_URL, data=json.dumps(data), headers=headers)
    except Exception as e:
        print(f"Discord通知の送信中にエラーが発生しました: {e}")

def check_availability():
    driver = None  # driver変数をNoneで初期化
    try:
        # ヘッドレスモードで実行するためのオプションを設定
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--start-maximized")

        service = webdriver.chrome.service.Service()
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        driver.get("https://avo.hta.nl/uithoorn/Accommodation/Book/106")
        
        # 予約時間（1.5 uur）を選択
        reservation_duration_dropdown = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.ID, "selectedTimeLength"))
        )
        select = Select(reservation_duration_dropdown)
        select.select_by_value("1,5")

        # 確認したい曜日と時間帯の辞書
        schedule = {
            'Monday': ['20:00 - 21:30'],
            'Thursday': ['20:00 - 21:30'],
            'Saturday': ['17:00 - 18:30'],
            'Sunday': ['15:30 - 17:00', '14:00 - 15:30']
        }

        # NL時間で現在の日付を取得
        nl_timezone = pytz.timezone('Europe/Amsterdam')
        today_nl = datetime.now(nl_timezone).date()
        
        # 2週間後の月、木、土、日の日付を計算
        future_dates_to_check = []
        for day in [1, 4, 6, 7]:
            date_to_check = today_nl + timedelta(weeks=2) + timedelta(days=(day - today_nl.isoweekday()) % 7)
            future_dates_to_check.append(date_to_check)
        
        for future_date in future_dates_to_check:
            # カレンダーを開く
            calendar_input = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.ID, "datepicker"))
            )
            calendar_input.click()

            # 月を選択
            month_dropdown = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.CLASS_NAME, "ui-datepicker-month"))
            )
            select_month = Select(month_dropdown)
            select_by_value(str(future_date.month - 1))

            # 日付を選択
            WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, f"//a[text()='{future_date.day}']"))
            ).click()

            # ここで時間帯のドロップダウンメニューが完全に表示されるまで待機
            time_dropdown = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.ID, "customSelectedTimeSlot"))
            )
            time_options = time_dropdown.find_elements(By.TAG_NAME, "option")
            available_times = [option.text.strip().replace(" ", "") for option in time_options]

            # 空き状況を確認
            day_of_week_en = future_date.strftime("%A")
            required_times = schedule.get(day_of_week_en, [])

            day_of_week_jp = ""
            if day_of_week_en == 'Monday': day_of_week_jp = "月曜日"
            elif day_of_week_en == 'Thursday': day_of_week_jp = "木曜日"
            elif day_of_week_en == 'Saturday': day_of_week_jp = "土曜日"
            elif day_of_week_en == 'Sunday': day_of_week_jp = "日曜日"
            
            found_availability = False
            
            for required_time in required_times:
                if required_time in available_times:
                    found_availability = True
                    message = f"体育館に空きがあります！\n日付: {future_date.strftime('%Y年%m月%d日')}（{day_of_week_jp}）\n時間: {required_time}"
                    print(message)
                    send_discord_message(message)

            if not found_availability:
                print(f"{future_date.strftime('%Y年%m月%d日')}（{day_of_week_jp}）の枠は空いていません。")
            
            time.sleep(2) # 次の確認のために少し待機

    except Exception as e:
        print(f"エラーが発生しました: {e}")
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    check_availability()
