"""
Universal Salesforce Classic Printable View -> PDF -> Excel pipeline.

Run locally on Windows:
    python salesforce_universal_pipeline.py

Authentication is performed by the user in the visible Edge window. MFA is never
automated or bypassed. The Edge profile is persistent so an approved session can
be reused when Salesforce permits it.
"""
import base64
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import pymupdf as fitz  # PyMuPDF
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
PROFILE_DIR = BASE_DIR / "salesforce_edge_profile"
SALESFORCE_HOME = "https://zybooks.my.salesforce.com/home/home.jsp"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def safe_filename(value):
    value = re.sub(r"[^A-Za-z0-9._ -]+", "", value).strip()
    value = re.sub(r"\s+", "_", value)
    return value[:100] or "Salesforce_Account"


def norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def is_printable_view(page):
    """Return True when the current tab already shows Salesforce Printable View."""
    try:
        url = (page.url or "").casefold()
        title = (page.title() or "").casefold()
        body = page.locator("body").inner_text(timeout=5000).casefold()
        markers = ("printable view", "account details", "courses", "contacts")
        return (
            "print" in url
            or "printable" in title
            or ("print" in title and any(x in body for x in markers))
            or ("account details" in body and "courses" in body)
        )
    except Exception:
        return False


def normalize_salesforce_name(value):
    """Normalize punctuation so comma/hyphen variants compare equally."""
    value = re.sub(r"[\s,;:_-]+", " ", str(value or "")).casefold()
    return re.sub(r"[^a-z0-9 ]", "", value).strip()


def is_account_record(page, college_name):
    """Best-effort check for Classic or Lightning Account record pages."""
    try:
        url = page.url or ""
        # Salesforce Classic record ID or Lightning Account record route.
        if re.search(r"/001[a-zA-Z0-9]{10,}(?:/|$)", url):
            return True
        if re.search(r"/lightning/r/Account/[a-zA-Z0-9]{15,}/view", url, re.I):
            return True

        title = page.title()
        body = page.locator("body").inner_text(timeout=5000)[:7000]
        expected = normalize_salesforce_name(college_name)
        visible = normalize_salesforce_name(title + "\n" + body)
        return expected and expected in visible and (
            "account" in visible or "courses" in visible or "contacts" in visible
        )
    except Exception:
        return False


def find_account_tab(context, college_name):
    """Return the Edge tab showing the requested Account, including newly opened tabs."""
    for candidate_page in reversed(context.pages):
        if is_account_record(candidate_page, college_name) or is_printable_view(candidate_page):
            return candidate_page
    return None


