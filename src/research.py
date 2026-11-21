import re

from .fetcher import fetch_page, search_banner_classes
from .schemas import COURSE_COLUMNS, INSTRUCTOR_COLUMNS
from playwright.sync_api import sync_playwright


def _blank_row(columns):
    return {column: "" for column in columns}


def _clip(text, query, window=1800):
    text = text or ""
    if not query:
        return text[:window]
    match = re.search(re.escape(str(query)), text, re.IGNORECASE)
    if not match:
        return text[:window]
    start = max(0, match.start() - window // 3)
    return text[start:start + window]


def _evidence(page, fields, note=""):
    return {
        "Source URL": page.get("url", ""),
        "Source title": page.get("title", ""),
        "HTTP status": page.get("status", ""),
        "Fields supported / review": fields,
        "Evidence note": note,
    }


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _parse_uncw_course_records(
    text, subject_code="", course_code="", crn_filter="", instructor_filter=""
):
    """Parse the tab-separated rows and detail blocks returned by UNCW Banner."""
    records = []
    if not text:
        return records

    row_pattern = re.compile(
        r"^([^\t\r\n]+)\t([^\t\r\n]+)\t(\d+)\t([^\t\r\n]+)\t"
        r"([\d.]+)\t(\d+)\t([^\t\r\n]+)\t([^\r\n]+)$",
        re.MULTILINE,
    )
    matches = list(row_pattern.finditer(text))

    for index, match in enumerate(matches):
        title, subject_name, number, section, credits, crn, term_name, instructor = (
            _clean(value) for value in match.groups()
        )

        if course_code and number != str(course_code).strip():
            continue
        if crn_filter and crn != str(crn_filter).strip():
            continue

        clean_instructor = re.sub(
            r"\s*\(Primary\)\s*$", "", instructor, flags=re.IGNORECASE
        ).strip()
        if instructor_filter and (
            instructor_filter.casefold() not in clean_instructor.casefold()
        ):
            continue

        block_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():block_end]

        row = _blank_row(COURSE_COLUMNS)
        row["Course Subject / Department Name"] = subject_name
        row["Course Discipline"] = subject_code
        row["Course Catalog Code"] = number
        row["Section ID"] = section
        row["Course ID"] = crn
        row["Course Name"] = title
        row["Credit Hours"] = credits

        if "Online-Extension" in block:
            row["Campus"] = "Extension"
            row["Course Modality"] = "Online"
            row["Course Meeting Location"] = "Online-Extension"
        else:
            row["Campus"] = "Main"
            row["Course Modality"] = "In-Person"
            building = re.search(r"Building:\s*(.*?)\s+Room:", block, re.I)
            room = re.search(r"Room:\s*(.*?)\s+Start Date:", block, re.I)
            location = []
            if building:
                location.append(_clean(building.group(1)))
            if room and _clean(room.group(1)).casefold() != "none":
                location.append("Room " + _clean(room.group(1)))
            row["Course Meeting Location"] = ", ".join(location)

        for label, column in (
            ("Start Date", "Course Date Start"),
            ("End Date", "Course Date End"),
        ):
            found = re.search(rf"{label}:\s*(\d{{2}}/\d{{2}}/\d{{4}})", block, re.I)
            if found:
                row[column] = found.group(1)

        days = re.search(
            r"\b(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday"
            r"(?:,(?:Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday))+)",
            block,
            re.I,
        )
        times = re.search(
            r"(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(\d{1,2}:\d{2}\s*[AP]M)",
            block,
            re.I,
        )
        meeting = []
        if days:
            meeting.append(days.group(1).replace(",", ", "))
        if times:
            meeting.append(f"{times.group(1)} - {times.group(2)}")
        row["Course Meeting Times"] = ", ".join(meeting)

        if re.search(r"\bLecture\b", block, re.I):
            row["Course Type"] = "Lecture"

        seats = re.search(r"(\d+)\s+of\s+(\d+)\s+seats remain", block, re.I)
        if seats:
            remaining, capacity = map(int, seats.groups())
            row["Course Enrollment Capacity"] = capacity
            row["Course Actual Enrollment"] = capacity - remaining

        waitlist = re.search(
            r"(\d+)\s+of\s+(\d+)\s+waitlist seats remain", block, re.I
        )
        if waitlist:
            remaining, capacity = map(int, waitlist.groups())
            row["Course Waitlist Capacity"] = capacity
            row["Course Waitlist Enrollment"] = capacity - remaining

        records.append(row)

    return _dedupe_courses(records)


def _dedupe_courses(records):
    unique = []
    seen = set()
    for row in records:
        key = (
            str(row.get("Course ID", "")).strip(),
            str(row.get("Section ID", "")).strip(),
            str(row.get("Course Catalog Code", "")).strip(),
        )
        # If both identifiers are missing, use the complete row as a fallback key.
        if not any(key):
            key = tuple(str(row.get(col, "")) for col in COURSE_COLUMNS)
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _split_table_line(line):
    line = line.strip()
    if "\t" in line:
        return [_clean(x) for x in line.split("\t") if _clean(x)]
    # Some portals render HTML table cells as runs of spaces.
    return [_clean(x) for x in re.split(r"\s{2,}", line) if _clean(x)]


def _header_index(headers, patterns):
    for i, header in enumerate(headers):
        normalized = re.sub(r"[^a-z0-9]+", " ", header.casefold()).strip()
        if any(re.search(pattern, normalized) for pattern in patterns):
            return i
    return None


def _parse_generic_course_records(
    texts, subject="", course_code="", crn_filter="", instructor_filter=""
):
    """
    Conservative parser for ordinary HTML schedule tables.
    Maps values only when a recognizable header exists; does not guess columns.
    """
    records = []
    for text in texts:
        if not text:
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for header_pos, line in enumerate(lines[:-1]):
            headers = _split_table_line(line)
            if len(headers) < 3:
                continue

            course_idx = _header_index(
                headers, (r"course(\s*(number|no|code))?$", r"catalog(\s*number)?", r"subject\s*course")
            )
            title_idx = _header_index(headers, (r"course\s*title", r"course\s*name", r"title$"))
            section_idx = _header_index(headers, (r"section", r"section\s*id"))
            crn_idx = _header_index(headers, (r"crn", r"course\s*reference", r"class\s*number"))
            subject_idx = _header_index(headers, (r"subject", r"department", r"course\s*prefix"))
            instructor_idx = _header_index(headers, (r"instructor", r"faculty", r"professor"))
            credits_idx = _header_index(headers, (r"credit(\s*hours?)?", r"units"))
            days_idx = _header_index(headers, (r"days", r"meeting\s*days"))
            time_idx = _header_index(headers, (r"time", r"meeting\s*time"))
            location_idx = _header_index(headers, (r"location", r"room", r"building"))
            capacity_idx = _header_index(headers, (r"capacity", r"enrollment\s*capacity"))
            enrolled_idx = _header_index(headers, (r"enrolled", r"actual\s*enrollment", r"enrollment$"))

            # Need at least one course identifier and a title or instructor-like result column.
            if course_idx is None and crn_idx is None:
                continue
            if title_idx is None and instructor_idx is None:
                continue

            for data_line in lines[header_pos + 1:]:
                values = _split_table_line(data_line)
                if len(values) < len(headers):
                    continue

                def get(idx):
                    return values[idx] if idx is not None and idx < len(values) else ""

                course_value = get(course_idx)
                number_match = re.search(r"\b([A-Z]{0,5}\s*\d{2,5}[A-Z]?)\b", course_value, re.I)
                number = number_match.group(1).replace(" ", "") if number_match else course_value
                if course_code and _clean(number).casefold() != _clean(course_code).casefold():
                    # Some schedules combine prefix and number in one cell.
                    if not re.search(rf"\b{re.escape(str(course_code))}\b", course_value, re.I):
                        continue

                crn = get(crn_idx)
                if crn_filter and crn.strip() != str(crn_filter).strip():
                    continue

                instructor_value = get(instructor_idx)
                if instructor_filter and instructor_filter.casefold() not in instructor_value.casefold():
                    continue

                row = _blank_row(COURSE_COLUMNS)
                row["Course Subject / Department Name"] = get(subject_idx) or subject
                row["Course Discipline"] = subject
                row["Course Catalog Code"] = number
                row["Course ID"] = crn
                row["Section ID"] = get(section_idx)
                row["Course Name"] = get(title_idx)
                row["Credit Hours"] = get(credits_idx)
                row["Course Meeting Times"] = ", ".join(
                    x for x in (get(days_idx), get(time_idx)) if x
                )
                row["Course Meeting Location"] = get(location_idx)
                row["Course Enrollment Capacity"] = get(capacity_idx)
                row["Course Actual Enrollment"] = get(enrolled_idx)
                if row["Course Meeting Location"]:
                    row["Course Modality"] = "In-Person"
                records.append(row)

    return _dedupe_courses(records)

