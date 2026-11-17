"""Conservative discovery of official college class-schedule links.

This module only identifies candidate links from an institution website. It does
not claim that a candidate is a working schedule portal; candidates must be
validated before being passed to a college-specific scraper.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import re


KEYWORDS = {
    "class schedule": 12,
    "class search": 12,
    "schedule of classes": 12,
    "course schedule": 12,
    "search for classes": 12,
    "browse classes": 11,
    "registration": 5,
    "enrollment": 4,
    "course catalog": 4,
    "catalog": 2,
    "student planning": 3,
    "self service": 4,
    "banner": 2,
    "peoplesoft": 2,
}

SKIP_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".doc", ".docx")


@dataclass
class ScheduleCandidate:
    college: str
    homepage_url: str
    candidate_url: str
    link_text: str
    score: int
    evidence_text: str
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
            self._href = None
            self._text = []


def _fetch_html(url: str, timeout: int) -> tuple[str, str]:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; CollegeScheduleResearch/1.0)"})
    with urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            raise ValueError(f"Homepage did not return HTML (Content-Type: {content_type}).")
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read(3_000_000).decode(charset, errors="replace"), final_url


def _score(text: str, url: str) -> int:
    haystack = f"{text} {url}".lower().replace("_", " ").replace("-", " ")
    score = 0
    for phrase, weight in KEYWORDS.items():
        if phrase in haystack:
            score += weight
    return score


def discover_schedule_candidates(
    college: str,
    homepage_url: str,
    timeout: int = 20,
    max_candidates: int = 15,
) -> list[dict]:
    """Fetch an official homepage and return ranked schedule-link candidates.

    Only links found on the supplied homepage are considered. This deliberately
    avoids broad crawling and never marks a URL as a confirmed schedule portal.
    """
    if not college or not str(college).strip():
        raise ValueError("college is required")
    parsed = urlparse(str(homepage_url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("homepage_url must be a valid http(s) URL")

    html, final_homepage = _fetch_html(homepage_url, timeout)
    parser = _LinkParser()
    parser.feed(html)

    home_host = (urlparse(final_homepage).hostname or "").lower()
    best_by_url = {}
    for href, link_text in parser.links:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute, _ = urldefrag(urljoin(final_homepage, href))
        target = urlparse(absolute)
        if target.scheme not in {"http", "https"} or not target.netloc:
            continue
        if target.path.lower().endswith(SKIP_EXTENSIONS):
            continue
        score = _score(link_text, absolute)
        if score <= 0:
            continue
        host = (target.hostname or "").lower()
        # Prefer official-site links, but retain likely hosted portals linked by
        # the official homepage (e.g. Banner or PeopleSoft).
        if host == home_host or host.endswith("." + home_host):
            score += 3
        candidate = ScheduleCandidate(
            college=str(college).strip(),
            homepage_url=final_homepage,
            candidate_url=absolute,
            link_text=link_text,
            score=score,
            evidence_text=f"Link discovered on official homepage: {link_text or absolute}",
        )
        old = best_by_url.get(absolute)
        if old is None or candidate.score > old.score:
            best_by_url[absolute] = candidate

    ranked = sorted(best_by_url.values(), key=lambda item: (-item.score, item.candidate_url))
    return [asdict(item) for item in ranked[:max_candidates]]


def discover_one(college: str, homepage_url: str, timeout: int = 20) -> dict:
    """Return a compact status record suitable for a batch queue."""
    try:
        candidates = discover_schedule_candidates(college, homepage_url, timeout=timeout)
        return {
            "COLLEGE": college,
            "HOMEPAGE_URL": homepage_url,
            "DISCOVERY_STATUS": "CANDIDATES_FOUND" if candidates else "NO_CANDIDATES_FOUND",
            "CANDIDATE_COUNT": len(candidates),
            "TOP_CANDIDATE_URL": candidates[0]["candidate_url"] if candidates else "",
            "CANDIDATES": candidates,
            "ERROR_DETAILS": "",
        }
    except (HTTPError, URLError, TimeoutError, ValueError, OSError, UnicodeError) as exc:
        return {
            "COLLEGE": college,
            "HOMEPAGE_URL": homepage_url,
            "DISCOVERY_STATUS": "DISCOVERY_ERROR",
            "CANDIDATE_COUNT": 0,
            "TOP_CANDIDATE_URL": "",
            "CANDIDATES": [],
            "ERROR_DETAILS": f"{type(exc).__name__}: {exc}",
        }
