"""Common adapter contracts shared by college schedule portal adapters.

The existing single-course search contract is intentionally preserved.
Batch discovery is an optional, separate capability so existing adapters
continue to work without implementing it immediately.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass
class SearchRequest:
    college: str
    schedule_url: str
    term: str
    subject: str
    course_number: str
    section_id: str = ""
    instructor: str = ""
    timeout: int = 60


@dataclass
class SearchResult:
    portal_type: str
    college: str
    source_url: str
    status: str = ""
    title: str = ""
    courses: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    raw_text: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoveryRequest:
    """Inputs for discovering a term's available subjects and course sections.

    Unlike SearchRequest, subject and course_number are deliberately optional:
    discovery starts before those values are known.
    """
    college: str
    schedule_url: str
    term: str
    timeout: int = 60
    max_pages: int = 25


@dataclass
class DiscoveryResult:
    """Normalized output from a portal's catalog/course discovery operation."""
    portal_type: str
    college: str
    source_url: str
    status: str = "not_implemented"
    subjects: list[dict[str, Any]] = field(default_factory=list)
    courses: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


class BaseAdapter(ABC):
    """Interface all portal adapters must follow."""

    portal_type = "unknown"

    @abstractmethod
    def search_courses(self, request: SearchRequest) -> SearchResult:
        """Search for one specific subject/course combination."""
        raise NotImplementedError

    def discover_courses(self, request: DiscoveryRequest) -> DiscoveryResult:
        """Discover subjects and course sections for a term, when supported.

        This method is intentionally non-abstract. Existing adapters remain
        instantiable and retain their current single-course search behavior.
        A batch runner should treat NotImplementedError as unsupported, not as
        an empty successful search.
        """
        self.validate_discovery_request(request)
        raise NotImplementedError(
            f"Batch course discovery is not implemented for portal '{self.portal_type}'."
        )

    @staticmethod
    def _validate_url(schedule_url: str) -> None:
        parsed = urlparse(str(schedule_url).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Schedule URL must be a valid http(s) URL.")

    def validate_request(self, request: SearchRequest) -> None:
        """Validate inputs for the existing single-course search workflow."""
        required = {
            "college": request.college,
            "schedule_url": request.schedule_url,
            "term": request.term,
            "subject": request.subject,
            "course_number": request.course_number,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(
                "Missing required search input(s): " + ", ".join(missing)
            )
        self._validate_url(request.schedule_url)

    def validate_discovery_request(self, request: DiscoveryRequest) -> None:
        """Validate batch discovery inputs without requiring a subject/course."""
        required = {
            "college": request.college,
            "schedule_url": request.schedule_url,
            "term": request.term,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(
                "Missing required discovery input(s): " + ", ".join(missing)
            )
        self._validate_url(request.schedule_url)
        if request.timeout <= 0:
            raise ValueError("timeout must be greater than zero.")
        if request.max_pages <= 0:
            raise ValueError("max_pages must be greater than zero.")

    def make_result(
        self,
        request: SearchRequest,
        *,
        status: str = "",
        title: str = "",
        courses: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        raw_text: str = "",
        diagnostics: dict[str, Any] | None = None,
    ) -> SearchResult:
        """Build a consistently shaped single-course search result."""
        return SearchResult(
            portal_type=self.portal_type,
            college=request.college,
            source_url=request.schedule_url,
            status=status,
            title=title,
            courses=courses or [],
            evidence=evidence or [],
            raw_text=raw_text,
            diagnostics=diagnostics or {},
        )

    def make_discovery_result(
        self,
        request: DiscoveryRequest,
        *,
        status: str = "",
        subjects: list[dict[str, Any]] | None = None,
        courses: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> DiscoveryResult:
        """Build a consistently shaped batch discovery result."""
        return DiscoveryResult(
            portal_type=self.portal_type,
            college=request.college,
            source_url=request.schedule_url,
            status=status,
            subjects=subjects or [],
            courses=courses or [],
            evidence=evidence or [],
            diagnostics=diagnostics or {},
        )
