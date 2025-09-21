"""
Uithoorn Gym Availability Checker
---------------------------------

What it does
- Opens the Uithoorn booking page for "Brede School Legmeer / Gymzaal A"
- Selects duration "1,5 uur"
- Jumps to the date exactly 14 days from today (2 weeks ahead)
- Checks availability for these target weekday/time windows:
    Mon 20:00–21:30
    Thu 20:00–21:30
    Sat 17:00–18:30
    Sun 14:00–15:30 and 15:30–17:00
- Prints a WhatsApp-ready summary message and saves a CSV report

Notes
- If login is required on your account, fill USERNAME and PASSWORD in .env and the login() function will run.
- Designed for Windows + Chrome with Selenium.
- Robust selectors use visible text; minor UI changes may require updating XPaths.

Setup
1) Install Python packages:
   pip install selenium python-dotenv webdriver-manager

2) Create a .env file in the same folder with (edit as needed):
   BASE_URL=https://avo.hta.nl/uithoorn/Accommodation/Book/106
   USERNAME=
   PASSWORD=
   HEADLESS=true

3) Run:
   python uithoorn_checker.py

4) Optional: Schedule with Windows Task Scheduler for automatic runs every day at 09:00.

"""
import os
import time
import csv
import sys
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

DURATION_TEXT = "1,5 uur"  # Hoe lang wilt u reserveren?
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
    """Return a mapping from weekday -> date for the week that is 14 days from today.
    We pick the Monday of the "+2 weeks" week, then compute the specific weekdays.
    """
    if today is None:
        today = dt.date.today()
    future = today + dt.timedelta(days=14)
    # Find that week's Monday
    monday = future - dt.timedelta(days=future.weekday())
    mapping = {}
    for wd in TARGET_WINDOWS.keys():
        mapping[wd] = monday + dt.timedelta(days=wd)
    return mapping


# ----------------------- Selenium core -----------------------

def build_driver(headless: bool) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1400,900")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


def safe_click(driver, by, selector, timeout=20):
    el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, selector)))
    el.click()
    return el


def safe_find(driver, by, selector, timeout=20):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, selector)))


def safe_find_all(driver, by, selector, timeout=20):
    WebDriverWait(driver, timeout).until(EC.presence_of_all_elements_located((by, selector)))
    return driver.find_elements(by, selector)


def login_if_needed(driver, username: str, password: str):
    if not username:
        return  # Skip if no creds configured
    try:
        # Example selectors — update if your login flow differs
        # Look for a login link/button by common text
        candidates = driver.find_elements(By.XPATH, "//a[normalize-space()='Login' or normalize-space()='Inloggen'] | //button[normalize-space()='Login' or normalize-space()='Inloggen']")
        if candidates:
            candidates[0].click()
            safe_find(driver, By.NAME, "username", 15).send_keys(username)
            safe_find(driver, By.NAME, "password", 15).send_keys(password)
            safe_click(driver, By.XPATH, "//button[@type='submit' or .='Login' or .='Inloggen']", 15)
            # Wait for post-login state
            time.sleep(2)
    except Exception as e:
        log(f"Login step skipped or not required. Detail: {e}")


def select_duration(driver):
    # Click the duration dropdown labeled: "Hoe lang wilt u reserveren?"
    safe_click(driver, By.XPATH, "//label[contains(., 'Hoe lang wilt u reserveren?')]/following::*[self::select or self::button][1]")
    # If it's a <select>
    try:
        select = driver.find_element(By.XPATH, "//label[contains(., 'Hoe lang wilt u reserveren?')]/following::select[1]")
        from selenium.webdriver.support.ui import Select
        Select(select).select_by_visible_text(DURATION_TEXT)
        return
    except:
        pass
    # If it's a custom list/menu
    try:
        safe_click(driver, By.XPATH, f"//*[self::li or self::div or self::button][normalize-space()='{DURATION_TEXT}']")
    except:
        # Fallback: click any element containing the text
        safe_click(driver, By.XPATH, f"//*[contains(normalize-space(), '{DURATION_TEXT}')]")


def open_facility(driver):
    # Click the facility card if needed (Binnensport -> Gymzaal A -> Boeken)
    try:
        safe_click(driver, By.XPATH, f"//*[contains(., '{FACILITY_TEXT}') and (self::a or self::button or self::div)]")
        time.sleep(1)
    except Exception as e:
        log(f"Facility click may not be required or element not found. Detail: {e}")


