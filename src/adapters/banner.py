"""Banner adapter that reuses the existing, tested UNCW search workflow.

This is an adapter wrapper, not a replacement for src.fetcher. It delegates
portal interaction to search_banner_classes() and uses the existing UNCW
parser so the current working behavior and field mapping are preserved.
"""
from typing import Any

from .base import BaseAdapter, SearchRequest, SearchResult
from ..fetcher import search_banner_classes
from ..research import _parse_uncw_course_records


class BannerAdapter(BaseAdapter):
    portal_type = "banner"

    def search_courses(self, request: SearchRequest) -> SearchResult:
        self.validate_request(request)

        # Existing UNCW/Banner browser workflow. Keep this call as the single
        # source of truth until term discovery is implemented and tested.
        raw = search_banner_classes(
            url=request.schedule_url,
            term=request.term,
            subject=request.subject,
            course_number=request.course_number,
            timeout=request.timeout,
        )

        text = raw.get("text", "") or ""
        tables = raw.get("tables", []) or []

        # Reuse the parser already used by Researcher so column mapping does
        # not diverge between the old and new workflow.
        course_rows = _parse_uncw_course_records(
            text,
            subject_code=request.subject,
            course_code=request.course_number,
            crn_filter=request.section_id,
            instructor_filter=request.instructor,
        )

        if not course_rows and tables:
            course_rows = _parse_uncw_course_records(
                "\\n\\n".join(tables),
                subject_code=request.subject,
                course_code=request.course_number,
                crn_filter=request.section_id,
                instructor_filter=request.instructor,
            )

        evidence = [{
            "Source URL": raw.get("url", request.schedule_url),
            "Source title": raw.get("title", ""),
            "HTTP status": raw.get("status", ""),
            "Fields supported / review": (
                "Course title, subject, course number, section, CRN, credits, "
                "meeting times, location and enrollment where published."
            ),
            "Evidence note": (
                f"Banner search for {request.term}; "
                f"{len(course_rows)} matching course record(s) extracted."
            ),
        }]

        return self.make_result(
            request,
            status=str(raw.get("status", "")),
            title=str(raw.get("title", "")),
            courses=course_rows,
            evidence=evidence,
            raw_text=text,
            diagnostics={
                "platform": raw.get("platform", self.portal_type),
                "term_selected": raw.get("term_selected"),
                "subject_selected": raw.get("subject_selected"),
                "table_count": len(tables),
            },
        )
