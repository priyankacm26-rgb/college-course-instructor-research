
from dataclasses import dataclass
import requests
from bs4 import BeautifulSoup


@dataclass
class Page:
    url: str
    title: str
    text: str
    status: str


def fetch_page(url: str, timeout: int = 30, use_browser: bool = False) -> Page:
    if use_browser:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout * 1000
                )
                page.wait_for_timeout(1500)

                html = page.content()
                status = str(response.status) if response else "No HTTP response"
                title = page.title()
                browser.close()

        except Exception as exc:
            raise RuntimeError(f"Playwright fetch failed: {exc}")

    else:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "CollegeResearchAgent/1.0"}
        )
        response.raise_for_status()
        html = response.text
        status = str(response.status_code)

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    text = "\n".join(
        line.strip()
        for line in soup.get_text("\n").splitlines()
        if line.strip()
    )

    return Page(url=url, title=title, text=text, status=status)



def search_banner_classes(
    url: str,
    term: str,
    subject: str,
    course_number: str,
    timeout: int = 60
):
    import re
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

    BASE = "https://registration.uncw.edu/StudentRegistrationSsb"
    TERM_URL = BASE + "/ssb/term/termSelection?mode=search"

    TERM_IDS = {
        "fall 2026": "202710",
    }

    term_id = TERM_IDS.get(term.strip().lower())

    if not term_id:
        raise RuntimeError(
            f"Term ID is not configured for {term}. "
            "Currently configured: Fall 2026."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(
            viewport={"width": 1600, "height": 1000}
        )
        page.set_default_timeout(timeout * 1000)

        try:
            # STEP 1: Open actual term selection page.
            page.goto(
                TERM_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )
            page.wait_for_timeout(2000)

            # STEP 2: Open Select2 term dropdown.
            page.locator("#s2id_txt_term").click()

            box = page.locator(
                "#select2-drop input.select2-input"
            ).last

            box.wait_for(state="visible", timeout=10000)
            box.fill(term)

            option = page.locator(
                "#select2-drop .select2-result-label"
            ).filter(
                has_text=re.compile(
                    rf"^\s*{re.escape(term)}\s*$",
                    re.I
                )
            ).first

            option.wait_for(state="visible", timeout=15000)
            option.click()

            # STEP 3: Continue to Browse Classes.
            page.get_by_role(
                "button",
                name=re.compile("Continue", re.I)
            ).click()

            try:
                page.wait_for_url(
                    re.compile(r".*/classSearch/classSearch.*"),
                    timeout=30000
                )
            except PlaywrightTimeoutError:
                page.wait_for_timeout(4000)

            if "classSearch/classSearch" not in page.url:
                raise RuntimeError(
                    "Fall 2026 was selected, but Browse Classes did not open."
                )

            print("Fall 2026 selected successfully.")

            # STEP 4: Find the requested subject through UNCW endpoint.
            response = page.request.get(
                BASE + "/ssb/classSearch/get_subject",
                params={
                    "searchTerm": subject,
                    "term": term_id,
                    "offset": "1",
                    "max": "10"
                },
                timeout=20000
            )

            if not response.ok:
                raise RuntimeError(
                    f"Subject lookup failed: HTTP {response.status}"
                )

            subjects = response.json()

            subject_match = next(
                (
                    item for item in subjects
                    if str(item.get("code", "")).upper()
                    == subject.upper()
                ),
                None
            )

            if not subject_match:
                raise RuntimeError(
                    f"Subject {subject} was not found."
                )

            # Set the hidden subject field as in the working script.
            page.locator("#txt_subject").evaluate(
                """(element, code) => {
                    element.value = code;

                    if (window.jQuery) {
                        window.jQuery(element)
                            .trigger('input')
                            .trigger('change');
                    } else {
                        element.dispatchEvent(
                            new Event('input', {bubbles: true})
                        );
                        element.dispatchEvent(
                            new Event('change', {bubbles: true})
                        );
                    }
                }""",
                subject
            )

            selected = page.locator(
                "#txt_subject"
            ).input_value()

            if selected.upper() != subject.upper():
                raise RuntimeError(
                    f"Subject selection failed: {selected}"
                )

            # STEP 5: Enter course number.
            page.locator(
                "#txt_courseNumber"
            ).fill(str(course_number))

            # STEP 6: Search.
            page.locator("#search-go").click()

            page.locator(
                "#searchResultsTable"
            ).wait_for(
                state="visible",
                timeout=30000
            )

            page.wait_for_function(
                """() => {
                    const el = document.querySelector(
                        '#searchResultsTable'
                    );
                    return el && el.innerText.trim().length > 30;
                }""",
                timeout=30000
            )

            page.wait_for_timeout(1500)

            # STEP 7: Attempt to show up to 50 results.
            try:
                selects = page.locator(
                    "#searchResults select, #searchResultsTable select"
                )

                for i in range(selects.count()):
                    select = selects.nth(i)
                    options = select.locator("option")

                    for j in range(options.count()):
                        opt = options.nth(j)

                        if opt.inner_text().strip() == "50":
                            value = opt.get_attribute("value")
                            if value is not None:
                                select.select_option(value)
                                page.wait_for_timeout(2000)
                                break
            except Exception:
                pass

            # STEP 8: Capture rendered results.
            title = page.title()
            rendered_text = page.locator(
                "#searchResultsTable"
            ).inner_text()

            tables = page.locator("table")
            table_text = []

            for i in range(tables.count()):
                try:
                    content = tables.nth(i).inner_text()
                    if content.strip():
                        table_text.append(content)
                except Exception:
                    pass

            print("UNCW course search completed.")
            print(rendered_text[:10000])

            return {
                "url": page.url,
                "title": title,
                "status": str(response.status),
                "text": rendered_text,
                "tables": table_text
            }

        finally:
            browser.close()

