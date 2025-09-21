"""
Uithoorn Gym Availability Checker
---------------------------------

- Opens the Uithoorn booking page for "Brede School Legmeer / Gymzaal A"
- Selects duration "1,5 uur"
- Checks availability for target weekday/time windows (2 weeks ahead):
    Mon 20:00–21:30
    Thu 20:00–21:30
    Sat 17:00–18:30
    Sun 14:00–15:30 and 15:30–17:00
- Prints a WhatsApp-ready summary message and saves a CSV report
"""

import os
import time
import csv
import datetime as dt
from dataclasses import dataclass
from typing import List, Dict, Tuple

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options

# ----------------------- Config -----------------------

TARGET_WINDOWS = {
    0: [("20:00", "21:30")],  # Monday
    3: [("20:00", "21:30")],  # Thursday
    5: [("17:00", "18:30")],  # Saturday
    6: [("14:00", "15:30"), ("15:30", "17:00")],  # Sunday
}

DURATION_TEXT = "1,5 uur"
FACILITY_TEXT = "Brede School Legmeer / Gymzaal A"

# ----------------------- Helpers -----------------------

def log(msg: str):
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

@dataclass
class Slot:
    date: dt.date
    start: str
    end: str

    def as_row(self):
        return [self.date.isoformat(), self.start, self.end]


def get_target_dates_two_weeks_ahead(today: dt.date | None = None) -> Dict[int, dt.date]:
    if today is None:
        today = dt.date.today()
    future = today + dt.timedelta(days=14)
    monday = future - dt.timedelta(days=future.weekday())
    mapping = {}
    for wd in TARGET_WINDOWS.keys():
        mapping[wd] = monday + dt.timedelta(days=wd)
    return mapping

# ----------------------- Selenium core -----------------------

def accept_cookies_if_present(driver):
    try:
        texts = [
            "Accepteer alles", "Alles accepteren", "Accepteren",
            "Accept all", "I agree", "OK", "Akkoord", "Ja, ik ga akkoord"
        ]
        for t in texts:
            btns = driver.find_elements(By.XPATH, f"//button[normalize-space()='{t}'] | //*[self::a or self::span or self::div][normalize-space()='{t}']")
            if btns:
                btns[0].click()
                time.sleep(0.5)
                return
        btns = driver.find_elements(By.XPATH, "//button[contains(.,'accep') or contains(.,'Accep') or contains(.,'accept')]")
        if btns:
            btns[0].click()
            time.sleep(0.5)
    except Exception:
        pass

def build_driver(headless: bool) -> webdriver.Chrome:
    opts = Options()
    chrome_bin = os.getenv("CHROME_BIN") or os.getenv("CHROME_PATH")
    if chrome_bin:
        opts.binary_location = chrome_bin
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,900")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)

def safe_click(driver, by, selector, timeout=20):
    el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, selector)))
    el.click()
    return el

def safe_find(driver, by, selector, timeout=20):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, selector)))

def login_if_needed(driver, username: str, password: str):
    if not username:
        return
    try:
        candidates = driver.find_elements(By.XPATH, "//a[normalize-space()='Login' or normalize-space()='Inloggen'] | //button[normalize-space()='Login' or normalize-space()='Inloggen']")
        if candidates:
            candidates[0].click()
            safe_find(driver, By.NAME, "username", 15).send_keys(username)
            safe_find(driver, By.NAME, "password", 15).send_keys(password)
            safe_click(driver, By.XPATH, "//button[@type='submit' or .='Login' or .='Inloggen']", 15)
            time.sleep(2)
    except Exception:
        pass

def select_duration(driver):
    safe_click(driver, By.XPATH, "//label[contains(., 'Hoe lang wilt u reserveren?')]/following::*[self::select or self::button][1]")
    try:
        select = driver.find_element(By.XPATH, "//label[contains(., 'Hoe lang wilt u reserveren?')]/following::select[1]")
        from selenium.webdriver.support.ui import Select
        Select(select).select_by_visible_text(DURATION_TEXT)
        return
    except:
        pass
    try:
        safe_click(driver, By.XPATH, f"//*[self::li or self::div or self::button][normalize-space()='{DURATION_TEXT}']")
    except:
        safe_click(driver, By.XPATH, f"//*[contains(normalize-space(), '{DURATION_TEXT}')]")

def open_facility(driver):
    try:
        safe_click(driver, By.XPATH, f"//*[contains(., '{FACILITY_TEXT}') and (self::a or self::button or self::div)]")
        time.sleep(1)
    except Exception:
        pass

