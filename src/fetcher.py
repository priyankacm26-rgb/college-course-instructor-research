
from dataclasses import dataclass
from urllib.parse import urlparse
import re
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




def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _visible_selects(page):
    selects = page.locator("select:visible")
    found = []
    for i in range(selects.count()):
        item = selects.nth(i)
        try:
            options = item.locator("option")
            labels = []
            for j in range(min(options.count(), 500)):
                label = options.nth(j).inner_text().strip()
                if label:
                    labels.append(label)
            found.append((item, labels))
        except Exception:
            continue
    return found


def _term_matches(option_text: str, requested: str) -> bool:
    """Match terms regardless of whether the portal shows 'Fall 2026' or '2026 Fall Term'."""
    shown = _norm(option_text)
    wanted = _norm(requested)
    seasons = ("spring", "summer", "fall", "winter")

    wanted_season = next((s for s in seasons if re.search(rf"\\b{s}\\b", wanted)), None)
    shown_season = next((s for s in seasons if re.search(rf"\\b{s}\\b", shown)), None)
    wanted_year = re.search(r"\\b(20\\d{2})\\b", wanted)
    shown_year = re.search(r"\\b(20\\d{2})\\b", shown)

    if wanted_season and wanted_year and shown_season and shown_year:
        return wanted_season == shown_season and wanted_year.group(1) == shown_year.group(1)

    return shown == wanted or shown.startswith(wanted + " ")


def _select_option_by_text(select, labels, requested, kind):
    """Choose the closest exact code/name or term option."""
    requested_norm = _norm(requested)
    if not requested_norm:
        return False

    candidates = []
    for label in labels:
        label_norm = _norm(label)
        if kind == "subject":
            if _subject_matches(label, requested):
                candidates.append(label)
        elif kind == "term":
            if _term_matches(label, requested):
                candidates.append(label)
        else:
            if label_norm == requested_norm or label_norm.startswith(requested_norm + " "):
                candidates.append(label)

    if not candidates and kind == "subject":
        # A subject name may have been supplied while the portal displays its code,
        # or vice versa. Do not silently choose a fuzzy/ambiguous option.
        return False

    if candidates:
        try:
            select.select_option(label=candidates[0])
            return True
        except Exception:
            try:
                select.select_option(value=candidates[0])
                return True
            except Exception:
                return False
    return False


def _find_input(page, hints):
    """Find a visible input by id/name/placeholder/aria-label."""
    inputs = page.locator("input:visible")
    for i in range(inputs.count()):
        item = inputs.nth(i)
        try:
            attrs = " ".join(
                str(item.get_attribute(attr) or "")
                for attr in ("id", "name", "placeholder", "aria-label", "title")
            )
            if any(hint in _norm(attrs) for hint in hints):
                return item
        except Exception:
            continue
    return None


def _click_search(page):
    # Prefer buttons/inputs whose accessible text indicates a search action.
    candidates = page.locator(
        "button:visible, input[type=submit]:visible, input[type=button]:visible"
    )
    for i in range(candidates.count()):
        item = candidates.nth(i)
        try:
            label = " ".join(
                str(item.get_attribute(attr) or "")
                for attr in ("value", "aria-label", "title")
            )
            label += " " + item.inner_text()
            if re.search(r"\b(search|find|submit|display classes|view sections)\b",
                         label, re.I):
                item.click()
                return True
        except Exception:
            continue

    # If no labeled search button exists, submit the form containing the course field.
    course_input = _find_input(
        page, ("course number", "course", "catalog number", "catalog")
    )
    if course_input is not None:
        try:
            course_input.press("Enter")
            return True
        except Exception:
            pass
    return False


def _select_named_option(page, keywords, requested, kind="exact"):
    """Select an option from the native control whose metadata matches keywords."""
    wanted = _norm(requested)
    if not wanted:
        return False
    for select, labels in _visible_selects(page):
        try:
            meta = _norm(" ".join(str(select.get_attribute(a) or "")
                                  for a in ("id", "name", "aria-label", "title")))
            if keywords and not any(k in meta for k in keywords):
                continue
            if kind == "term":
                matched = next((x for x in labels if _term_matches(x, requested)), None)
            else:
                matched = next((x for x in labels
                                if _norm(x) == wanted or _norm(x).startswith(wanted + " ")), None)
            if matched:
                try:
                    select.select_option(label=matched)
                except Exception:
                    select.select_option(value=matched)
                return True
        except Exception:
            continue
    return False