def _search_kirkwood_courses(
    schedule_url, term, subject, course_code, srn, instructor, timeout
):
    """Search Kirkwood by subject and term, optionally narrowing to one course."""
    subject_code = _clean(subject).split()[0].upper()
    course_number = _clean(course_code)
    if not subject_code:
        raise ValueError("Enter a Kirkwood subject code, for example HUM or ACC.")

    term_match = re.search(r"(Fall|Spring|Summer)\s+(\d{4})", term, re.IGNORECASE)
    if not term_match:
        raise ValueError(
            "Kirkwood term must look like Fall 2026, Spring 2027 or Summer 2027."
        )

    season, year = term_match.group(1).lower(), term_match.group(2)
    term_suffix = {"fall": "FA", "spring": "SP", "summer": "SU"}[season]
    term_code = f"{year}{term_suffix}"
    checkbox_id = f"STATICterm{year}{term_suffix}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(schedule_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            search_box = page.get_by_placeholder("Search for courses...")
            search_box.fill(f"{subject_code} {course_number}".strip())
            search_box.press("Enter")
            page.wait_for_timeout(3000)

            checkbox = page.locator(f"#{checkbox_id}")
            label = page.locator(f'label[for="{checkbox_id}"]')
            if checkbox.count() == 0:
                raise RuntimeError(f"Kirkwood term filter not found: {term}")
            if not checkbox.is_checked():
                label.click()
            page.wait_for_timeout(2000)

            # Each course has its own sections request. With a blank course
            # number, click every matching subject course; with a number, only
            # click the matching course. Do not change other college adapters.
            buttons = page.locator(
                f'button[id^="collapsible-view-available-sections-for-{subject_code}-"]'
            )
            button_ids = buttons.evaluate_all("els => els.map(el => el.id)")
            selected_buttons = []
            for button_id in button_ids:
                match = re.search(
                    rf"-available-sections-for-{re.escape(subject_code)}-([A-Za-z0-9]+)-groupHeading$",
                    button_id,
                    re.IGNORECASE,
                )
                if not match:
                    continue
                found_number = match.group(1)
                if course_number and found_number.casefold() != course_number.casefold():
                    continue
                selected_buttons.append((button_id, found_number))

            if not selected_buttons:
                target = f"{subject_code}-{course_number}" if course_number else subject_code
                raise RuntimeError(f"No Kirkwood course results found for {target} in {term}.")

            rows, context_lines, evidence_urls = [], [], []
            for button_id, found_number in selected_buttons:
                button = page.locator(f'#{button_id}')
                with page.expect_response(
                    lambda response: (
                        "/Student/Courses/Sections" in response.url
                        and response.status == 200
                    ),
                    timeout=timeout * 1000,
                ) as response_info:
                    button.click()
                response = response_info.value
                evidence_urls.append(response.url)
                payload = response.json()
                retrieved = payload.get("SectionsRetrieved", {})
                term_groups = retrieved.get("TermsAndSections", [])
                selected_group = next(
                    (group for group in term_groups
                     if group.get("Term", {}).get("Code") == term_code),
                    None,
                )
                if not selected_group:
                    continue

                course_info = retrieved.get("Course", {})
                sections = selected_group.get("Sections", [])
                course_label = f"{subject_code}-{found_number}"
                context_lines.extend([
                    "COLLEGE: Kirkwood Community College", f"TERM: {term}",
                    f"COURSE: {course_label}",
                    f"TITLE: {course_info.get('Title', '')}",
                    f"DESCRIPTION: {course_info.get('Description', '')}", "",
                ])

                for item in sections:
                    section = item.get("Section", {})
                    section_name = section.get("SectionNameDisplay", "")
                    if srn and str(srn).strip() not in (
                        section_name, str(section.get("Synonym", ""))
                    ):
                        continue
                    faculty = item.get("FacultyDisplay", "")
                    if instructor and instructor.casefold() not in faculty.casefold():
                        continue

                    meetings, locations, online_flags = [], [], []
                    for meeting in section.get("FormattedMeetingTimes", []):
                        day = meeting.get("DaysOfWeekDisplay", "")
                        start_time = meeting.get("StartTimeDisplay", "")
                        end_time = meeting.get("EndTimeDisplay", "")
                        dates = meeting.get("DatesDisplay", "")
                        time_text = " ".join(
                            part for part in (day, start_time, "-", end_time, dates) if part
                        )
                        if time_text:
                            meetings.append(time_text)
                        building = meeting.get("BuildingDisplay", "")
                        room = meeting.get("RoomDisplay", "")
                        location = " ".join(part for part in (building, room) if part)
                        if location and location not in locations:
                            locations.append(location)
                        online_flags.append(meeting.get("IsOnline", False))

                    if online_flags and all(online_flags):
                        modality = "Online"
                    elif any(online_flags):
                        modality = "Hybrid"
                    else:
                        modality = "In-Person"
                    location_display = section.get("LocationDisplay", "")
                    if location_display and location_display not in locations:
                        locations.insert(0, location_display)

                    row = _blank_row(COURSE_COLUMNS)
                    row["Course Subject / Department Name"] = subject
                    row["Course Discipline"] = subject_code
                    row["Course Catalog Code"] = found_number
                    row["Section ID"] = section_name
                    row["Course ID"] = section.get("Synonym", "")
                    row["Course Name"] = section.get("Title", "")
                    row["Course Description"] = course_info.get("Description", "")
                    row["Credit Hours"] = section.get("MinimumCredits", "")
                    row["Course Date Start"] = section.get("StartDateDisplay", "")
                    row["Course Date End"] = section.get("EndDateDisplay", "")
                    row["Course Meeting Times"] = "; ".join(meetings)
                    row["Course Meeting Location"] = "; ".join(locations)
                    row["Course Enrollment Capacity"] = section.get("Capacity", "")
                    row["Course Actual Enrollment"] = section.get("Enrolled", "")
                    row["Course Waitlist Enrollment"] = section.get("Waitlisted", "")
                    row["Course Modality"] = modality
                    row["Course Type"] = ", ".join(sorted({
                        detail.get("InstructorMethod", "")
                        for detail in item.get("InstructorDetails", [])
                        if detail.get("InstructorMethod")
                    }))
                    row["Instructor"] = faculty
                    rows.append(row)
                    context_lines.extend([
                        f"SECTION: {section_name}", f"INSTRUCTOR: {faculty}",
                        f"LOCATION: {row['Course Meeting Location']}",
                        f"MEETING TIMES: {row['Course Meeting Times']}",
                        f"AVAILABILITY: {section.get('AvailabilityDisplay', '')}", "",
                    ])

            rows = _dedupe_courses(rows)
            if not rows:
                raise RuntimeError(
                    "Kirkwood returned course buttons but no matching sections. "
                    "Check the term, CRN/section ID and instructor filters."
                )
            return {
                "course": rows,
                "context": "\n".join(context_lines),
                "url": evidence_urls[0],
                "status": 200,
                "title": page.title(),
            }
        finally:
            browser.close()


def _search_uncw_instructor(name, department="", timeout=30):
    """Search UNCW's official ASP.NET People Search using its real form controls."""
    directory_url = "https://itsappserv01.uncw.edu/directory/"

    requested = _clean(name)
    if "," in requested:
        last_name, first_part = [part.strip() for part in requested.split(",", 1)]
        first_name = first_part.split()[0] if first_part else ""
    else:
        name_parts = requested.split()
        if len(name_parts) < 2:
            return {
                "matches": [], "url": directory_url,
                "title": "Directory: UNCW", "status": ""
            }
        first_name, last_name = name_parts[0], name_parts[-1]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            response = page.goto(
                directory_url,
                wait_until="domcontentloaded",
                timeout=timeout * 1000,
            )

            # These IDs and names are taken from the supplied UNCW page source.
            # It is an ASP.NET WebForms form; Search is an input[type=submit].
            page.locator("#rdoSearchTable_1").check(timeout=timeout * 1000)
            page.locator("#txtFirstName").fill(first_name)
            page.locator("#txtLastName").fill(last_name)

            if department:
                department_select = page.locator("#lstDepartments")
                options = department_select.locator("option").all()
                wanted = _clean(department).casefold()
                for option in options:
                    label = _clean(option.inner_text())
                    value = option.get_attribute("value") or ""
                    if label.casefold() == wanted or value.replace("&#32;", " ").casefold() == wanted:
                        department_select.select_option(value=value)
                        break

            # The source confirms this is a POST submit control, not a button.
            with page.expect_navigation(
                wait_until="domcontentloaded",
                timeout=timeout * 1000,
            ):
                page.locator("#btnSearch").click()

            table = page.locator("#gvDirectory")
            table.wait_for(state="attached", timeout=timeout * 1000)

            headers = [
                _clean(value).casefold()
                for value in table.locator("tr").first.locator("th").all_text_contents()
            ]
            matches = []
            rows = table.locator("tr")

            def normalized_person(value):
                return re.sub(r"[^a-z]", "", _clean(value).casefold())

            requested_full = normalized_person(f"{first_name} {last_name}")
            for i in range(1, rows.count()):
                cells = rows.nth(i).locator("td")
                values = [_clean(value) for value in cells.all_text_contents()]
                if not values:
                    continue
                record = dict(zip(headers, values))
                found_name = record.get("name", "")
                if normalized_person(found_name) != requested_full:
                    continue

                email = ""
                if "email" in headers:
                    email_cell = cells.nth(headers.index("email"))
                    email_link = email_cell.locator('a[href^="mailto:"]')
                    if email_link.count():
                        email = (
                            email_link.first.get_attribute("href") or ""
                        ).replace("mailto:", "").split("?")[0].strip()

                matches.append({
                    "name": found_name,
                    "title": record.get("title", ""),
                    "department": record.get("department", ""),
                    "phone": record.get("phone", ""),
                    "email": email,
                    "campus_box": record.get("campus box", ""),
                })

            return {
                "matches": matches,
                "url": page.url,
                "title": page.title(),
                "status": response.status if response else "",
            }
        finally:
            browser.close()