def go_to_month(driver, target: dt.date):
    """Navigate the calendar UI to the month/year that contains target date."""
    # Try to read current month label, then click next until matched
    # Labels often like "oktober 2025"; we compare month+year.
    def month_key(text: str) -> Tuple[int, int]:
        text = text.strip().lower()
        months = {
            'januari':1, 'februari':2, 'maart':3, 'april':4, 'mei':5, 'juni':6,
            'juli':7, 'augustus':8, 'september':9, 'oktober':10, 'november':11, 'december':12
        }
        for name, idx in months.items():
            if name in text:
                # extract year digits
                import re
                m = re.search(r"(20\d{2})", text)
                year = int(m.group(1)) if m else dt.date.today().year
                return idx, year
        # Fallback: try numeric MM-YYYY in text
        import re
        m = re.search(r"(\d{1,2})[\-/ ](\d{4})", text)
        if m:
            return int(m.group(1)), int(m.group(2))
        # Unknown label
        return -1, -1

    # Find the header label and next button
    header = safe_find(driver, By.XPATH, "//*[self::h2 or self::div][contains(@class,'calendar') or contains(@class,'month')][1] | //*[contains(@class,'datepicker') and (self::div or self::span)]//following::*[self::div or self::span][1]", 10)
    next_btn = driver.find_elements(By.XPATH, "//button[contains(@aria-label,'Next') or contains(@aria-label,'Volgende') or contains(.,'>') or contains(.,'›') or contains(.,'Volgend')] | //*[contains(@class,'next')][self::a or self::button]")
    target_key = (target.month, target.year)

    # Try up to 12 months
    for _ in range(12):
        label_text = header.text
        cur_key = month_key(label_text)
        if cur_key == target_key:
            return
        if next_btn:
            next_btn[0].click()
            time.sleep(0.5)
        else:
            # Try alternate next arrow
            safe_click(driver, By.XPATH, "//*[self::button or self::a][contains(.,'Volgende') or contains(.,'Next') or contains(.,'›') or contains(.,'>')]", 10)
            time.sleep(0.5)


def pick_date(driver, date_obj: dt.date):
    # Click the day cell matching date_obj.day
    day_str = str(date_obj.day)
    # Avoid disabled/outside-month cells
    candidates = driver.find_elements(By.XPATH, f"//td[not(contains(@class,'disabled')) and .//*[normalize-space()='{day_str}']] | //*[self::button or self::a][@role='gridcell' or @role='button'][normalize-space()='{day_str}']")
    if not candidates:
        # Fallback: any element with day text
        candidates = driver.find_elements(By.XPATH, f"//*[normalize-space()='{day_str}']")
    if candidates:
        candidates[0].click()
        time.sleep(0.5)
    else:
        raise RuntimeError(f"Could not click date {date_obj}")


def read_available_times(driver) -> List[Tuple[str, str]]:
    """Return list of (start,end) strings visible under the "Welke tijd" section."""
    # Look for time chips around a label "Welke tijd"
    container = safe_find(driver, By.XPATH, "//*[contains(.,'Welke tijd')]/following::*[self::div or self::ul][1]", 10)
    # Find all time ranges like "20:00 - 21:30"
    items = container.find_elements(By.XPATH, ".//*[contains(text(),':') and (contains(text(),'–') or contains(text(),'-'))]")
    results = []
    for el in items:
        txt = el.text.strip().replace('\u2013', '-').replace('–', '-').replace('—','-')
        if '-' in txt:
            parts = [p.strip() for p in txt.split('-')]
            if len(parts) == 2:
                results.append((parts[0], parts[1]))
    # Deduplicate
    results = sorted(list(set(results)))
    return results


def match_targets(available: List[Tuple[str, str]], targets: List[Tuple[str, str]]):
    avail_set = {(a[0], a[1]) for a in available}
    hits = [(s, e) for (s, e) in targets if (s, e) in avail_set]
    return hits


def compose_whatsapp_message(hits: List[Slot]) -> str:
    if not hits:
        return "今のところ2週間後の対象枠に空きはありません。\nまた後で確認します。"
    lines = ["【空き枠（2週間後）】"]
    # Group by weekday name (ja)
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

        # Login if your account requires it
        login_if_needed(driver, username, password)

        # Some pages require clicking the facility card first
        open_facility(driver)

        # Set duration
        select_duration(driver)

        # Determine target dates two weeks ahead
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

        # Output
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
        driver.quit()