def _click_custom_choice(page, requested, kind="exact"):
    """Conservative fallback for visible custom dropdown options."""
    wanted = _norm(requested)
    if not wanted:
        return False
    for selector in ("[role=option]:visible", "li:visible", "label:visible"):
        items = page.locator(selector)
        for i in range(min(items.count(), 500)):
            item = items.nth(i)
            try:
                label = item.inner_text().strip()
                matched = (_term_matches(label, requested) if kind == "term"
                           else (_norm(label) == wanted or _norm(label).startswith(wanted + " ")))
                if matched:
                    item.click()
                    page.wait_for_timeout(300)
                    return True
            except Exception:
                continue
    return False


def _capture_schedule_results(page, response, platform):
    tables = page.locator("table:visible")
    table_text = []
    for i in range(min(tables.count(), 40)):
        try:
            value = tables.nth(i).inner_text().strip()
            if value:
                table_text.append(value)
        except Exception:
            pass
    result_text = ""
    for selector in ("[id*=result i]:visible", "[class*=result i]:visible", "main:visible", "body"):
        try:
            value = page.locator(selector).first.inner_text(timeout=2500)
            if value and len(value.strip()) > len(result_text.strip()):
                result_text = value
        except Exception:
            continue
    if not result_text.strip() and not table_text:
        raise RuntimeError("Search completed, but no readable course results were found. "
                           "Check the selected term, campus and subject, or whether the portal requires sign-in.")
    return {"url": page.url, "title": page.title(),
            "status": str(response.status) if response else "No HTTP response",
            "text": result_text, "tables": table_text, "platform": platform}