def _search_cmich_courses(schedule_url, term, subject, course_code, srn, instructor, timeout):
    """Search CMU's public Course Search and Registration page via its UI."""
    base_url = schedule_url or "https://courseregistration.apps.cmich.edu/"
    subject_code = str(subject or "").strip().split()[0].upper()
    number = str(course_code or "").strip()
    if not subject_code:
        raise ValueError("CMU course search requires a department/subject code.")
    if not re.fullmatch(r"[A-Z0-9]+", subject_code):
        raise ValueError(f"Invalid CMU department code: {subject_code!r}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            response = page.goto(base_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            page.locator("#AcademicSession").wait_for(state="visible", timeout=timeout * 1000)

            term_options = page.locator("#AcademicSession option").evaluate_all(
                "els => els.map(e => ({label:(e.label || e.textContent || '').trim(), value:e.value}))"
            )
            selected_term = next(
                (x for x in term_options if x["label"].casefold() == str(term).strip().casefold()),
                None,
            )
            if not selected_term or not selected_term["value"]:
                available = ", ".join(x["label"] for x in term_options if x["value"])
                raise RuntimeError(f"CMU term '{term}' was not found. Available terms: {available}")

            page.locator("#AcademicSession").select_option(selected_term["value"])
            page.locator("#Keyword").fill(f"{subject_code} {number}".strip())

            # Department selection is optional; the course keyword is sufficient
            # and avoids depending on CMU's custom Angular multi-select internals.
            with page.expect_response(
                lambda r: r.request.method in ("POST", "GET") and r.status < 500,
                timeout=timeout * 1000,
            ) as response_info:
                page.locator("#Search").click()
            search_response = response_info.value

            page.wait_for_function(
                """() => {
                    const bodies = document.querySelectorAll('tbody[ng-repeat*="course in vm.searchedCourses"]');
                    return bodies.length > 0 || document.body.innerText.includes('No results');
                }""",
                timeout=timeout * 1000,
            )
            page.wait_for_timeout(500)

            course_groups = page.locator('tbody[ng-repeat*="course in vm.searchedCourses"]')
            rows = []
            context = [f"COLLEGE: Central Michigan University", f"TERM: {term}", f"COURSE: {subject_code} {number}"]
            for group_index in range(course_groups.count()):
                group = course_groups.nth(group_index)
                heading = _clean(group.locator("h3").first.inner_text()) if group.locator("h3").count() else ""
                course_match = re.search(r"([A-Z]{2,6})\s+(\w+)\s*-\s*(.*?)\s*\[CR\s*([\d.]+)", heading, re.I)
                if course_match:
                    found_subject, found_number, title, credits = course_match.groups()
                    if found_subject.upper() != subject_code:
                        continue
                    if number and found_number.upper() != number.upper():
                        continue
                else:
                    title, credits = heading, ""

                section_rows = group.locator('tr[ng-repeat*="section in course.sections"]')
                for idx in range(section_rows.count()):
                    tr = section_rows.nth(idx)
                    cells = tr.locator("td")
                    if cells.count() < 8:
                        continue
                    values = [_clean(cells.nth(i).inner_text()) for i in range(cells.count())]
                    section_id = values[1] if len(values) > 1 else ""
                    if srn and str(srn).strip() != section_id:
                        continue
                    meeting_days = values[2] if len(values) > 2 else ""
                    time_and_dates = values[3] if len(values) > 3 else ""
                    location = values[4] if len(values) > 4 else ""
                    modality = values[5] if len(values) > 5 else ""
                    faculty = values[6] if len(values) > 6 else ""
                    seats_text = values[7] if len(values) > 7 else ""
                    if instructor and instructor.casefold() not in faculty.casefold():
                        continue

                    date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4})", time_and_dates)
                    time_match = re.search(r"(\d{1,2}:\d{2}\s*[AP]M\s*-\s*\d{1,2}:\d{2}\s*[AP]M)", time_and_dates, re.I)
                    capacity = re.search(r"Total:\s*(\d+)", seats_text, re.I)
                    available = re.search(r"Available:\s*(\d+)", seats_text, re.I)
                    waitlist = re.search(r"Wait List:\s*(\d+)", seats_text, re.I)

                    row = _blank_row(COURSE_COLUMNS)
                    row["Course Subject / Department Name"] = subject
                    row["Course Discipline"] = subject_code
                    row["Course Catalog Code"] = found_number if course_match else number
                    row["Course Name"] = title
                    row["Credit Hours"] = credits
                    row["Course ID"] = section_id
                    row["Section ID"] = section_id
                    row["Course Meeting Times"] = "; ".join(x for x in (meeting_days, time_match.group(1) if time_match else "") if x)
                    row["Course Meeting Location"] = location
                    row["Course Modality"] = modality
                    row["Instructor"] = faculty
                    if date_match:
                        row["Course Date Start"], row["Course Date End"] = date_match.groups()
                    if capacity:
                        row["Course Enrollment Capacity"] = capacity.group(1)
                    if available and capacity:
                        row["Course Actual Enrollment"] = str(max(0, int(capacity.group(1)) - int(available.group(1))))
                    if waitlist:
                        row["Course Waitlist Enrollment"] = waitlist.group(1)
                    rows.append(row)
                    context.extend([f"SECTION: {section_id}", f"TITLE: {title}", f"INSTRUCTOR: {faculty}", f"MEETING: {row['Course Meeting Times']}", f"LOCATION: {location}", ""])

            if not rows:
                visible = _clean(page.locator("body").inner_text())
                raise RuntimeError(
                    f"CMU search completed but no matching sections were extracted for {subject_code} {number or '(all courses)'} ({term}). "
                    f"Check the course number, term and filters. Page message: {visible[-500:]}"
                )

            return {
                "course": _dedupe_courses(rows),
                "context": "\n".join(context),
                "url": page.url,
                "title": page.title(),
                "status": search_response.status if search_response else (response.status if response else ""),
            }
        finally:
            browser.close()


