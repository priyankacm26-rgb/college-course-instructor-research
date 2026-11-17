"""CUNY Global Search adapter.

This adapter delegates the current CUNY portal interaction to the existing
CUNY workflow in src.fetcher and reuses the conservative generic course-table
parser in src.research. The current fetcher workflow selects Baruch College;
generalizing institution selection across all CUNY campuses is a later task.
"""
from .base import BaseAdapter, SearchRequest
from ..fetcher import _cuny_global_search
from ..research import _parse_generic_course_records


class CUNYAdapter(BaseAdapter):
    portal_type = "cuny_global_search"

    def search_courses(self, request: SearchRequest):
        self.validate_request(request)

        raw = _cuny_global_search(
    url=request.schedule_url,
    term=request.term,
    subject=request.subject,
    course_number=request.course_number,
    timeout=request.timeout,
    institution=request.college,
)
        text = raw.get("text", "") or ""
        tables = raw.get("tables", []) or []

        course_rows = _parse_generic_course_records(
            [text] + tables,
            subject=request.subject,
            course_code=request.course_number,
            crn_filter=request.section_id,
            instructor_filter=request.instructor,
        )

        evidence = [{
            "Source URL": raw.get("url", request.schedule_url),
            "Source title": raw.get("title", ""),
            "HTTP status": raw.get("status", ""),
            "Fields supported / review": (
                "Course and section fields recognized from the official CUNY "
                "search results; verify each populated field against the source."
            ),
            "Evidence note": (
                f"CUNY Global Search for {request.term}; "
                f"{len(course_rows)} matching record(s) extracted."
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
                "institution_selected": raw.get("institution_selected"),
                "term_selected": raw.get("term_selected"),
                "subject_selected": raw.get("subject_selected"),
                "table_count": len(tables),
            },
        )
