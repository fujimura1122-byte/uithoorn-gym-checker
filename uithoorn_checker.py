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

# --- Page-specific helpers (from HTML structure) ---

def open_datepicker(driver):
    """Open the jQuery UI datepicker popup by clicking the calendar icon."""
    # id="calDiv" opens the popup; input has id="datepicker"
    try:
        safe_click(driver, By.ID, "calDiv", 10)
    except Exception:
        safe_click(driver, By.ID, "datepicker", 10)
    # wait for popup
    safe_find(driver, By.ID, "ui-datepicker-div", 10)


def select_duration(driver):
    """Select "1,5 uur" from the duration <select id='selectedTimeLength'>."""
    from selenium.webdriver.support.ui import Select
    sel = safe_find(driver, By.ID, "selectedTimeLength", 10)
    Select(sel).select_by_visible_text(DURATION_TEXT)
    time.sleep(0.3)


def go_to_month(driver, target: dt.date):
    """Navigate the jQuery UI datepicker popup to target month/year."""
    open_datepicker(driver)
    title = safe_find(driver, By.XPATH, "//div[@id='ui-datepicker-div']//div[contains(@class,'ui-datepicker-title')]", 10)

    def current_year_month():
        # Use the dropdowns inside the title
        month_sel = driver.find_element(By.XPATH, "//div[@id='ui-datepicker-div']//select[contains(@class,'ui-datepicker-month')]")
        year_sel = driver.find_element(By.XPATH, "//div[@id='ui-datepicker-div']//select[contains(@class,'ui-datepicker-year')]")
        return int(month_sel.get_attribute("value")) + 1, int(year_sel.get_attribute("value"))

    # Try up to 24 steps (2 years)
    for _ in range(24):
        cur_m, cur_y = current_year_month()
        if cur_m == target.month and cur_y == target.year:
            return
        # click next arrow
        safe_click(driver, By.XPATH, "//a[contains(@class,'ui-datepicker-next')]", 10)
        time.sleep(0.2)


def pick_date(driver, date_obj: dt.date):
    """Click day link in jQuery UI datepicker popup."""
    open_datepicker(driver)
    day = str(date_obj.day)
    # Click the day link (inside #ui-datepicker-div)
    link = safe_find(driver, By.XPATH, f"//div[@id='ui-datepicker-div']//a[normalize-space()='{day}']", 10)
    link.click()
    time.sleep(0.4)


def open_facility(driver):
    # Not needed on this page; keep for compatibility
    pass


def read_available_times(driver) -> List[Tuple[str, str]]:
    """Read options from <select id='customSelectedTimeSlot'> that look like "HH:MM - HH:MM"."""
    sel = safe_find(driver, By.ID, "customSelectedTimeSlot", 10)
    options = sel.find_elements(By.TAG_NAME, "option")
    results = []
    for op in options:
        txt = op.text.strip().replace('–', '-').replace('–', '-').replace('—','-')
        if ' - ' in txt and 'Geen tijden' not in txt:
            parts = [p.strip() for p in txt.split('-')]
            if len(parts) == 2:
                results.append((parts[0], parts[1]))
    return sorted(list(set(results)))


def match_targets(available: List[Tuple[str, str]], targets: List[Tuple[str, str]]):
    avail_set = {(a[0], a[1]) for a in available}
    return [(s, e) for (s, e) in targets if (s, e) in avail_set]


def compose_whatsapp_message(hits: List[Slot]) -> str:
    if not hits:
        return "今のところ2週間後の対象枠に空きはありません。
また後で確認します。"
    lines = ["【空き枠（2週間後）】"]
    wd_ja = ["月","火","水","木","金","土","日"]
    for slot in sorted(hits, key=lambda s: (s.date, s.start)):
        w = wd_ja[slot.date.weekday()]
        lines.append(f"{slot.date:%m/%d}（{w}） {slot.start}-{slot.end}")
    lines.append("参加できる方は返信ください！")
    return "
".join(lines)

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
        print("
====== WhatsAppに貼り付け ======
" + wa_msg + "
===============================
")

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
