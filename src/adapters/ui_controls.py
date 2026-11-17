
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def find_search_box(page):
    """Find a searchable course input using common portal patterns."""

    selectors = [
        'input[placeholder*="Search for courses" i]',
        'input[placeholder*="Search" i]',
        'input[aria-label*="Search" i]',
        'input[type="search"]',
        'input[type="text"]',
    ]

    for selector in selectors:
        locator = page.locator(selector).first

        try:
            if locator.is_visible(timeout=1500):
                return locator
        except PlaywrightTimeoutError:
            continue

    raise RuntimeError("Could not find a visible search input.")


def search_autocomplete(page, search_text, timeout=10000):
    """Type a search term and inspect autocomplete suggestions."""

    search_box = find_search_box(page)
    search_box.fill(search_text)

    page.wait_for_timeout(1500)

    suggestions = page.locator(
        '[role="option"], li, [class*="suggestion"], '
        '[class*="autocomplete"], [class*="dropdown"]'
    )

    results = []

    for item in suggestions.all():
        try:
            if item.is_visible():
                value = item.inner_text().strip()

                if value and value not in results:
                    results.append(value)
        except Exception:
            continue

    return {
        "search_text": search_text,
        "suggestions": results,
        "suggestion_count": len(results),
    }


def select_autocomplete(page, expected_text, timeout=10000):
    """Select a suggestion matching the requested text."""

    candidates = [
        '[role="option"]',
        'li',
        '[class*="suggestion"]',
        '[class*="autocomplete"]',
    ]

    for selector in candidates:
        items = page.locator(selector)

        for item in items.all():
            try:
                if not item.is_visible():
                    continue

                text = item.inner_text().strip()

                if expected_text.casefold() in text.casefold():
                    item.click(timeout=timeout)
                    return text

            except Exception:
                continue

    raise RuntimeError(
        f"No autocomplete suggestion matched: {expected_text}"
    )