if __name__ == "__main__":
    main()


---

## ✨ GitHub Actions（毎週金曜に自動実行＆メール送付）
以下を**そのまま**リポジトリに追加すれば、毎週金曜にクラウドで実行し、結果ファイル（CSV／WhatsApp文面）をメール送付できます。ローカルPCは不要です。

### 1) リポジトリ構成（例）
```
.
├─ uithoorn_checker.py          # 既存スクリプト（このキャンバスのコード）
├─ requirements.txt             # 依存関係
└─ .github/
   └─ workflows/
      └─ uithoorn-check.yml     # 自動実行ワークフロー
```

### 2) requirements.txt（新規作成）
```
selenium
python-dotenv
webdriver-manager
```

### 3) .github/workflows/uithoorn-check.yml（新規作成）
> **メール送付は GitHub Action のSMTP送信アクションを使用**します（スクリプト側の改修不要）。

```yaml
name: Uithoorn Checker (Weekly)

on:
  schedule:
    # 毎週金曜 07:30 UTC に実行（アムステルダム時間に合わせる場合は季節で調整）
    - cron: '30 7 * * 5'
  workflow_dispatch: {}

jobs:
  run-checker:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Setup Chrome
        uses: browser-actions/setup-chrome@v1
        with:
          chrome-version: stable

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run checker (headless)
        env:
          BASE_URL: https://avo.hta.nl/uithoorn/Accommodation/Book/106
          HEADLESS: 'true'
        run: |
          python uithoorn_checker.py

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: reports
          path: |
            availability_report.csv
            whatsapp_message.txt

      - name: Send result email
        uses: dawidd6/action-send-mail@v3
        with:
          server_address: ${{ secrets.MAIL_HOST }}
          server_port: ${{ secrets.MAIL_PORT }}
          username: ${{ secrets.MAIL_USERNAME }}
          password: ${{ secrets.MAIL_PASSWORD }}
          subject: "Uithoorn 空枠チェック結果"
          from: ${{ secrets.MAIL_FROM }}
          to: ${{ secrets.MAIL_TO }}
          body: |
            週次レポートです（自動送信）。

            ・whatsapp_message.txt をそのままグループに貼り付けできます。
            ・availability_report.csv は詳細一覧です。

            実行時刻（UTC）: ${{ github.event.schedule }} / ${{ github.run_id }}
          attachments: |
            availability_report.csv
            whatsapp_message.txt
```

> **補足**: Chrome は headless で実行します。`webdriver-manager` が実行時に適合する ChromeDriver を取得します。

### 4) リポジトリ・シークレットの設定（Settings → Secrets and variables → Actions → New repository secret）
以下を作成してください：
- `MAIL_HOST`：SMTPサーバ（例：smtp.gmail.com）
- `MAIL_PORT`：SMTPポート（TLSなら587）
- `MAIL_USERNAME`：SMTPユーザー（送信アカウント）
- `MAIL_PASSWORD`：アプリパスワード等
- `MAIL_FROM`：送信者メールアドレス
- `MAIL_TO`：送信先メールアドレス（カンマ区切りで複数可）

> **Gmail使用時の注意**：2段階認証＋「アプリ パスワード」を作成し、それを`MAIL_PASSWORD`に設定してください。

### 5) 実行タイミング（時刻の調整）
- GitHub Actions の `cron` は **UTC** 基準です。アムステルダム（Europe/Amsterdam）で金曜9:30に受け取りたい場合は、夏時間(CET+2)で `7:30 UTC`、冬時間(CET+1)で `8:30 UTC` に相当します。季節で時刻がずれます。固定時刻に合わせたい場合は、時間を余裕側に寄せるか、`workflow_dispatch`で手動実行を併用してください。

### 6) 動作確認（初回手動トリガー）
- `Actions` タブ → `Uithoorn Checker (Weekly)` → `Run workflow` で手動実行 → 成功後、受信メールと `reports` アーティファクトに `availability_report.csv` / `whatsapp_message.txt` が生成されることを確認してください。

---

## 参考：スクリプト側でメール送信したい場合（任意）
> （通常は上記のSMTPアクションで十分なので不要です）
- `smtplib` と `email.message.EmailMessage` を使って、環境変数から `MAIL_*` を読み、同ファイルを添付して送る処理を `uithoorn_checker.py` の最後に追加すればOKです。
- ただし GitHub Actions の方がログや失敗時の再実行が簡単なので、**推奨はアクションでの送信**です。