def wait_for_user_account(page, college_name):
    """Wait for user authentication, then search or allow manual account selection."""
    page.goto(SALESFORCE_HOME, wait_until="domcontentloaded", timeout=60000)
    print("\nSalesforce is open in Microsoft Edge.")
    print("Complete your Salesforce login and MFA in Edge.")
    # Wait for authentication/MFA and the Salesforce interface without a console prompt.
    print("Waiting for Salesforce login/MFA to complete (up to 3 minutes)...")
    search_selectors = [
        'input[name="srch"]',
        'input[id*="phSearchInput"]',
        'input[placeholder*="Search"]',
        'input[title*="Search"]',
    ]
    ready = False
    for _ in range(180):
        if is_printable_view(page) or is_account_record(page, college_name):
            ready = True
            break
        for selector in search_selectors:
            try:
                candidate = page.locator(selector).first
                if candidate.count() and candidate.is_visible():
                    ready = True
                    break
            except Exception:
                continue
        if ready:
            break
        page.wait_for_timeout(1000)

    if not ready:
        raise RuntimeError(
            "Salesforce was not detected as ready within 3 minutes. "
            "Complete login/MFA in Edge and run the export again."
        )
    print("Salesforce is ready. Continuing automatically.")
    page.wait_for_timeout(1500)

    # If the user has already navigated to the requested Account or Printable View,
    # do not send them back to the Salesforce home page.
    if is_printable_view(page) or is_account_record(page, college_name):
        print("A Salesforce account/Printable View page is already open.")
        return page

    search_box = None
    for selector in search_selectors:
        try:
            candidate = page.locator(selector).first
            if candidate.count() and candidate.is_visible():
                search_box = candidate
                break
        except Exception:
            continue

    if search_box:
        try:
            search_box.fill(college_name, timeout=10000)
            search_box.press("Enter", timeout=10000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"Automatic Salesforce search did not complete: {exc}")
    else:
        print("Salesforce search field was not detected after login.")

    # Salesforce may display "California State University-Chico" even when
    # the search term is "California State University, Chico". Normalize
    # punctuation and whitespace, then inspect visible Account result links.
    def comparable_account_name(value):
        value = re.sub(r"[\s,;:_-]+", " ", str(value or "")).casefold()
        return re.sub(r"[^a-z0-9 ]", "", value).strip()

    requested_name = comparable_account_name(college_name)
    try:
        account_links = page.locator("a")
        for link_index in range(min(account_links.count(), 250)):
            candidate = account_links.nth(link_index)
            try:
                if not candidate.is_visible():
                    continue
                link_text = candidate.inner_text(timeout=1500).strip()
                if comparable_account_name(link_text) != requested_name:
                    continue
                href = candidate.get_attribute("href") or ""
                if "/001" not in href and "Account" not in href:
                    continue
                candidate.click(timeout=10000)
                page.wait_for_load_state("domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                if is_account_record(page, college_name):
                    print(f"Account selected automatically: {page.url}")
                    return page
            except Exception:
                continue
    except Exception as exc:
        print(f"Automatic account selection was not confirmed: {exc}")

    selected_page = find_account_tab(page.context, college_name)
    if selected_page:
        print(f"Account page detected in another Edge tab: {selected_page.url}")
        return selected_page

    print("\nAutomatic account selection was not confirmed.")
    print(f"Please select the correct Account in Microsoft Edge: {college_name}")
    print("Waiting up to 3 minutes for the Account page to open...")

    # Manual selection may open a separate tab; poll every open Edge tab.
    selected_page = None
    for _ in range(180):
        selected_page = find_account_tab(page.context, college_name)
        if selected_page:
            break
        page.wait_for_timeout(1000)

    if selected_page:
        print(f"Account page detected in Edge: {selected_page.url}")
        return selected_page

    open_tabs = []
    for tab_index, candidate_page in enumerate(page.context.pages, start=1):
        try:
            open_tabs.append(f"Tab {tab_index}: {candidate_page.url} | {candidate_page.title()}")
        except Exception:
            open_tabs.append(f"Tab {tab_index}: URL/title unavailable")

    raise RuntimeError(
        "The requested Account was not detected in any open Edge tab. "
        "Make sure you clicked the Account Name link in the Accounts section, "
        "not a Contact or Lead. Open tabs: " + " ; ".join(open_tabs)
    )


def open_printable_view(page, context):
    """Use an already-open Printable View, otherwise open it from the Account."""
    if is_printable_view(page):
        print("Using the Printable View that is already open.")
        return page

    link = page.get_by_role("link", name=re.compile(r"Printable View", re.I))
    if not link.count():
        link = page.locator('a:has-text("Printable View")')

    if not link.count():
        print("\nPrintable View link is not visible yet. Waiting for it to appear...")
        for _ in range(90):
            for candidate_page in reversed(context.pages):
                if is_printable_view(candidate_page):
                    print("Printable View detected automatically.")
                    return candidate_page
            try:
                link = page.get_by_role("link", name=re.compile(r"Printable View", re.I))
                if not link.count():
                    link = page.locator('a:has-text("Printable View")')
                if link.count():
                    break
            except Exception:
                pass
            page.wait_for_timeout(1000)

        if not link.count():
            raise RuntimeError(
                "Printable View link was not detected within 90 seconds. "
                "Check that the correct Salesforce Account page is open."
            )

    href = link.first.get_attribute("href")
    printable_url = urljoin(page.url, href) if href else None

    try:
        with page.expect_popup(timeout=5000) as popup_info:
            link.first.click(timeout=10000)
        printable_page = popup_info.value
    except PlaywrightTimeoutError:
        try:
            link.first.click(timeout=10000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            if not printable_url:
                raise
            page.goto(printable_url, wait_until="domcontentloaded", timeout=60000)
        printable_page = page

    printable_page.wait_for_load_state("domcontentloaded", timeout=30000)
    printable_page.wait_for_timeout(1500)
    return printable_page


def save_pdf_from_page(page, pdf_path):
    """Print the authenticated page to PDF through Chromium DevTools."""
    session = page.context.new_cdp_session(page)
    result = session.send("Page.printToPDF", {
        "printBackground": True,
        "preferCSSPageSize": True,
        "landscape": False,
        "displayHeaderFooter": False,
    })
    pdf_path.write_bytes(base64.b64decode(result["data"]))


def page_snapshot(page):
    return page.evaluate("""() => {
      const tidy = s => (s || '').replace(/\\s+/g, ' ').trim();
      const tables = Array.from(document.querySelectorAll('table')).map((table, i) => ({
        table_index: i,
        rows: Array.from(table.querySelectorAll('tr')).map(row =>
          Array.from(row.querySelectorAll('th,td')).map(cell => tidy(cell.innerText))
        ).filter(row => row.some(Boolean))
      })).filter(t => t.rows.length);

      const quickLabels = new Set([
        'Academic Calendar', 'Bookstore Information', 'Course Catalog',
        'Faculty Directory', 'Class Schedule', 'School Link - Coursicle'
      ]);
      const quickLinks = [];
      for (const row of document.querySelectorAll('tr')) {
        const cells = Array.from(row.querySelectorAll('th,td'));
        // Salesforce lays out Quick Links as label/value pairs, sometimes
        // with two pairs in the same row. Read only the next cell per label.
        for (let i = 0; i < cells.length - 1; i++) {
          const label = tidy(cells[i].innerText);
          if (!quickLabels.has(label)) continue;
          const valueCell = cells[i + 1];
          const anchor = valueCell.querySelector('a');
          const href = anchor ? (anchor.href || anchor.getAttribute('href') || '') : '';
          const value = anchor ? (href || tidy(anchor.innerText)) : tidy(valueCell.innerText);
          if (value) quickLinks.push({ label, value, href });
        }
      }
      return {
        title: document.title,
        url: location.href,
        text: document.body ? document.body.innerText : '',
        tables,
        quick_links: quickLinks
      };
    }""")


def read_pdf(pdf_path):
    with fitz.open(pdf_path) as doc:
        pages = [p.get_text("text") for p in doc]
        return "\n".join(pages), pages


def find_section(text, heading, next_headings):
    """Find section headings as standalone lines, case-insensitively."""
    lines = text.splitlines()
    start = None
    heading_re = re.compile(rf"^\s*{re.escape(heading)}\s*(?:\[\d+\+\])?\s*$", re.I)
    for i, line in enumerate(lines):
        if heading_re.match(line):
            start = i + 1
            break
    if start is None:
        return ""
    stop = len(lines)
    for i in range(start, len(lines)):
        if any(re.match(rf"^\s*{re.escape(h)}\s*(?:\[\d+\+\])?\s*$", lines[i], re.I)
                   for h in next_headings):
            stop = i
            break
    return "\n".join(lines[start:stop])


def course_heading_pattern():
    # Common US course subject/number/title formats; deliberately broader than one college.
    return re.compile(
        r"(?im)^\s*((?:DEPRECATED\s*-\s*)?[A-Z][A-Z0-9/&]{1,11}"
        r"(?:\s+[A-Z]{1,5})?\s+\d{2,4}[A-Z]?"
        r"(?:\s*[/.-]\s*[A-Z0-9]+)?\s*[-:]\s*.+?)\s*$"
    )


def parse_courses(section_text):
    matches = list(course_heading_pattern().finditer(section_text))
    rows = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section_text)
        block = [re.sub(r"\s+", " ", x).strip() for x in
                 section_text[match.end():end].splitlines() if x.strip()]
        row = {"Course": re.sub(r"\s+", " ", match.group(1)).strip()}
        # Preserve all labelled course fields rather than assuming one fixed schema.
        for line in block:
            if ":" in line:
                label, value = line.split(":", 1)
                label = label.strip()
                if 2 <= len(label) <= 80 and label not in row:
                    row[label] = value.strip()
            else:
                # Salesforce printable view often puts a label on one line and value next.
                if line in {
                    "Course Status - Spring", "Course Status - Winter",
                    "Course Status - Fall", "1st Applicable zyBook",
                    "Total Yearly Enrollment", "Competitors in Use 1st",
                    "Total Fall Enrollment", "Total Spring Enrollment",
                    "Total Summer Enrollment",
                }:
                    pos = block.index(line)
                    row[line] = block[pos + 1] if pos + 1 < len(block) else ""
        rows.append(row)
    return rows


def parse_label_value_records(section_text, record_heading, known_labels):
    """Parse repeated Salesforce related-list records using visible label/value pairs."""
    raw_lines = [re.sub(r"\s+", " ", x).strip()
                 for x in section_text.splitlines() if x.strip()]
    if not raw_lines:
        return []
    labels = sorted(known_labels, key=len, reverse=True)
    starts = []
    for i, line in enumerate(raw_lines):
        if i + 1 < len(raw_lines) and any(raw_lines[i + 1].startswith(x) for x in labels):
            starts.append(i)
    # If a section has a Salesforce table header, skip it.
    rows = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(raw_lines)
        block = raw_lines[start:end]
        record = {record_heading: block[0]}
        for label in labels:
            value = ""
            for j, line in enumerate(block[1:], 1):
                if line.startswith(label):
                    value = line[len(label):].strip()
                    if not value and j + 1 < len(block):
                        value = block[j + 1]
                    break
            record[label.strip()] = value
        if any(v for k, v in record.items() if k != record_heading):
            rows.append(record)
    return rows


def table_rows_for_section(snapshot, heading_keywords):
    """Capture visible tables as a safe fallback when text record parsing is weak."""
    result = []
    for table in snapshot.get("tables", []):
        rows = table.get("rows", [])
        joined = " ".join(" ".join(r) for r in rows[:3]).casefold()
        if any(k.casefold() in joined for k in heading_keywords):
            result.extend(rows)
    return result


def add_records_sheet(wb, title, rows, fallback_rows=None):
    ws = wb.create_sheet(title)
    if rows:
        headers = []
        for row in rows:
            for key in row:
                if key not in headers:
                    headers.append(key)
        ws.append(headers)
        for row in rows:
            ws.append([row.get(h, "") for h in headers])
    elif fallback_rows:
        width = max(len(r) for r in fallback_rows)
        for row in fallback_rows:
            ws.append(row + [""] * (width - len(row)))
    else:
        ws.append(["No records confidently detected"])
    return ws


def style_workbook(wb):
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[1].height = 30
        for col in ws.columns:
            width = min(max(max(len(str(c.value or "")) for c in col) + 2, 12), 55)
            ws.column_dimensions[get_column_letter(col[0].column)].width = width
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)



