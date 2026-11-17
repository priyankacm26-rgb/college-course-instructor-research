"""Bounded discovery of official college class-schedule portals.

Searches the supplied college homepage and a small number of relevant pages
(e.g. Registrar, Enrollment, Registration). Links on external hosted systems
may be recorded as candidates, but are never marked confirmed automatically.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import re
import time

KNOWN_SCHEDULE_PORTALS = {
    "kirkwood community college": {
        "url": "https://selfservice.kirkwood.edu/Student/Courses",
        "platform": "Kirkwood Self-Service",
        "status": "KNOWN_WORKING",
    },
    "university of north carolina wilmington": {
        "url": "https://registration.uncw.edu/StudentRegistrationSsb",
        "platform": "UNCW Banner",
        "status": "KNOWN_WORKING",
    },
}
KEYWORDS = {
    # Strong course schedule indicators
    "class schedule": 18,
    "class search": 18,
    "search classes": 17,
    "search for classes": 18,
    "schedule of classes": 18,
    "course schedule": 18,
    "browse classes": 17,
    "course offerings": 17,
    "class listings": 16,
    "section search": 16,
    "term schedule": 16,
    "find courses": 16,
    "course search": 17,
    "schedule planner": 14,

    # Registration and student portals
    "register for classes": 14,
    "registration": 3,
    "enrollment": 2,
    "student self service": 12,
    "self service": 10,
    "student planning": 9,
    "my classes": 8,

    # Portal technologies
    "banner": 5,
    "peoplesoft": 5,
    "colleague": 4,
    "workday student": 5,

    # General academic links
    "course catalog": 3,
    "catalog": 1,
    "academics": 1,

    # Administrative pages to deprioritize
    "academic calendar": -5,
    "transcripts": -8,
    "graduation": -8,
    "tuition": -6,
    "financial aid": -6,
    "registrar": -2,
    "admissions": -5,
}
  
CRAWL_HINTS = ("registrar", "enroll", "registration", "admission", "academics", "student", "schedule", "class", "course")
SKIP_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".doc", ".docx", ".xlsx")

@dataclass
class ScheduleCandidate:
    college: str
    homepage_url: str
    candidate_url: str
    link_text: str
    score: int
    evidence_text: str
    source_page_url: str
    discovery_status: str = "CANDIDATE_REQUIRES_VALIDATION"

class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []
    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)
    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href.strip(), " ".join(" ".join(self._text).split())))
            self._href, self._text = None, []


def _fetch_html(url: str, timeout: int) -> tuple[str, str]:
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
        )
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=(5, min(timeout, 10)),
        stream=True,
        allow_redirects=True,
    )

    try:
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            raise ValueError(
                f"URL did not return HTML: {content_type}"
            )

        content = bytearray()

        for chunk in response.iter_content(chunk_size=16384):
            if not chunk:
                continue

            remaining = 2_000_000 - len(content)
            content.extend(chunk[:remaining])

            if len(content) >= 2_000_000:
                break

        charset = response.encoding or "utf-8"

        return (
            content.decode(charset, errors="replace"),
            response.url,
        )

    finally:
        response.close()

def _score(text: str, url: str) -> int:
    haystack = f"{text} {url}".lower().replace("_", " ").replace("-", " ")
    return sum(weight for phrase, weight in KEYWORDS.items() if phrase in haystack)

def _valid_url(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in {"http", "https"} and bool(p.netloc) and not p.path.lower().endswith(SKIP_EXTENSIONS)


def discover_schedule_candidates(
    college: str,
    homepage_url: str,
    timeout: int = 20,
    max_candidates: int = 20,
    max_pages: int = 12,
    max_depth: int = 2,
    max_total_seconds: int = 30,
) -> list[dict]:
    """Discover and rank possible official course schedule links."""

    if not college or not str(college).strip():
        raise ValueError("college is required")

    parsed = urlparse(str(homepage_url).strip())

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("homepage_url must be a valid http(s) URL")

    seed = str(homepage_url).strip()
    seed_host = (parsed.hostname or "").lower()

    queue = deque([(seed, 0)])
    visited = set()
    best_by_url = {}
    page_errors = []

    start_time = time.monotonic()

    while queue and len(visited) < max_pages:

        # Stop when the total discovery time is exhausted.
        if time.monotonic() - start_time >= max_total_seconds:
            page_errors.append(
                f"Stopped: total time limit of {max_total_seconds} seconds reached."
            )
            break

        page_url, depth = queue.popleft()
        normalized = urldefrag(page_url)[0]

        if normalized in visited:
            continue

        visited.add(normalized)

        try:
            html, final_page = _fetch_html(normalized, timeout)
        except Exception as exc:
            page_errors.append(
                f"{normalized}: {type(exc).__name__}: {exc}"
            )
            continue

        final_page = urldefrag(final_page)[0]

        parser = _LinkParser()
        parser.feed(html)

        links_to_crawl = []

        for href, link_text in parser.links:

            if not href or href.startswith(
                ("#", "mailto:", "tel:", "javascript:")
            ):
                continue

            absolute = urldefrag(urljoin(final_page, href))[0]

            if not _valid_url(absolute):
                continue

            target = urlparse(absolute)
            host = (target.hostname or "").lower()

            same_site = (
                host == seed_host or host.endswith("." + seed_host)
            )

            score = _score(link_text, absolute)

            if same_site:
                score += 3

            if score > 0:
                candidate = ScheduleCandidate(
                    college=str(college).strip(),
                    homepage_url=seed,
                    candidate_url=absolute,
                    link_text=link_text,
                    score=score,
                    evidence_text=(
                        f"Link found on {final_page}: "
                        f"{link_text or absolute}"
                    ),
                    source_page_url=final_page,
                )

                old = best_by_url.get(absolute)

                if old is None or candidate.score > old.score:
                    best_by_url[absolute] = candidate

            path_text = f"{link_text} {target.path}".lower()

            relevant = any(
                hint in path_text for hint in CRAWL_HINTS
            )

            if (
                same_site
                and relevant
                and depth < max_depth
                and absolute not in visited
            ):
                links_to_crawl.append(
                    (score, absolute, depth + 1)
                )

        links_to_crawl.sort(
            key=lambda item: item[0],
            reverse=True
        )

        for _, next_url, next_depth in links_to_crawl:
            if next_url not in visited:
                queue.append((next_url, next_depth))

    # Rank candidates by relevance score.
    ranked = sorted(
        best_by_url.values(),
        key=lambda item: item.score,
        reverse=True
    )

    return [asdict(item) for item in ranked[:max_candidates]]



def discover_one(
    college: str,
    homepage_url: str,
    timeout: int = 20,
    max_candidates: int = 20,
    max_pages: int = 12,
    max_depth: int = 2,
) -> dict:
    """Check known working portals first, then crawl the college website."""

    try:
        # 1. Check previously verified college portals.
        known = KNOWN_SCHEDULE_PORTALS.get(
            str(college).strip().lower()
        )

        if known:
            candidate = ScheduleCandidate(
                college=college,
                homepage_url=homepage_url,
                candidate_url=known["url"],
                link_text=known["platform"],
                score=100,
                evidence_text=(
                    "Previously verified working portal. "
                    "Existing college integration."
                ),
                source_page_url=homepage_url,
                discovery_status="KNOWN_WORKING",
            )

            candidates = [asdict(candidate)]

            return {
                "COLLEGE": college,
                "HOMEPAGE_URL": homepage_url,
                "DISCOVERY_STATUS": "CANDIDATES_FOUND",
                "CANDIDATE_COUNT": 1,
                "TOP_CANDIDATE_URL": known["url"],
                "CANDIDATES": candidates,
                "ERROR_DETAILS": "",
            }

        # 2. Otherwise use the normal website crawler.
        candidates = discover_schedule_candidates(
            college,
            homepage_url,
            timeout=timeout,
            max_candidates=max_candidates,
            max_pages=max_pages,
            max_depth=max_depth,
        )

        return {
            "COLLEGE": college,
            "HOMEPAGE_URL": homepage_url,
            "DISCOVERY_STATUS": (
                "CANDIDATES_FOUND"
                if candidates
                else "NO_CANDIDATES_FOUND"
            ),
            "CANDIDATE_COUNT": len(candidates),
            "TOP_CANDIDATE_URL": (
                candidates[0]["candidate_url"]
                if candidates else ""
            ),
            "CANDIDATES": candidates,
            "ERROR_DETAILS": "",
        }

    except Exception as exc:
        return {
            "COLLEGE": college,
            "HOMEPAGE_URL": homepage_url,
            "DISCOVERY_STATUS": "DISCOVERY_ERROR",
            "CANDIDATE_COUNT": 0,
            "TOP_CANDIDATE_URL": "",
            "CANDIDATES": [],
            "ERROR_DETAILS": f"{type(exc).__name__}: {exc}",
        }