def _glendale_detail_record(page):
    """Extract Glendale PeopleSoft Class Detail fields, including expandable detail sections."""
    def value(element_id):
        locator = page.locator(f'[id="{element_id}"]')
        try:
            return locator.first.inner_text().strip() if locator.count() else ""
        except Exception:
            return ""

    def clean_text(value):
        return re.sub(r"\s+", " ", str(value or "")).strip()

    # Capture the full visible detail page once. Some PeopleSoft fields (notably
    # consent, prerequisites, class attributes and notes) are rendered as
    # label/value rows without stable IDs, so relying only on field IDs loses them.
    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        body_text = ""
    body_lines = [clean_text(line) for line in body_text.splitlines() if clean_text(line)]

    def section_after_heading(*headings):
        """Return visible text beneath a matching section heading."""
        wanted = {_norm(x) for x in headings}
        for index, line in enumerate(body_lines):
            if _norm(line).rstrip(":") in wanted:
                collected = []
                for next_line in body_lines[index + 1:]:
                    # PeopleSoft detail sections commonly use a following heading
                    # as the boundary; stop only at known section titles.
                    if _norm(next_line).rstrip(":") in {
                        "enrollment information", "class availability",
                        "combined section", "notes", "description",
                        "textbook other materials", "textbook materials",
                        "class details", "meeting information",
                    }:
                        break
                    collected.append(next_line)
                return clean_text(" | ".join(collected))
        return ""

    def label_value(*labels):
        """Extract a label/value pair from the visible page text."""
        wanted = {_norm(x).rstrip(":") for x in labels}
        for index, line in enumerate(body_lines):
            normalized = _norm(line)
            for label in wanted:
                if normalized == label:
                    return body_lines[index + 1] if index + 1 < len(body_lines) else ""
                if normalized.startswith(label + " "):
                    remainder = line[len(label):].lstrip(" :\t")
                    if remainder:
                        return remainder
                    return body_lines[index + 1] if index + 1 < len(body_lines) else ""
        return ""

    heading = value("DERIVED_CLSRCH_DESCR200")
    course_match = re.search(
        r"^\s*([A-Z]{2,6})\s*(\d+[A-Z]?)\s*[- ]\s*(\S+)\s*(.*?)\s*$",
        heading, re.I
    )
    subject_code = course_match.group(1) if course_match else ""
    catalog_number = course_match.group(2) if course_match else ""
    section = course_match.group(3) if course_match else ""
    course_title = course_match.group(4) if course_match else heading

    meta_parts = [part.strip() for part in value("DERIVED_CLSRCH_SSS_PAGE_KEYDESCR").split("|")]
    meeting_rows = []
    meetings = page.locator('span[id^="MTG_SCHED$"]')
    for i in range(meetings.count()):
        schedule_el = meetings.nth(i)
        schedule = clean_text(schedule_el.inner_text())
        suffix = (schedule_el.get_attribute("id") or "").rsplit("$", 1)[-1]

        def indexed(base):
            loc = page.locator(f'[id="{base}${suffix}"]')
            try:
                return clean_text(loc.first.inner_text()) if loc.count() else ""
            except Exception:
                return ""

        meeting_rows.append({
            "meeting_time": schedule,
            "room": indexed("MTG_LOC"),
            "instructor": indexed("MTG_INSTR"),
            "meeting_dates": indexed("MTG_DATE"),
        })

    def unique_join(key):
        return "; ".join(dict.fromkeys(row[key] for row in meeting_rows if row.get(key)))

    status = value("SSR_CLS_DTL_WRK_SSR_DESCRSHORT")
    if not status:
        status_img = page.locator('[id="win0divSSR_CLS_DTL_WRK_SSR_STATUS_LONG"] img')
        if status_img.count():
            status = status_img.first.get_attribute("alt") or status_img.first.get_attribute("title") or ""

    enrollment_info = section_after_heading("Enrollment Information")
    availability_info = section_after_heading("Class Availability")
    combined_section_info = section_after_heading("Combined Section")
    notes = section_after_heading("Notes")
    description = value("DERIVED_CLSRCH_DESCRLONG") or section_after_heading("Description")
    textbook_materials = section_after_heading("Textbook/Other Materials", "Textbook Other Materials")

    # Explicit fields are also parsed individually for downstream QA/export.
    add_consent = label_value("Add Consent")
    enrollment_requirements = label_value("Enrollment Requirements", "Requirement")
    class_attributes = label_value("Class Attributes")
    capacity = value("SSR_CLS_DTL_WRK_ENRL_CAP") or label_value("Combined Section Capacity")
    enrollment = value("SSR_CLS_DTL_WRK_ENRL_TOT") or label_value("Enrollment Total")
    available_seats = value("SSR_CLS_DTL_WRK_AVAILABLE_SEATS") or label_value("Available Seats")
    waitlist_capacity = value("SSR_CLS_DTL_WRK_WAIT_CAP") or label_value("Wait List Capacity")
    waitlist_total = value("SSR_CLS_DTL_WRK_WAIT_TOT") or label_value("Wait List Total")

    return {
        "college": meta_parts[0] if meta_parts else "",
        "term": meta_parts[1] if len(meta_parts) > 1 else "",
        "subject_code": subject_code,
        "course_number": catalog_number,
        "section": section,
        "course_title": course_title,
        "class_number": value("SSR_CLS_DTL_WRK_CLASS_NBR"),
        "meeting_time": unique_join("meeting_time"),
        "room": unique_join("room"),
        "instructor": unique_join("instructor"),
        "meeting_dates": unique_join("meeting_dates") or value("SSR_CLS_DTL_WRK_SSR_DATE_LONG"),
        "status": status,
        "credits": value("SSR_CLS_DTL_WRK_UNITS_RANGE"),
        "campus": value("CAMPUS_TBL_DESCR"),
        "location": value("CAMPUS_LOC_VW_DESCR"),
        "capacity": capacity,
        "enrollment": enrollment,
        "available_seats": available_seats,
        "waitlist_capacity": waitlist_capacity,
        "waitlist_total": waitlist_total,
        "description": description,
        "add_consent": add_consent,
        "enrollment_requirements": enrollment_requirements,
        "class_attributes": class_attributes,
        "enrollment_information": enrollment_info,
        "class_availability": availability_info,
        "combined_section_information": combined_section_info,
        "class_notes": notes,
        "textbook_materials": textbook_materials,
        "detail_text": clean_text(body_text),
        "meeting_rows": meeting_rows,
    }