def parse_quick_links(snapshot=None, pdf_text=""):
    """Extract Quick Links captured from the Salesforce Printable View page."""
    labels = [
        "Academic Calendar", "Bookstore Information", "Course Catalog",
        "Faculty Directory", "Class Schedule", "School Link - Coursicle",
    ]
    snapshot = snapshot or {}
    captured = snapshot.get("quick_links", [])
    results = []
    seen = set()

    for item in captured:
        label = re.sub(r"\s+", " ", str(item.get("label", ""))).strip()
        value = re.sub(r"\s+", " ", str(item.get("value", ""))).strip()
        href = str(item.get("href", "") or "").strip()
        if label not in labels:
            continue
        if href.startswith(("http://", "https://")):
            value = href
        if not value:
            continue
        key = label
        if key not in seen:
            seen.add(key)
            results.append({"Quick Link": label, "URL / Value": value})

    # Fallback to table text captured in the same live page snapshot.
    if not results:
        for table in snapshot.get("tables", []):
            for row in table.get("rows", []):
                cells = [re.sub(r"\s+", " ", str(cell or "")).strip() for cell in row]
                for index, cell in enumerate(cells[:-1]):
                    if cell in labels and cells[index + 1]:
                        value = cells[index + 1]
                        key = (cell, value)
                        if key not in seen:
                            seen.add(key)
                            results.append({"Quick Link": cell, "URL / Value": value})

    return results