def go_to_month(driver, target: dt.date):
    def month_key(text: str) -> Tuple[int, int]:
        text = text.strip().lower()
        months = {
            'januari':1, 'februari':2, 'maart':3, 'april':4, 'mei':5, 'juni':6,
            'juli':7, 'augustus':8, 'september':9, 'oktober':10, 'november':11, 'december':12
        }
        for name, idx in months.items():
            if name in text:
                import re
                m = re.search(r"(20\d{2})", text)
                year = int(m.group(1)) if m else dt.date.today().year
                return idx, year
        import re
        m = re.search(r"(\d{1,2})[\-/ ](\d{4})", text)
        if m:
            return int(m.group(1)), int(m.group(2))
        return -1, -1

    header = safe_find(driver, By.XPATH, "//*[self::h2 or self::div][contains(@class,'calendar') or contains(@class,'month')][1]", 10)
    next_btn = driver.find_elements(By.XPATH, "//button[contains(@aria-label,'Next') or contains(@aria-label,'Volgende') or contains(.,'>') or contains(.,'›')]")
    target_key = (target.month, target.year)

    for _ in range(12):
        label_text = header.text
        cur_key = month_key(label_text)
        if cur_key == target_key:
            return
        if next_btn:
            next_btn[0].click()
            time.sleep(0.5)
        else:
            safe_click(driver, By.XPATH, "//*[self::button or self::a][contains(.,'Volgende') or contains(.,'Next') or contains(.,'›') or contains(.,'>')]")
            time.sleep(0.5)

def pick_date(driver, date_obj: dt.date):
    day_str = str(date_obj.day)
    candidates = driver.find_elements(By.XPATH, f"//td[not(contains(@class,'disabled')) and .//*[normalize-space()='{day_str}']] | //*[self::button or self::a][normalize-space()='{day_str}']")
    if not candidates:
        candidates = driver.find_elements(By.XPATH, f"//*[normalize-space()='{day_str}']")
    if candidates:
        candidates[0].click()
        time.sleep(0.5)
    else:
        raise RuntimeError(f"Could not click date {date_obj}")

def read_available_times(driver) -> List[Tuple[str, str]]:
    container = safe_find(driver, By.XPATH, "//*[contains(.,'Welke tijd')]/following::*[self::div or self::ul][1]", 10)
    items = container.find_elements(By.XPATH, ".//*[contains(text(),':') and (contains(text(),'–') or contains(text(),'-'))]")
    results = []
    for el in items:
        txt = el.text.strip().replace('\u2013', '-').replace('–', '-').replace('—','-')
        if '-' in txt:
            parts = [p.strip() for p in txt.split('-')]
            if len(parts) == 2:
                results.append((parts[0], parts[1]))
    return sorted(list(set(results)))

def match_targets(available: List[Tuple[str, str]], targets: List[Tuple[str, str]]):
    avail_set = {(a[0], a[1]) for a in available}
    return [(s, e) for (s, e) in targets if (s, e) in avail_set]

def compose_whatsapp_message(hits: List[Slot]) -> str:
    if not hits:
        return "今のところ2週間後の対象枠に空きはありません。\nまた後で確認します。"
    lines = ["【空き枠（2週間後）】"]
    wd_ja = ["月","火","水","木","金","土","日"]
    for slot in sorted(hits, key=lambda s: (s.date, s.start)):
        w = wd_ja[slot.date.weekday()]
        lines.append(f"{slot.date:%m/%d}（{w}） {slot.start}-{slot.end}")
    lines.append("参加できる方は返信ください！")
    return "\n".join(lines)

# ----------------------- Main -----------------------

def main():
    load_dotenv()
    base_url = os.getenv("BASE_URL", "https://avo.hta.nl/uithoorn/Accommodation/Book/106")
    username = os.getenv("USERNAME", "")
    password = os.getenv("PASSWORD", "")
    headless = os.getenv("HEADLESS", "true").lower() == "true"

    driver = build_driver(headless=headless)
    driver.set_page_load_timeout(60)

    all_hits: List[Slot] = []

    try:
        log("Opening booking page…")
        driver.get(base_url)

        accept_cookies_if_present(driver)
        login_if_needed(driver, username, password)
        open_facility(driver)
        select_duration(driver)

        target_dates = get_target_dates_two_weeks_ahead()

        for wd, date_obj in sorted(target_dates.items()):
            log(f"Checking {date_obj} (weekday {wd})…")
            go_to_month(driver, date_obj)
            pick_date(driver, date_obj)
            time.sleep(0.8)

            available = read_available_times(driver)
            targets = TARGET_WINDOWS.get(wd, [])
            hits = match_targets(available, targets)
            for (s, e) in hits:
                all_hits.append(Slot(date_obj, s, e))

        report_csv = "availability_report.csv"
        with open(report_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "start", "end"])
            for slot in sorted(all_hits, key=lambda s: (s.date, s.start)):
                writer.writerow(slot.as_row())

        wa_msg = compose_whatsapp_message(all_hits)
        msg_txt = "whatsapp_message.txt"
        with open(msg_txt, "w", encoding="utf-8") as f:
            f.write(wa_msg)

        log(f"Done. Matches: {len(all_hits)}")
        log(f"Saved CSV -> {report_csv}")
        log(f"Saved WhatsApp text -> {msg_txt}")
        print("\n====== WhatsAppに貼り付け ======\n" + wa_msg + "\n===============================\n")

    finally:
        try:
            with open("page_source.html", "w", encoding="utf-8", errors="ignore") as f:
                try:
                    f.write(driver.page_source)
                except Exception:
                    pass
            try:
                driver.save_screenshot("screenshot.png")
            except Exception:
                pass
        except Exception:
            pass
        driver.quit()

if __name__ == "__main__":
    main()