def _glendale_people_soft_search(url, term, subject, course_number, timeout):
    """Glendale CC PeopleSoft class search: Institution + term + search criteria."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        page.set_default_timeout(timeout * 1000)
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            try: page.wait_for_load_state("networkidle", timeout=10000)
            except Exception: pass
            page.wait_for_timeout(1200)
            institution = _select_named_option(page, ("institution",), "Glendale Community College")
            if not institution:
                # Some PeopleSoft skins expose a visible institution label rather than a named select.
                institution = _click_custom_choice(page, "Glendale Community College")
            term_ok = _select_named_option(page, ("term", "strm"), term, "term")
            if not term_ok:
                for sel, labels in _visible_selects(page):
                    if _select_option_by_text(sel, labels, term, "term"):
                        term_ok = True
                        break
            # Glendale's PeopleSoft subject dropdown uses a CODE as the option
            # value (for example value="MATH") and the full department name as
            # the visible label ("MATHEMATICS"). Match both value and label.
            subject_ok = False
            subject_select = page.locator(
                'select[name="SSR_CLSRCH_WRK_SUBJECT_SRCH$0"]'
            )
            if subject_select.count() == 0:
                subject_select = page.locator(
                    'select[id^="SSR_CLSRCH_WRK_SUBJECT_SRCH$"]'
                )
            if subject_select.count():
                options = subject_select.first.locator("option")
                wanted = _norm(subject)
                for option_index in range(options.count()):
                    option = options.nth(option_index)
                    option_value = (option.get_attribute("value") or "").strip()
                    option_label = option.inner_text().strip()
                    if not option_value:
                        continue
                    if _norm(option_value) == wanted or _norm(option_label) == wanted:
                        subject_select.first.select_option(value=option_value)
                        subject_ok = True
                        break
            # Fallback to a named native subject dropdown on a changed PeopleSoft skin.
            if not subject_ok:
                subject_ok = _select_named_option(
                    page, ("subject",), subject, "exact"
                )
            course_input = _find_input(page, ("course number", "catalog number", "catalog nbr", "course"))
            if course_input is not None: course_input.fill(str(course_number))
            if not term_ok or not subject_ok:
                raise RuntimeError(f"Glendale PeopleSoft selection incomplete (institution={institution}, term={term_ok}, subject={subject_ok}). Verify the public search page and field labels.")
            if not _click_search(page):
                raise RuntimeError("Glendale PeopleSoft: could not find the Search button.")
            page.wait_for_timeout(1800)
            try: page.wait_for_load_state("networkidle", timeout=10000)
            except Exception: pass
            result = _capture_schedule_results(page, response, "glendale_peoplesoft")

            # If search returned a detail page directly, parse it first.
            records = []
            detail_record = _glendale_detail_record(page)
            if detail_record.get("class_number"):
                records.append(detail_record)

            # In the normal Glendale workflow, each Class Number is a link to a
            # separate Class Detail view. Open each one, extract the detail
            # fields, then return to the result list for the next class.
            class_link_ids = page.locator('a[id^="MTG_CLASS_NBR$"]').evaluate_all(
                "els => els.map(el => el.id).filter(Boolean)"
            )
            seen_class_numbers = {
                str(record.get("class_number", "")).strip() for record in records
            }

            for link_id in class_link_ids:
                link = page.locator(f'[id="{link_id}"]')
                if not link.count():
                    continue
                class_number_text = link.first.inner_text().strip()
                try:
                    # PeopleSoft may open the detail view in the same tab or
                    # in a popup depending on the portal configuration.
                    detail_page = None
                    try:
                        with page.context.expect_page(timeout=1800) as popup_info:
                            link.first.click()
                        detail_page = popup_info.value
                        detail_page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        # No popup: the detail view is expected in this tab.
                        detail_page = page

                    try:
                        detail_page.wait_for_selector(
                            '[id="SSR_CLS_DTL_WRK_CLASS_NBR"], '
                            '[id="DERIVED_CLSRCH_DESCR200"]',
                            state="attached", timeout=10000
                        )
                    except Exception:
                        pass

                    detail = _glendale_detail_record(detail_page)
                    if not detail.get("class_number"):
                        detail["class_number"] = class_number_text

                    if detail.get("class_number") and detail.get("class_number") not in seen_class_numbers:
                        # Keep the search-result fields if a detail page omits
                        # an optional value.
                        detail["source_url"] = detail_page.url
                        records.append(detail)
                        seen_class_numbers.add(detail["class_number"])

                    if detail_page is not page:
                        detail_page.close()
                    elif detail_page is page and page.locator('[id="SSR_CLS_DTL_WRK_CLASS_NBR"]').count():
                        page.go_back(wait_until="domcontentloaded", timeout=15000)
                        page.wait_for_timeout(900)
                        try:
                            page.locator('a[id^="MTG_CLASS_NBR$"]').first.wait_for(
                                state="visible", timeout=10000
                            )
                        except Exception:
                            # A PeopleSoft POST result can restore asynchronously.
                            page.wait_for_timeout(1200)
                except Exception:
                    # Do not lose the search result if a detail link fails.
                    continue

            # Preserve the original result-grid parser as a fallback. It also
            # fills any class for which the detail link could not be opened.
            class_links = page.locator('a[id^="MTG_CLASS_NBR$"]')
            for i in range(class_links.count()):
                class_link = class_links.nth(i)
                class_id = class_link.get_attribute("id") or ""
                match = re.search(r"MTG_CLASS_NBR\$(\d+)$", class_id)
                if not match:
                    continue
                idx = match.group(1)

                def field_value(field_id):
                    locator = page.locator(f'[id="{field_id}${idx}"]')
                    try:
                        return locator.first.inner_text().strip() if locator.count() else ""
                    except Exception:
                        return ""

                class_number = class_link.inner_text().strip()
                if class_number in seen_class_numbers:
                    continue
                section = field_value("MTG_CLASSNAME")
                meeting_time = field_value("MTG_DAYTIME")
                room = field_value("MTG_ROOM")
                instructor_name = field_value("MTG_INSTR")
                meeting_dates = field_value("MTG_TOPIC")
                status_locator = page.locator(
                    f'[id="DERIVED_CLSRCH_SSR_STATUS_LONG${idx}"] img'
                )
                status = ""
                try:
                    if status_locator.count():
                        status = status_locator.first.get_attribute("alt") or status_locator.first.get_attribute("title") or ""
                except Exception:
                    pass

                course_title = ""
                try:
                    group = page.locator('[id^="win0divSSR_CLSRSLT_WRK_GROUPBOX2GP"]')
                    if group.count():
                        course_title = group.first.inner_text().strip()
                except Exception:
                    pass

                records.append({
                    "class_number": class_number,
                    "section": section,
                    "meeting_time": meeting_time,
                    "room": room,
                    "instructor": instructor_name,
                    "meeting_dates": meeting_dates,
                    "status": status,
                    "course_title": course_title,
                })
            result["records"] = records
            return result
        finally:
            browser.close()


def search_banner_classes(
    url: str,
    term: str,
    subject: str,
    course_number: str,
    timeout: int = 60
):
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

    parsed_url = urlparse(str(url or "").strip())
    host = (parsed_url.hostname or "").casefold()
    if host == "psprd.glendale.edu" or host.endswith(".psprd.glendale.edu"):
        return _glendale_people_soft_search(
            url=url, term=term, subject=subject,
            course_number=course_number, timeout=timeout,
        )

    BASE = "https://registration.uncw.edu/StudentRegistrationSsb"
    TERM_URL = BASE + "/ssb/term/termSelection?mode=search"

    TERM_IDS = {
        "fall 2026": "202710",
        "spring 2027": "202720",
    }

    term_id = TERM_IDS.get(term.strip().lower())

    if not term_id:
        raise RuntimeError(
            f"Term ID is not configured for {term}. "
            "Currently configured: Fall 2026 and Spring 2027."
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
                    f"{term} was selected, but Browse Classes did not open."
                )

            print(f"{term} selected successfully.")

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

            # STEP 6: Capture the structured JSON response generated by the
            # official Banner search UI. This avoids guessing a private endpoint.
            captured_responses = []

            def capture_uncw_json(response):
                if response.status != 200:
                    return
                try:
                    payload = response.json()
                except Exception:
                    return
                if not isinstance(payload, dict) or payload.get("success") is not True:
                    return
                data = payload.get("data")
                if not isinstance(data, list) or not data:
                    return
                if not any(
                    isinstance(item, dict)
                    and "courseReferenceNumber" in item
                    and "faculty" in item
                    for item in data
                ):
                    return
                captured_responses.append({
                    "url": response.url,
                    "status": response.status,
                    "payload": payload,
                })

            page.on("response", capture_uncw_json)

            # STEP 7: Search.
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

            # STEP 8: Attempt to show up to 50 results. Some Banner versions
            # issue a second JSON response after changing the page size.
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

            # STEP 9: Capture rendered and structured results.
            page.wait_for_timeout(1200)
            best_capture = max(
                captured_responses,
                key=lambda item: len(item["payload"].get("data", [])),
                default=None,
            )
            payload = best_capture["payload"] if best_capture else {}
            records = payload.get("data", []) if isinstance(payload, dict) else []
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
                "url": best_capture["url"] if best_capture else page.url,
                "title": title,
                "status": str(best_capture["status"] if best_capture else response.status),
                "platform": "uncw_banner",
                "text": rendered_text,
                "tables": table_text,
                "records": records,
                "total_count": payload.get("totalCount", len(records)) if payload else 0,
                "sections_fetched_count": payload.get("sectionsFetchedCount", len(records)) if payload else 0,
                "raw_payload": payload,
            }

        finally:
            browser.close()