def convert_pdf_to_excel(pdf_path, college_name, snapshot=None):
    text, pages = read_pdf(pdf_path)
    headings = ["Contacts", "Opportunities", "Account History", "Class zyBooks"]
    courses_sec = find_section(text, "Courses", headings)
    contacts_sec = find_section(text, "Contacts", ["Opportunities", "Account History", "Class zyBooks"])
    opp_sec = find_section(text, "Opportunities", ["Account History", "Class zyBooks"])
    zy_sec = find_section(text, "Class zyBooks", [])

    courses = parse_courses(courses_sec)
    contacts = parse_label_value_records(
        contacts_sec, "Contact Name",
        ["Title", "Department", "Strategic Contact", "Phone", "Email",
         "Last Activity", "Last Name", "Mobile", "Description", "First Name"]
    )
    opportunities = parse_label_value_records(
        opp_sec, "Opportunity",
        ["Stage", "Enrollment", "Paid Student Registrations", "Amount",
         "Actual Revenue", "Close Date", "Probability (%)", "Reason Lost",
         "Owner Full Name", "Expected Revenue", "Lead Source", "Fiscal Period",
         "Last Modified Date", "Name", "Type"]
    )
    zybooks = parse_label_value_records(
        zy_sec, "Class zyBook Record",
        ["Course", "Subjects", "Total Revenue", "Subscribers",
         "Term (for Report)", "Created Date", "Name", "Status"]
    )

    quick_links = parse_quick_links(snapshot, text)

    # Retain page/table text for auditing, especially when an unfamiliar layout is encountered.
    wb = Workbook()
    summary = wb.active
    summary.title = "Account Summary"
    summary.append(["Field", "Value"])
    summary.append(["College", college_name])
    summary.append(["Source PDF", pdf_path.name])
    summary.append(["PDF pages", len(pages)])
    summary.append(["Courses detected", len(courses)])
    summary.append(["Contacts detected", len(contacts)])
    summary.append(["Opportunities detected", len(opportunities)])
    summary.append(["Class zyBooks detected", len(zybooks)])
    summary.append(["Quick Links detected", len(quick_links)])
    summary.append(["Extraction note", "Counts are parser detections, not independently verified totals. Review each sheet against the PDF."])
    summary.append(["PDF text length", len(text)])

    add_records_sheet(wb, "Courses", courses,
                      table_rows_for_section(snapshot or {}, ["Course", "Course Status"]))
    add_records_sheet(wb, "Contacts", contacts,
                      table_rows_for_section(snapshot or {}, ["Title", "Email", "Phone"]))
    add_records_sheet(wb, "Opportunities", opportunities,
                      table_rows_for_section(snapshot or {}, ["Stage", "Amount", "Close Date"]))
    add_records_sheet(wb, "Class zyBooks", zybooks,
                      table_rows_for_section(snapshot or {}, ["Subscribers", "Total Revenue"]))
    quick_ws = add_records_sheet(wb, "Quick Links", quick_links)
    raw = wb.create_sheet("PDF Text")
    raw.append(["Page", "Line"])
    for page_no, page_text in enumerate(pages, 1):
        for line in page_text.splitlines():
            if line.strip():
                raw.append([page_no, line.strip()])

    # Preserve clickable destinations in the Quick Links worksheet.
    for row_index in range(2, quick_ws.max_row + 1):
        value_cell = quick_ws.cell(row=row_index, column=2)
        value = str(value_cell.value or "").strip()
        if value.startswith(("http://", "https://")):
            value_cell.hyperlink = value
            value_cell.style = "Hyperlink"

    style_workbook(wb)
    output = pdf_path.with_name(pdf_path.stem + "_converted.xlsx")
    wb.save(output)
    return output, {
        "courses": len(courses), "contacts": len(contacts),
        "opportunities": len(opportunities), "class_zybooks": len(zybooks),
        "quick_links": len(quick_links), "pages": len(pages),
    }