def _search_cmich_instructor(name, department="", faculty_url="", timeout=30):
    """Search CMU's official PeopleSearch JSON API and match the requested person."""
    from urllib.parse import urlencode, urljoin
    import json
    from urllib.request import Request, urlopen

    query = _clean(name)
    if not query:
        raise ValueError("CMU instructor search requires an instructor name.")

    # Support schedule formats such as "Roethlisberger, M" and full names.
    if "," in query:
        surname, given_part = [part.strip() for part in query.split(",", 1)]
        requested_first = given_part.split()[0] if given_part else ""
    else:
        name_parts = query.split()
        surname = name_parts[-1] if name_parts else query
        requested_first = name_parts[0] if len(name_parts) > 1 else ""

    params = {
        "indexCatalogue": "cmich-people",
        "searchQuery": surname,
        "wordsMode": "AllWords",
        "sf_culture": "en",
        "page": 1,
        "pageSize": 10,
        "orderBy": "Title asc",
        "category": "All",
    }
    endpoint = "https://www.cmich.edu/api/PeopleSearch/results"
    request_url = endpoint + "?" + urlencode(params)
    request = Request(
        request_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.cmich.edu/directory",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"CMU PeopleSearch API request failed: {exc}") from exc

    records = payload.get("Data", []) if isinstance(payload, dict) else []
    surname_key = surname.casefold().strip(" .")
    candidates = []
    for item in records:
        first = _clean(item.get("FirstName", ""))
        last = _clean(item.get("LastName", ""))
        if last.casefold() != surname_key:
            continue
        if requested_first:
            first_key = requested_first.casefold().strip(" .")
            # Full first name matches exactly; initials match the first letter.
            if not (
                first.casefold() == first_key
                or (len(first_key) == 1 and first.casefold().startswith(first_key))
            ):
                continue
        candidates.append(item)

    if not candidates:
        returned_names = [
            _clean(item.get("Title") or item.get("Name"))
            for item in records
        ]
        raise RuntimeError(
            f"CMU PeopleSearch returned {len(records)} record(s) for "
            f"'{surname}', but none matched instructor '{query}'. "
            f"Returned names: {', '.join(returned_names) or '[none]'}. "
            f"API URL: {request_url}"
        )

    # If name matching still leaves multiple people, use a supplied department
    # only as a tie-breaker; never silently choose the first same-surname record.
    if len(candidates) > 1 and department:
        dept_key = department.casefold()
        dept_matches = [
            item for item in candidates
            if any(dept_key in _clean(value).casefold()
                   for value in (item.get("Department") or []))
        ]
        if len(dept_matches) == 1:
            candidates = dept_matches

    if len(candidates) != 1:
        names = ", ".join(_clean(item.get("Title") or item.get("Name")) for item in candidates)
        raise RuntimeError(
            f"CMU PeopleSearch found multiple possible matches for '{query}': "
            f"{names}. Provide the instructor's first name or initial."
        )

    item = candidates[0]
    profile_url = urljoin("https://www.cmich.edu", item.get("Link") or "")
    departments = item.get("Department") or []
    department_name = "; ".join(_clean(value) for value in departments if _clean(value))
    return {
        "url": request_url,
        "source_title": "Central Michigan University PeopleSearch API",
        "status": status,
        "name": _clean(item.get("Title") or item.get("Name")),
        "first_name": _clean(item.get("FirstName")),
        "middle_name": _clean(item.get("MiddleName")),
        "last_name": _clean(item.get("LastName")),
        "title": _clean(item.get("ProfessionalTitle")),
        "department": department_name,
        "email": _clean(item.get("Email")),
        "phone": _clean(item.get("PhoneNumber")),
        "office_address": _clean(item.get("CampusAddress")),
        "profile_url": profile_url,
        "description": _clean(item.get("Biography")),
        "publication_date": _clean(item.get("PublicationDate")),
        "last_modified": _clean(item.get("LastModified")),
        "profile_text": "",
        "profile_status": "",
        "department_hint": department or "",
        "raw_record": item,
    }


def _chico_term_code(term):
    """Resolve a supplied Chico PeopleSoft term label/code."""
    value = _clean(term)
    if re.fullmatch(r"\d{4}", value):
        return value
    known_terms = {
        "fall 2026": "2268",
        "spring 2027": "2272",
        "spring 2026": "2262",
        "summer 2026": "2265",
        "fall 2025": "2258",
        "spring 2025": "2252",
    }
    code = known_terms.get(value.casefold())
    if not code:
        raise ValueError(
            f"Could not map Chico State term '{term}' to a PeopleSoft term code. "
            "Enter the 4-digit term code or add the verified term mapping."
        )
    return code


def _chico_time(value):
    value = _clean(value)
    match = re.match(r"^(\d{1,2})\.(\d{2})\.\d{2}", value)
    if not match:
        return value
    hour, minute = int(match.group(1)), int(match.group(2))
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"


def _search_chico_courses(schedule_url, term, subject, course_code, srn, instructor, timeout):
    """Search Chico State's observed PeopleSoft HighPoint class-search JSON endpoint.

    The endpoint is called from a browser page on the same origin so the request
    uses the portal's session cookies, just like the request observed in DevTools.
    """
    import json
    from urllib.parse import urlparse, urlunparse, urlencode

    subject_code = _clean(subject).split()[0].upper()
    number = _clean(course_code)
    if not subject_code:
        raise ValueError("Chico State course search requires a subject code.")
    if not re.fullmatch(r"[A-Z0-9]+", subject_code):
        raise ValueError(f"Invalid Chico State subject code: {subject_code!r}")

    default_url = (
        "https://cmsweb.csuchico.edu/psc/CCHIPRD/EMPLOYEE/SA/s/"
        "WEBLIB_HCX_CM.H_CLASS_SEARCH.FieldFormula.IScript_Main"
    )
    parsed = urlparse(schedule_url or default_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Enter Chico State's official Class Schedule URL.")
    root = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    endpoint = root + "/psc/CCHIPRD/EMPLOYEE/SA/s/WEBLIB_HCX_CM.H_CLASS_SEARCH.FieldFormula.IScript_ClassSearch"
    term_code = _chico_term_code(term)

    rows, evidence_urls, context_records = [], [], []
    page_num, page_count, status = 1, 1, ""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
        )
        page = context.new_page()
        try:
            page.goto(schedule_url or default_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            while page_num <= page_count:
                params = {
                    "institution": "CHICO",
                    "term": term_code,
                    "subject": subject_code,
                    "catalog_nbr": number,
                    "enrl_stat": "",
                    "crse_attr": "",
                    "crse_attr_value": "",
                    "page": page_num,
                }
                request_url = endpoint + "?" + urlencode(params)
                result = page.evaluate(
                    """async (url) => {
                        const response = await fetch(url, {
                            method: 'GET',
                            credentials: 'include',
                            headers: { 'Accept': 'application/json, text/plain, */*' }
                        });
                        return {
                            status: response.status,
                            contentType: response.headers.get('content-type') || '',
                            text: await response.text()
                        };
                    }""",
                    request_url,
                )
                status = result.get("status", "")
                body = result.get("text", "") or ""
                evidence_urls.append(request_url)
                if not 200 <= int(status or 0) < 300:
                    raise RuntimeError(
                        f"Chico State schedule request returned HTTP {status}. "
                        f"Response preview: {_clip(body, '', 500)}"
                    )
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError as exc:
                    content_type = result.get("contentType", "unknown")
                    preview = re.sub(r"\s+", " ", body[:500]).strip()
                    raise RuntimeError(
                        "Chico State schedule endpoint did not return JSON. "
                        f"Content-Type: {content_type}. Response preview: {preview or '[empty response]'}. "
                        "The portal may require a valid browser session or may have returned an error page."
                    ) from exc
                if not isinstance(payload, dict):
                    raise RuntimeError("Chico State schedule endpoint returned an unexpected JSON structure.")

                page_count = max(1, int(payload.get("pageCount") or 1))
                classes = payload.get("classes") or []
                for item in classes:
                    class_number = _clean(item.get("class_nbr"))
                    section = _clean(item.get("class_section"))
                    instructors = item.get("instructors") or []
                    instructor_names = [
                        _clean(person.get("name")) for person in instructors
                        if _clean(person.get("name"))
                    ]
                    instructor_emails = [
                        _clean(person.get("email")) for person in instructors
                        if _clean(person.get("email"))
                    ]
                    faculty = "; ".join(instructor_names)
                    if srn and str(srn).strip() not in {class_number, section}:
                        continue
                    if instructor and instructor.casefold() not in faculty.casefold():
                        continue

                    meetings, meeting_locations, meeting_dates = [], [], []
                    for meeting in item.get("meetings") or []:
                        days = _clean(meeting.get("days"))
                        start_time = _chico_time(meeting.get("start_time"))
                        end_time = _chico_time(meeting.get("end_time"))
                        time_text = " - ".join(x for x in (start_time, end_time) if x)
                        meetings.append(" ".join(x for x in (days, time_text) if x))
                        location = " ".join(x for x in (
                            _clean(meeting.get("facility_descr")), _clean(meeting.get("room"))
                        ) if x and x != "***")
                        if location:
                            meeting_locations.append(location)
                        dates = " - ".join(x for x in (
                            _clean(meeting.get("start_dt")), _clean(meeting.get("end_dt"))
                        ) if x)
                        if dates:
                            meeting_dates.append(dates)

                    row = _blank_row(COURSE_COLUMNS)
                    row["Course Subject / Department Name"] = _clean(item.get("subject_descr")) or subject_code
                    row["Course Discipline"] = _clean(item.get("subject")) or subject_code
                    row["Course Catalog Code"] = _clean(item.get("catalog_nbr")) or number
                    row["Section ID"] = section
                    row["Course ID"] = class_number
                    row["Course Name"] = _clean(item.get("descr"))
                    row["Course Type"] = _clean(item.get("section_type")) or _clean(item.get("component"))
                    row["Course Modality"] = _clean(item.get("instruction_mode_descr"))
                    row["Course Enrollment Capacity"] = _clean(item.get("class_capacity"))
                    row["Course Actual Enrollment"] = _clean(item.get("enrollment_total"))
                    row["Course Waitlist Capacity"] = _clean(item.get("wait_cap"))
                    row["Course Waitlist Enrollment"] = _clean(item.get("wait_tot"))
                    row["Course Meeting Location"] = "; ".join(dict.fromkeys(meeting_locations))
                    row["Credit Hours"] = _clean(item.get("units"))
                    row["Grading Method"] = _clean(item.get("grading_basis"))
                    row["Course Date Start"] = _clean(item.get("start_dt"))
                    row["Course Date End"] = _clean(item.get("end_dt"))
                    row["Course Meeting Times"] = "; ".join(x for x in meetings if x)
                    if "Course Description" in row:
                        row["Course Description"] = _clean(item.get("topic"))
                    if "Course Status" in row:
                        row["Course Status"] = _clean(item.get("enrl_stat_descr"))
                    row["Instructor"] = faculty
                    rows.append(row)
                    context_records.append({
                        "section": section,
                        "class_number": class_number,
                        "title": row["Course Name"],
                        "instructor": faculty,
                        "emails": "; ".join(instructor_emails),
                        "meeting": row["Course Meeting Times"],
                    })
                page_num += 1
        finally:
            browser.close()

    rows = _dedupe_courses(rows)
    if not rows:
        raise RuntimeError(
            f"Chico State schedule API returned no matching sections for "
            f"{subject_code} {number or '(all courses)'} ({term}). Check the term, course number, "
            "section/CRN and instructor filters."
        )

    context_parts = [
        "COLLEGE: California State University, Chico", f"TERM: {term} ({term_code})",
        f"SUBJECT: {subject_code}", f"COURSE: {number or 'ALL COURSES'}",
    ]
    for item in context_records:
        context_parts.extend([
            f"SECTION: {item['section']}", f"CLASS NUMBER: {item['class_number']}",
            f"TITLE: {item['title']}", f"INSTRUCTOR: {item['instructor']}",
            f"MEETING: {item['meeting']}", f"EMAILS: {item['emails']}", "",
        ])
    return {
        "course": rows,
        "context": "\n".join(context_parts),
        "url": evidence_urls[0] if evidence_urls else endpoint,
        "title": "California State University, Chico Class Schedule API",
        "status": status,
    }


def _search_chico_instructor(name, department="", timeout=30):
    """Search the official Chico State Faculty/Staff directory and open its profile.

    The directory's observed search result uses #tblEmployee with columns for
    name, email, phone and department. Match first/last-name tokens without
    requiring the same display order (e.g. 'Kathleen Isenegger' vs
    'Isenegger, Kathleen').
    """
    from urllib.parse import urljoin

    query = _clean(name)
    if not query:
        raise ValueError("Chico State instructor search requires an instructor name.")

    base_url = "https://apps.csuchico.edu/directory/"
    target_tokens = [
        token.casefold() for token in re.findall(r"[A-Za-z]+", query)
    ]
    if len(target_tokens) < 2:
        raise ValueError("Enter at least the instructor's first and last name.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            directory_response = page.goto(
                base_url, wait_until="domcontentloaded", timeout=timeout * 1000
            )
            page.locator("#SearchType").select_option(label="Faculty/Staff")
            page.locator("#SearchText").fill(query)

            # Submit the observed official form. The search results page
            # contains the employee row in #tblEmployee.
            page.locator(
                'form[action="/directory/Search/Search"] input[type="submit"]'
            ).click()
            try:
                page.wait_for_load_state("domcontentloaded", timeout=timeout * 1000)
            except Exception:
                pass
            try:
                page.locator("#tblEmployee tbody tr").first.wait_for(
                    state="attached", timeout=timeout * 1000
                )
            except Exception:
                pass

            candidates = []
            rows = page.locator("#tblEmployee tbody tr")
            for i in range(rows.count()):
                tr = rows.nth(i)
                cells = tr.locator("td")
                if cells.count() < 4:
                    continue
                name_link = cells.nth(0).locator("a").first
                label = _clean(name_link.inner_text()) if name_link.count() else _clean(cells.nth(0).inner_text())
                href = name_link.get_attribute("href") if name_link.count() else ""
                label_tokens = [
                    token.casefold() for token in re.findall(r"[A-Za-z]+", label)
                ]
                if not href or not all(token in label_tokens for token in target_tokens):
                    continue

                email_link = cells.nth(1).locator("a").first
                email = (
                    _clean(email_link.inner_text())
                    if email_link.count()
                    else _clean(cells.nth(1).inner_text())
                )
                phone = _clean(cells.nth(2).inner_text())
                department_cell = cells.nth(3)
                department_label = _clean(department_cell.inner_text())
                candidates.append({
                    "name": label,
                    "profile_url": urljoin(base_url, href),
                    "email": email,
                    "phone": phone,
                    "department": department_label,
                })

            if len(candidates) != 1:
                detail = _clean(page.locator("body").inner_text())[-1000:]
                raise RuntimeError(
                    f"Chico State Faculty/Staff search returned {len(candidates)} "
                    f"matching profiles for '{query}'. Expected exactly one. "
                    f"Search page: {page.url}. Result detail: {detail}"
                )

            match = candidates[0]
            profile_response = page.goto(
                match["profile_url"],
                wait_until="domcontentloaded",
                timeout=timeout * 1000,
            )
            body_text = page.locator("body").inner_text()
            profile_text = _clean(body_text)

            email_match = re.search(
                r"[A-Z0-9._%+-]+@(?:csuchico\.edu|[A-Z0-9.-]+\.[A-Z]{2,})",
                profile_text,
                re.I,
            )
            phone_match = re.search(
                r"(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}",
                profile_text,
            )

            def label_value(labels):
                """Read profile label/value pairs without swallowing adjacent lines.

                Chico's profile uses a two-column HTML table. A regex using \\s*
                can cross the newline between the label and value and backtrack
                into the value (for example, returning only the final 's' from
                'Computer Science Department'). Prefer the actual DOM structure.
                """
                wanted = {
                    re.sub(r"[^a-z0-9]+", " ", str(label).casefold()).strip()
                    for label in labels
                }

                # First: explicit table rows (the layout used by Chico profiles).
                try:
                    rows_data = page.locator("tr").evaluate_all(
                        """rows => rows.map(row =>
                            Array.from(row.querySelectorAll("th, td"))
                                .map(cell => (cell.innerText || cell.textContent || "")
                                    .replace(/\\s+/g, " ").trim())
                                .filter(Boolean)
                        )"""
                    )
                    for cells in rows_data:
                        if len(cells) < 2:
                            continue
                        key = re.sub(r"[^a-z0-9]+", " ", cells[0].casefold()).strip().rstrip(":")
                        if key in wanted:
                            value = _clean(" ".join(cells[1:]))
                            if value:
                                return value
                except Exception:
                    pass

                # Second: definition-list layouts used by some directory templates.
                try:
                    pairs = page.locator("dt").evaluate_all(
                        """nodes => nodes.map(dt => ({
                            label: (dt.innerText || dt.textContent || "").trim(),
                            value: dt.nextElementSibling
                                ? (dt.nextElementSibling.innerText || dt.nextElementSibling.textContent || "").trim()
                                : ""
                        }))"""
                    )
                    for pair in pairs:
                        key = re.sub(
                            r"[^a-z0-9]+", " ", pair.get("label", "").casefold()
                        ).strip().rstrip(":")
                        if key in wanted and _clean(pair.get("value")):
                            return _clean(pair["value"])
                except Exception:
                    pass

                # Third: line-oriented text fallback. Do not use \\s* here:
                # it matches newlines and can consume the value itself.
                body_lines = [_clean(line) for line in body_text.splitlines() if _clean(line)]
                for index, line in enumerate(body_lines):
                    normalized = re.sub(r"[^a-z0-9]+", " ", line.casefold()).strip()
                    if normalized.rstrip(":") in wanted:
                        if index + 1 < len(body_lines):
                            return body_lines[index + 1]
                    for label in labels:
                        match = re.match(
                            rf"^{re.escape(label)}[ \\t]*:[ \\t]*(.+)$",
                            line,
                            re.I,
                        )
                        if match and _clean(match.group(1)):
                            return _clean(match.group(1))
                        match = re.match(
                            rf"^{re.escape(label)}[ \\t]+(.+)$",
                            line,
                            re.I,
                        )
                        if match and _clean(match.group(1)):
                            return _clean(match.group(1))
                return ""

            title = label_value(["Title", "Position", "Job Title", "Academic Rank"])
            profile_department = label_value(["Department", "Academic Department"])
            office = label_value(["Office", "Office Location", "Campus Address", "Location"])

            return {
                "name": match["name"],
                "title": title,
                "department": profile_department or match["department"] or department or "",
                "email": email_match.group(0) if email_match else match["email"],
                "phone": phone_match.group(0) if phone_match else match["phone"],
                "office": office,
                "profile_url": match["profile_url"],
                "directory_url": page.url,
                "directory_status": directory_response.status if directory_response else "",
                "profile_status": profile_response.status if profile_response else "",
                "profile_text": profile_text,
            }
        finally:
            browser.close()



def _parse_uncw_json_records(
    records, subject_code="", course_code="", crn_filter="", instructor_filter=""
):
    """Map structured UNCW Banner section JSON into the existing course schema."""
    import html

    rows = []
    day_names = (
        ("sunday", "Sun"), ("monday", "Mon"), ("tuesday", "Tue"),
        ("wednesday", "Wed"), ("thursday", "Thu"),
        ("friday", "Fri"), ("saturday", "Sat"),
    )

    def clock(value):
        value = _clean(value)
        if not value:
            return ""
        digits = re.sub(r"[^0-9]", "", value).zfill(4)[-4:]
        try:
            hour, minute = int(digits[:2]), int(digits[2:])
            suffix = "AM" if hour < 12 else "PM"
            hour12 = hour % 12 or 12
            return f"{hour12}:{minute:02d} {suffix}"
        except Exception:
            return value

    for item in records or []:
        if not isinstance(item, dict):
            continue
        number = _clean(item.get("courseNumber") or item.get("courseDisplay"))
        code = _clean(item.get("subject") or subject_code)
        crn = _clean(item.get("courseReferenceNumber"))
        if course_code and number.casefold() != str(course_code).strip().casefold():
            continue
        if subject_code and code.casefold() != str(subject_code).strip().casefold():
            continue
        if crn_filter and crn.casefold() != str(crn_filter).strip().casefold():
            continue

        faculty = item.get("faculty") or []
        names = [_clean(person.get("displayName")) for person in faculty
                 if isinstance(person, dict) and _clean(person.get("displayName"))]
        emails = [_clean(person.get("emailAddress")) for person in faculty
                  if isinstance(person, dict) and _clean(person.get("emailAddress"))]
        if instructor_filter:
            instructor_text = " ".join(names).casefold()
            requested_name = instructor_filter.casefold().replace(",", " ")
            name_tokens = [token for token in requested_name.split() if token]
            if not all(token in instructor_text for token in name_tokens):
                continue

        meeting_text = []
        locations = []
        start_dates = []
        end_dates = []
        for meeting in item.get("meetingsFaculty") or []:
            if not isinstance(meeting, dict):
                continue
            mt = meeting.get("meetingTime") or {}
            if not isinstance(mt, dict):
                continue
            days = [label for key, label in day_names if mt.get(key) is True]
            start_time, end_time = clock(mt.get("beginTime")), clock(mt.get("endTime"))
            time_part = " - ".join(x for x in (start_time, end_time) if x)
            line = " ".join(x for x in (", ".join(days), time_part) if x)
            if line:
                meeting_text.append(line)
            building = _clean(mt.get("buildingDescription") or mt.get("building"))
            room = _clean(mt.get("room"))
            location = " ".join(x for x in (building, room) if x)
            if location and location not in locations:
                locations.append(location)
            start_date, end_date = _clean(mt.get("startDate")), _clean(mt.get("endDate"))
            if start_date and start_date not in start_dates:
                start_dates.append(start_date)
            if end_date and end_date not in end_dates:
                end_dates.append(end_date)

        campus = _clean(item.get("campusDescription"))
        instructional = _clean(item.get("instructionalMethodDescription") or item.get("scheduleTypeDescription"))
        location_text = "; ".join(locations)
        online = ("online" in instructional.casefold() or "online" in location_text.casefold()
                  or "web" in instructional.casefold())

        row = _blank_row(COURSE_COLUMNS)
        row["Course Subject / Department Name"] = _clean(item.get("subjectDescription") or code)
        row["Course Discipline"] = code
        row["Campus"] = campus
        row["Course Catalog Code"] = number
        row["Section ID"] = _clean(item.get("sequenceNumber"))
        row["Course ID"] = crn
        row["Course Name"] = _clean(item.get("courseTitle"))
        row["Course Type"] = _clean(item.get("scheduleTypeDescription"))
        row["Course Modality"] = "Online" if online else ("In-Person" if location_text else instructional)
        row["Course Enrollment Capacity"] = _clean(item.get("maximumEnrollment"))
        row["Course Actual Enrollment"] = _clean(item.get("enrollment"))
        row["Course Waitlist Capacity"] = _clean(item.get("waitCapacity"))
        row["Course Waitlist Enrollment"] = _clean(item.get("waitCount"))
        row["Course Meeting Location"] = location_text or ("Online" if online else "")
        row["Credit Hours"] = _clean(item.get("creditHourLow") or item.get("creditHours"))
        row["Course Date Start"] = "; ".join(start_dates)
        row["Course Date End"] = "; ".join(end_dates)
        row["Course Meeting Times"] = "; ".join(dict.fromkeys(meeting_text))

        attributes = []
        for attr in item.get("sectionAttributes") or []:
            if isinstance(attr, dict):
                code_value = html.unescape(_clean(attr.get("code")))
                desc_value = html.unescape(_clean(attr.get("description")))
                value = " - ".join(x for x in (code_value, desc_value) if x)
                if value:
                    attributes.append(value)
        details = []
        if item.get("termDesc"):
            details.append(f"Term: {_clean(item.get('termDesc'))}")
        if item.get("instructionalMethodDescription"):
            details.append(f"Instructional method: {_clean(item.get('instructionalMethodDescription'))}")
        if attributes:
            details.append("Section attributes: " + "; ".join(attributes))
        if details:
            row["Course Description"] = "\n".join(details)

        # Optional QA fields are intentionally added per row; the shared schema
        # is not modified, so other college exports remain unaffected.
        row["Instructor"] = "; ".join(names)
        row["Instructor Email"] = "; ".join(emails)
        rows.append(row)

    return _dedupe_courses(rows)

class Researcher:
    def __init__(self, timeout=30, use_browser=False):
        self.timeout = timeout
        self.use_browser = use_browser

    
    def research_course(
        self,
        college,
        term,
        schedule_url,
        srn,
        course_code,
        subject,
        instructor,
    ):
        if not self.use_browser:
            raise RuntimeError(
                "Enable 'Use Playwright for JavaScript pages' "
                "in the sidebar before searching."
            )

        is_kirkwood = (
            "selfservice.kirkwood.edu" in schedule_url.lower()
        )
        is_cmich = (
            "courseregistration.apps.cmich.edu" in (schedule_url or "").lower()
            or "central michigan university" in (college or "").casefold()
        )

        if is_cmich:
            result = _search_cmich_courses(
                schedule_url=schedule_url,
                term=term,
                subject=subject,
                course_code=course_code,
                srn=srn,
                instructor=instructor,
                timeout=self.timeout,
            )
            evidence = [{
                "Source URL": result["url"],
                "Source title": result["title"],
                "HTTP status": result["status"],
                "Fields supported / review": (
                    "Course title, subject, course number, section/CRN, credits, "
                    "instructor, meeting days/times, dates, location, modality and seats."
                ),
                "Evidence note": (
                    f"Official CMU Course Search and Registration results; "
                    f"{len(result['course'])} matching section(s) extracted for {term}."
                ),
            }]
            return {
                "course": result["course"],
                "evidence": evidence,
                "context": result["context"],
            }

        if is_kirkwood:
            result = _search_kirkwood_courses(
                schedule_url=schedule_url,
                term=term,
                subject=subject,
                course_code=course_code,
                srn=srn,
                instructor=instructor,
                timeout=self.timeout,
            )

            evidence = [{
                "Source URL": result["url"],
                "Source title": result["title"],
                "HTTP status": result["status"],
                "Fields supported / review": (
                    "Course title, description, credits, section, "
                    "instructor, dates, meeting times, location, "
                    "enrollment and waitlist."
                ),
                "Evidence note": (
                    f"Official Kirkwood sections API. "
                    f"{len(result['course'])} matching section(s) "
                    f"retrieved for {term}."
                ),
            }]

            return {
                "course": result["course"],
                "evidence": evidence,
                "context": result["context"],
            }

        is_chico = (
            "csuchico.edu" in (schedule_url or "").casefold()
            or "california state university, chico" in (college or "").casefold()
            or "chico state" in (college or "").casefold()
        )
        if is_chico:
            result = _search_chico_courses(
                schedule_url=schedule_url, term=term, subject=subject,
                course_code=course_code, srn=srn, instructor=instructor,
                timeout=self.timeout,
            )
            evidence = [{
                "Source URL": result["url"],
                "Source title": result["title"],
                "HTTP status": result["status"],
                "Fields supported / review": (
                    "Course title, subject, catalog number, section, class number, "
                    "credits, grading basis, instructor names, meeting days/times, "
                    "dates, modality, enrollment, waitlist and capacity."
                ),
                "Evidence note": (
                    f"Official Chico State PeopleSoft ClassSearch JSON endpoint; "
                    f"{len(result['course'])} matching section(s) extracted for {term}."
                ),
            }]
            return {"course": result["course"], "evidence": evidence, "context": result["context"]}

        # Existing UNCW and generic portal workflow remains unchanged.
        result = search_banner_classes(
            schedule_url, term, subject, course_code, self.timeout
        )

        text = result.get("text", "") or ""
        tables = result.get("tables", []) or []

        if result.get("platform") == "scccd_self_service":
            course_rows = []
            for item in result.get("records", []):
                if srn and str(item.get("class_number", "")).strip() != str(srn).strip():
                    continue
                row = _blank_row(COURSE_COLUMNS)
                row["Course Subject / Department Name"] = subject
                row["Course Discipline"] = subject
                row["Course Catalog Code"] = str(course_code).strip()
                row["Course ID"] = item.get("class_number", "")
                row["Section ID"] = item.get("section", "")
                row["Course Name"] = item.get("course_title", "")
                row["Course Date Start"] = item.get("date_start", item.get("dates", ""))
                if "Course Date End" in row:
                    row["Course Date End"] = item.get("date_end", "")
                row["Course Meeting Location"] = item.get("location", "")
                row["Course Modality"] = item.get("instructional_method", "")
                row["Course Meeting Times"] = item.get("meeting_information", "")
                optional_fields = {
                    "Course Description": item.get("description", ""),
                    "Credit Hours": item.get("credits", ""),
                    "Course Enrollment Capacity": item.get("capacity", ""),
                    "Course Actual Enrollment": item.get("enrollment", ""),
                    "Course Waitlist Capacity": item.get("waitlist_capacity", ""),
                    "Course Waitlist Enrollment": item.get("waitlist_total", ""),
                    "Instructor": item.get("instructor", ""),
                    "Course Status": item.get("status", ""),
                }
                for column, value in optional_fields.items():
                    if column in row:
                        row[column] = value
                # Keep the exact section/CRN and other detail fields in the QA row.
                if "Course Meeting Dates" in row:
                    row["Course Meeting Dates"] = item.get("dates", "")
                course_rows.append(row)
            course_rows = _dedupe_courses(course_rows)

        elif result.get("platform") == "glendale_peoplesoft":
            course_rows = []
            for item in result.get("records", []):
                if srn and str(item.get("class_number", "")).strip() != str(srn).strip():
                    continue
                if instructor and instructor.casefold() not in str(item.get("instructor", "")).casefold():
                    continue

                row = _blank_row(COURSE_COLUMNS)
                row["Course Subject / Department Name"] = subject
                row["Course Discipline"] = subject
                row["Course Catalog Code"] = str(course_code).strip()
                row["Course ID"] = item.get("class_number", "")
                row["Section ID"] = item.get("section", "")
                row["Course Name"] = item.get("course_title", "")
                row["Course Meeting Times"] = item.get("meeting_time", "")
                row["Course Meeting Location"] = item.get("room", "") or item.get("location", "")
                row["Course Modality"] = (
                    "Online" if "online" in str(row["Course Meeting Location"]).casefold()
                    else ("In-Person" if row["Course Meeting Location"] else "")
                )
                optional_fields = {
                    "Credit Hours": item.get("credits", ""),
                    "Course Enrollment Capacity": item.get("capacity", ""),
                    "Course Actual Enrollment": item.get("enrollment", ""),
                    "Course Waitlist Capacity": item.get("waitlist_capacity", ""),
                    "Course Waitlist Enrollment": item.get("waitlist_total", ""),
                    "Instructor": item.get("instructor", ""),
                }
                for column, value in optional_fields.items():
                    if column in row:
                        row[column] = value

                # The shared workbook schema has one description field rather
                # than separate columns for PeopleSoft detail-page metadata.
                # Preserve all Glendale-only detail fields there instead of
                # changing the global schema or affecting other colleges.
                description_parts = []
                if item.get("description"):
                    description_parts.append(f"Description: {item['description']}")
                for label, key in (
                    ("Add Consent", "add_consent"),
                    ("Enrollment Requirements", "enrollment_requirements"),
                    ("Class Attributes", "class_attributes"),
                    ("Enrollment Information", "enrollment_information"),
                    ("Class Availability", "class_availability"),
                    ("Combined Section", "combined_section_information"),
                    ("Notes", "class_notes"),
                    ("Textbook/Other Materials", "textbook_materials"),
                ):
                    value = _clean(item.get(key, ""))
                    if value:
                        description_parts.append(f"{label}: {value}")
                full_detail_text = _clean(item.get("detail_text", ""))
                if full_detail_text:
                    description_parts.append(f"Full Class Detail Page: {full_detail_text}")
                if "Course Description" in row:
                    row["Course Description"] = "\n".join(description_parts)

                if "Course Meeting Dates" in row:
                    row["Course Meeting Dates"] = item.get("meeting_dates", "")
                if "Course Status" in row:
                    row["Course Status"] = item.get("status", "")
                course_rows.append(row)
            course_rows = _dedupe_courses(course_rows)

        elif (
            result.get("platform") == "uncw_banner"
            or "registration.uncw.edu" in result.get("url", "")
        ):
            # Prefer the structured Banner response; retain the old rendered
            # table parser as a fallback for portal/UI variations.
            course_rows = _parse_uncw_json_records(
                result.get("records", []),
                subject_code=subject,
                course_code=course_code,
                crn_filter=srn,
                instructor_filter=instructor,
            )
            if not course_rows:
                course_rows = _parse_uncw_course_records(
                    text,
                    subject_code=subject,
                    course_code=course_code,
                    crn_filter=srn,
                    instructor_filter=instructor,
                )

            if not course_rows and tables:
                course_rows = _parse_uncw_course_records(
                    "\n\n".join(tables),
                    subject_code=subject,
                    course_code=course_code,
                    crn_filter=srn,
                    instructor_filter=instructor,
                )
        else:
            course_rows = _parse_generic_course_records(
                [text] + tables,
                subject=subject,
                course_code=course_code,
                crn_filter=srn,
                instructor_filter=instructor,
            )

        if not course_rows:
            if result.get("platform") == "scccd_self_service":
                raise RuntimeError(
                    "Reedley/SCCCD search returned results, but no Section Name rows "
                    "were extracted. Confirm that Section Listing is selected and "
                    "that the Reedley College location filter is applied."
                )
            raise RuntimeError(
                "The schedule search returned page content, but the "
                "course table could not be mapped safely."
            )

        context = (
            f"COLLEGE: {college}\nTERM: {term}\nSUBJECT: {subject}\n"
            f"COURSE NUMBER: {course_code}\nCRN FILTER: {srn}\n"
            f"INSTRUCTOR FILTER: {instructor}\n\n"
            f"PLATFORM: {result.get('platform', 'generic')}\n"
            f"SEARCH RESULTS:\n{text}\n\n"
            f"TABLES:\n" + "\n\n".join(tables)
        )
        if result.get("platform") == "glendale_peoplesoft":
            detail_blocks = []
            for item in result.get("records", []):
                detail_blocks.append(
                    "\n".join([
                        f"CLASS NUMBER: {item.get('class_number', '')}",
                        f"SECTION: {item.get('section', '')}",
                        f"INSTRUCTOR: {item.get('instructor', '')}",
                        f"ADD CONSENT: {item.get('add_consent', '')}",
                        f"ENROLLMENT REQUIREMENTS: {item.get('enrollment_requirements', '')}",
                        f"CLASS ATTRIBUTES: {item.get('class_attributes', '')}",
                        f"ENROLLMENT INFORMATION: {item.get('enrollment_information', '')}",
                        f"CLASS AVAILABILITY: {item.get('class_availability', '')}",
                        f"COMBINED SECTION: {item.get('combined_section_information', '')}",
                        f"NOTES: {item.get('class_notes', '')}",
                        f"DESCRIPTION: {item.get('description', '')}",
                        f"TEXTBOOK/OTHER MATERIALS: {item.get('textbook_materials', '')}",
                        f"DETAIL PAGE TEXT: {item.get('detail_text', '')}",
                    ])
                )
            context += "\n\nGLENDALE CLASS DETAIL PAGES:\n" + "\n\n".join(detail_blocks)

        evidence = [
            _evidence(
                result,
                "Course title, subject, course number, section, credits, "
                "meeting times, location and enrollment where published.",
                f"Official schedule search for {term}; "
                f"{len(course_rows)} matching records extracted.",
            )
        ]

        return {
            "course": course_rows,
            "evidence": evidence,
            "context": _clip(
                context, srn or course_code or instructor, 20000
            ),
        }

    
    def research_instructor(
        self, college, name, department, faculty_url,
        department_url, division_url, primary, schedule_url=""
    ):
        
        
        is_chico = (
            "california state university, chico" in (college or "").casefold()
            or "chico state" in (college or "").casefold()
            or "csuchico.edu" in (faculty_url or "").casefold()
            or "apps.csuchico.edu" in (department_url or "").casefold()
        )
        if is_chico:
            result = _search_chico_instructor(
                name=name, department=department, timeout=self.timeout
            )
            row = _blank_row(INSTRUCTOR_COLUMNS)
            row["Instructor #01 Is Primary"] = "" if primary == "Unknown" else primary
            row["Instructor #01 Title"] = result["title"]
            row["Instructor #01 Department"] = result["department"]
            row["Instructor #01 Email"] = result["email"]
            row["Instructor #01 Phone"] = result["phone"]
            row["Instructor #01 Office Address"] = result["office"]
            context = (
                f"COLLEGE: {college}\nINSTRUCTOR: {result['name']}\n"
                f"TITLE: {result['title']}\nDEPARTMENT: {result['department']}\n"
                f"EMAIL: {result['email']}\nPHONE: {result['phone']}\n"
                f"OFFICE: {result['office']}\nPROFILE URL: {result['profile_url']}\n"
                f"PROFILE TEXT: {result['profile_text']}"
            )
            evidence = [{
                "Source URL": result["profile_url"],
                "Source title": "CSU Chico Campus Directory - Faculty/Staff Profile",
                "HTTP status": result["profile_status"],
                "Fields supported / review": (
                    "Faculty/staff profile, title, department, publicly listed email, "
                    "phone and office details where present."
                ),
                "Evidence note": (
                    f"Official Chico State Faculty/Staff directory search for '{name}'. "
                    "Profile fields are extracted only when displayed on the profile page."
                ),
            }]
            return {"instructor": row, "evidence": evidence, "context": context}

        if "central michigan university" in (college or "").casefold() or "cmich.edu" in (faculty_url or "").casefold():
            result = _search_cmich_instructor(
                name=name,
                department=department,
                faculty_url=faculty_url,
                timeout=self.timeout,
            )
            row = _blank_row(INSTRUCTOR_COLUMNS)
            row["Instructor #01 Is Primary"] = (
                "" if primary == "Unknown" else primary
            )
            row["Instructor #01 Title"] = result["title"]
            row["Instructor #01 Department"] = result["department"] or department or ""
            row["Instructor #01 Email"] = result["email"]
            row["Instructor #01 Phone"] = result["phone"]
            row["Instructor #01 Office Address"] = result["office_address"]

            context = (
                f"COLLEGE: {college}\n"
                f"INSTRUCTOR: {result['name']}\n"
                f"FIRST NAME: {result['first_name']}\n"
                f"MIDDLE NAME: {result['middle_name']}\n"
                f"LAST NAME: {result['last_name']}\n"
                f"TITLE: {result['title']}\n"
                f"DEPARTMENT: {result['department']}\n"
                f"EMAIL: {result['email']}\n"
                f"PHONE: {result['phone']}\n"
                f"OFFICE: {result['office_address']}\n"
                f"PROFILE URL: {result['profile_url']}\n"
                f"BIOGRAPHY: {result['description']}\n"
                f"API LAST MODIFIED: {result['last_modified']}\n"
            )
            evidence = [{
                "Source URL": result["url"],
                "Source title": result.get("source_title", "CMU PeopleSearch API"),
                "HTTP status": result["status"],
                "Fields supported / review": (
                    "Instructor name, professional title, department, email, "
                    "phone, campus office address and profile link from the "
                    "official CMU PeopleSearch JSON response."
                ),
                "Evidence note": (
                    f"Official CMU PeopleSearch API query for '{name}'. "
                    f"Profile: {result['profile_url']}. "
                    f"Record last modified: {result['last_modified'] or 'not supplied'}."
                ),
            }]
            return {
                "instructor": row,
                "evidence": evidence,
                "context": context,
            }

        if "university of north carolina wilmington" in college.casefold() or "uncw" in college.casefold():
            result = _search_uncw_instructor(
                name=name,
                department=department,
                timeout=self.timeout
            )

            row = _blank_row(INSTRUCTOR_COLUMNS)
            row["Instructor #01 Is Primary"] = (
                "" if primary == "Unknown" else primary
            )

            if result["matches"]:
                match = result["matches"][0]
                row["Instructor #01 Title"] = match["title"]
                row["Instructor #01 Department"] = match["department"]
                row["Instructor #01 Phone"] = match["phone"]
                row["Instructor #01 Email"] = match["email"]
                context = (
                    f"COLLEGE: {college}\n"
                    f"INSTRUCTOR: {match['name']}\n"
                    f"TITLE: {match['title']}\n"
                    f"DEPARTMENT: {match['department']}\n"
                    f"PHONE: {match['phone']}\n"
                    f"EMAIL: {match['email']}\n"
                    f"CAMPUS BOX: {match['campus_box']}\n"
                )
                evidence_note = (
                    "Official UNCW People Search; Staff/Faculty filter used. "
                    "Exact first-name and last-name match found."
                )
            else:
                # No directory match: keep all researched instructor fields blank.
                context = (
                    f"COLLEGE: {college}\n"
                    f"INSTRUCTOR SEARCH: {name}\n"
                    "RESULT: No exact Staff/Faculty match found.\n"
                )
                evidence_note = (
                    "Official UNCW People Search was queried by first and last name; "
                    "no exact match was returned. Instructor detail fields left blank."
                )

            evidence = [{
                "Source URL": result["url"],
                "Source title": result["title"],
                "HTTP status": result["status"],
                "Fields supported / review": (
                    "Name, title, department, phone, email and campus box "
                    "when an exact directory match is returned."
                ),
                "Evidence note": evidence_note,
            }]

            return {
                "instructor": row,
                "evidence": evidence,
                "context": context,
            }

        import re

        is_kirkwood = "kirkwood" in (college or "").casefold()
        is_glendale = "glendale community college" in (college or "").casefold()

        # Public source roots for these two colleges. Glendale's online
        # directory is separate from the PeopleSoft schedule host; Kirkwood's
        # public academic-department pages are separate from Self-Service.
        if is_glendale:
            urls = [
                faculty_url,
                department_url,
                division_url,
                "https://www.glendale.edu/directory/index.html",
                "https://www.glendale.edu/about-gcc/faculty-and-staff/index.html",
                "https://www.glendale.edu/about-gcc/gcc-overview/inside-gcc/college-directory",
            ]
        else:
            urls = [faculty_url, department_url, division_url]
            if is_kirkwood:
                urls.extend([
                    "https://www.kirkwood.edu/about-us/faculty-leadership/",
                    "https://www.kirkwood.edu/about-us/faculty-leadership/academic-departments/",
                    "https://www.kirkwood.edu/",
                ])
                if (department or "").strip().upper().startswith("HUM"):
                    urls.append(
                        "https://www.kirkwood.edu/about-us/faculty-leadership/"
                        "academic-departments/arts-and-humanities/index"
                    )

        # Discover linked official pages even when one directory URL was
        # already supplied. The old condition skipped discovery whenever any
        # URL existed, which prevented Kirkwood's other public source pages
        # from being searched. Do not crawl unrelated external hosts.
        discovery_roots = list(urls)
        if schedule_url:
            discovery_roots.append(schedule_url)
        discovered = []
        try:
            from urllib.parse import urlparse
            root_hosts = set()
            for root in discovery_roots:
                host = (urlparse(str(root or "")).hostname or "").casefold()
                if host:
                    root_hosts.add(host)
                    if is_kirkwood and host.endswith("kirkwood.edu"):
                        root_hosts.add("www.kirkwood.edu")
                    if is_glendale and (host.endswith("glendale.edu") or host.endswith("glendale.cc.ca.us")):
                        root_hosts.update(("www.glendale.edu", "glendale.edu"))
            keywords = (
                "faculty", "staff", "directory", "people", "instructor",
                "department", "division", "academic", "program", "contact",
                "profile", "our team", "employees", "faculty-leadership",
            )
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                try:
                    for root in discovery_roots[:8]:
                        if not root:
                            continue
                        try:
                            page.goto(root, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                            links = page.locator("a[href]").evaluate_all(
                                "els => els.map(a => ({href:a.href, text:(a.innerText || a.textContent || '').trim(), aria:a.getAttribute('aria-label') || ''}))"
                            )
                            for link in links:
                                target = str(link.get("href", "")).split("#", 1)[0]
                                target_host = (urlparse(target).hostname or "").casefold()
                                label = " ".join((link.get("text", ""), link.get("aria", ""), target)).casefold()
                                if (target.startswith("http") and target_host in root_hosts
                                        and any(word in label for word in keywords)
                                        and target not in discovered):
                                    discovered.append(target)
                                if len(discovered) >= 30:
                                    break
                        except Exception:
                            continue
                        if len(discovered) >= 30:
                            break
                finally:
                    browser.close()
        except Exception:
            pass
        urls.extend(discovered)

        urls = list(dict.fromkeys(str(url).strip() for url in urls if str(url or "").strip()))
        pages = []
        evidence = []
        context_parts = [
            f"COLLEGE: {college}",
            f"INSTRUCTOR SEARCH: {name}",
        ]

        row = _blank_row(INSTRUCTOR_COLUMNS)
        row["Instructor #01 Is Primary"] = (
            "" if primary == "Unknown" else primary
        )
        row["Instructor #01 Department"] = department or ""

        def extract_label(text, labels):
            for label in labels:
                pattern = (
                    rf"{label}\s*[:\-]\s*"
                    rf"([^\n\r|;]+)"
                )
                match = re.search(
                    pattern, text, re.IGNORECASE
                )
                if match:
                    return match.group(1).strip()
            return ""

        def extract_from_context(text):
            values = {}

            email = re.search(
                r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
                text,
                re.IGNORECASE,
            )
            if email:
                values["Instructor #01 Email"] = email.group(0)

            phone = re.search(
                r"(?<!\d)(?:\+?1[\s.-]?)?"
                r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)",
                text,
            )
            if phone:
                values["Instructor #01 Phone"] = phone.group(0)

            title = extract_label(
                text,
                [
                    "Title",
                    "Job Title",
                    "Position",
                    "Academic Rank",
                    "Faculty Rank",
                ],
            )
            if title:
                values["Instructor #01 Title"] = title

            dept = extract_label(
                text,
                [
                    "Department",
                    "Academic Department",
                    "Program",
                ],
            )
            if dept:
                values["Instructor #01 Department"] = dept

            division = extract_label(
                text,
                [
                    "Academic Division",
                    "Division",
                    "School",
                    "College",
                ],
            )
            if division:
                values["Instructor #01 Academic Division"] = division

            office_address = extract_label(
                text,
                [
                    "Office Address",
                    "Office Location",
                    "Office",
                    "Campus Address",
                ],
            )
            if office_address:
                values["Instructor #01 Office Address"] = office_address

            building = extract_label(
                text,
                [
                    "Office Building",
                    "Building",
                ],
            )
            if building:
                values["Instructor #01 Office Building"] = building

            return values

        for url in urls:
            if not url or not url.strip():
                continue

            try:
                page = fetch_page(
                    url.strip(),
                    self.timeout,
                    self.use_browser,
                )
                pages.append(page)

                page_text = page.text or ""
                lines = page_text.splitlines()

                # Only extract fields from text surrounding
                # the requested instructor's exact name.
                def normalized_person(value):
                    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())

                requested_name = _clean(name)
                name_parts = [part for part in re.split(r"[\s,]+", requested_name) if part]
                requested_norm = normalized_person(requested_name)
                reversed_norm = normalized_person(" ".join(reversed(name_parts[:2]))) if len(name_parts) >= 2 else ""
                matching_lines = []
                for i, line in enumerate(lines):
                    line_norm = normalized_person(line)
                    if (requested_norm and requested_norm in line_norm) or (
                        reversed_norm and reversed_norm in line_norm
                    ) or (
                        len(name_parts) >= 2
                        and all(normalized_person(part) in line_norm for part in name_parts[:2])
                    ):
                        matching_lines.append(i)

                if matching_lines:
                    relevant_lines = set()

                    for index in matching_lines:
                        start = max(0, index - 8)
                        end = min(len(lines), index + 16)

                        relevant_lines.update(
                            range(start, end)
                        )

                    relevant_text = "\n".join(
                        lines[i] for i in sorted(relevant_lines)
                    )

                    extracted = extract_from_context(relevant_text)

                    # Glendale directory entries commonly render as
                    # "Last, First | email | extension | location | division".
                    # Capture these structured values without requiring labels.
                    if is_glendale:
                        email_match = re.search(
                            r"\b[A-Z0-9._%+-]+@glendale\.edu\b",
                            relevant_text, re.IGNORECASE
                        )
                        if email_match and not extracted.get("Instructor #01 Email"):
                            extracted["Instructor #01 Email"] = email_match.group(0)
                        phone_match = re.search(
                            r"(?:ext\.?\s*)?(\d{3,5})\b",
                            relevant_text, re.IGNORECASE
                        )
                        if phone_match and not extracted.get("Instructor #01 Phone"):
                            extracted["Instructor #01 Phone"] = phone_match.group(0)

                    for key, value in extracted.items():
                        if not row.get(key) and value:
                            row[key] = value

                    note = (
                        f"Requested instructor name found on source. "
                        f"Extracted fields: "
                        f"{', '.join(extracted.keys()) or 'None'}."
                    )
                else:
                    note = (
                        "Requested instructor name was not found "
                        "in the fetched page text. No profile fields "
                        "were extracted from this source."
                    )

                context_parts.append(
                    f"\nSOURCE: {page.title}"
                    f"\nURL: {page.url}"
                    f"\n{_clip(page_text, name, 5000)}"
                )

                evidence.append({
                    "Source URL": page.url,
                    "Source title": page.title,
                    "HTTP status": page.status,
                    "Fields supported / review": (
                        ", ".join(
                            key for key in row
                            if key != "Instructor #01 Is Primary"
                            and row.get(key)
                        ) or "No fields extracted"
                    ),
                    "Evidence note": note,
                })

            except Exception as exc:
                evidence.append({
                    "Source URL": url,
                    "Source title": "",
                    "HTTP status": "Fetch failed",
                    "Fields supported / review": "None",
                    "Evidence note": str(exc),
                })

        if not pages:
            raise RuntimeError(
                "Could not fetch any instructor source URL. "
                "Check the URLs and browser settings."
            )

        return {
            "instructor": row,
            "evidence": evidence,
            "context": "\n".join(context_parts),
        }
