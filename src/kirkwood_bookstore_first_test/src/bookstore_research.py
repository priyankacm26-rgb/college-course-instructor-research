"""Isolated browser inspection for the Kirkwood EagleShop course-material form.

This module deliberately does not modify the existing course/instructor fetcher.
It inspects the dynamic bookstore form so selectors can be implemented from
observed controls rather than guessed.
"""

import re
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


DEFAULT_KIRKWOOD_BOOKSTORE_URL = "https://shop.kirkwood.edu/buy_courselisting.asp"


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def inspect_kirkwood_bookstore_form(url=DEFAULT_KIRKWOOD_BOOKSTORE_URL,
                                    term="", timeout=60):
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Enter a valid bookstore URL beginning with https://")

    result = {
        "url": url,
        "title": "",
        "status": "",
        "term_requested": term,
        "term_selected": "",
        "selects": [],
        "buttons": [],
        "inputs": [],
        "page_text": "",
        "notes": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(timeout) * 1000,
            )
            page.wait_for_timeout(2500)
            result["title"] = page.title()
            result["status"] = str(response.status) if response else "No HTTP response"

            def read_controls():
                return page.evaluate("""() => {
                    const visible = (el) => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 &&
                               s.visibility !== 'hidden' && s.display !== 'none';
                    };
                    const labelFor = (el) => {
                        if (el.labels && el.labels.length) {
                            return Array.from(el.labels).map(x => x.innerText.trim()).join(' ');
                        }
                        const parent = el.closest('label');
                        if (parent) return parent.innerText.trim();
                        const id = el.id;
                        if (id) {
                            const lab = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                            if (lab) return lab.innerText.trim();
                        }
                        return '';
                    };
                    return {
                        selects: Array.from(document.querySelectorAll('select')).filter(visible).map((el, i) => ({
                            index: i, id: el.id, name: el.name,
                            label: labelFor(el),
                            value: el.value,
                            options: Array.from(el.options).map(o => ({
                                text: o.text.trim(), value: o.value,
                                disabled: o.disabled
                            })).filter(o => o.text || o.value)
                        })),
                        buttons: Array.from(document.querySelectorAll('button, input[type=submit], input[type=button]'))
                            .filter(visible).map(el => ({
                                text: (el.innerText || el.value || '').trim(),
                                id: el.id, name: el.name, type: el.type
                            })),
                        inputs: Array.from(document.querySelectorAll('input'))
                            .filter(visible).map(el => ({
                                type: el.type, id: el.id, name: el.name,
                                placeholder: el.placeholder, value: el.value,
                                label: labelFor(el)
                            }))
                    };
                }""")

            controls = read_controls()
            result.update(controls)

            if term:
                wanted = _norm(term)
                term_select_index = None
                term_option = None

                # Prefer a select whose label/options identify it as the term selector.
                for select_index, select in enumerate(result["selects"]):
                    label_blob = _norm(select.get("label", "") + " " + select.get("name", "") + " " + select.get("id", ""))
                    if "term" in label_blob or "semester" in label_blob:
                        for option in select["options"]:
                            if _norm(option["text"]) == wanted or wanted in _norm(option["text"]):
                                term_select_index = select_index
                                term_option = option
                                break
                    if term_option:
                        break

                # If labels are missing, look for the requested term in any select.
                if term_option is None:
                    for select_index, select in enumerate(result["selects"]):
                        for option in select["options"]:
                            if wanted in _norm(option["text"]):
                                term_select_index = select_index
                                term_option = option
                                break
                        if term_option:
                            break

                if term_option is None:
                    result["notes"].append(
                        f"Requested term '{term}' was not found in the currently loaded dropdown options."
                    )
                else:
                    target_select = page.locator("select:visible").nth(term_select_index)
                    target_select.select_option(value=term_option["value"])
                    page.wait_for_timeout(1800)
                    result["term_selected"] = term_option["text"]
                    result.update(read_controls())
                    result["notes"].append(
                        "Selected the requested term and captured the dependent dropdowns."
                    )

            result["page_text"] = page.locator("body").inner_text(timeout=10000)[:8000]
            return result
        finally:
            browser.close()