def run_export(college_name):
    safe = safe_filename(college_name)
    pdf_path = OUTPUT_DIR / f"{safe}_Printable_View.pdf"
    json_path = OUTPUT_DIR / f"{safe}_Printable_View.json"
    html_path = OUTPUT_DIR / f"{safe}_Printable_View.html"
    screenshot_path = OUTPUT_DIR / f"{safe}_Account.png"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="msedge",
            headless=False,
            viewport={"width": 1440, "height": 1000},
            args=["--start-maximized"],
        )
        export_completed = False
        try:
            page = context.pages[0] if context.pages else context.new_page()
            account_page = wait_for_user_account(page, college_name)
            print(f"Account page selected: {account_page.url}")

            printable_page = open_printable_view(account_page, context)
            snapshot = page_snapshot(printable_page)

            if len(snapshot.get("text", "").strip()) < 100:
                raise RuntimeError(
                    "Printable View opened but contains too little text to export. "
                    "Check that the correct account's Printable View is displayed."
                )

            save_pdf_from_page(printable_page, pdf_path)
            json_path.write_text(
                json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            html_path.write_text(printable_page.content(), encoding="utf-8")
            printable_page.screenshot(path=str(screenshot_path), full_page=True)

            print(f"PDF saved: {pdf_path}")
            print(f"Printable page text characters: {len(snapshot['text'])}")
            print(f"HTML tables captured: {len(snapshot.get('tables', []))}")

            print("PDF captured. Converting automatically to Excel...")
            xlsx, counts = convert_pdf_to_excel(pdf_path, college_name, snapshot)
            print(f"\\nExcel saved: {xlsx}")
            print("Detected counts:", counts)
            print("PDF and Excel saved locally. MySQL is not used.")

            print("Review the workbook, especially sections with zero detected records.")
            export_completed = True
            return xlsx, counts

        except Exception as exc:
            print(f"\\nEXPORT STOPPED: {type(exc).__name__}: {exc}")
            print("Closing the automation Edge session after the error.")
            context.close()
            raise
        finally:
            if export_completed:
                context.close()


if __name__ == "__main__":
    college = " ".join(sys.argv[1:]).strip()
    if not college:
        raise SystemExit(
            "College name is required. Launch this script from Streamlit "
            "or pass the college name as an argument."
        )
    run_export(college)
