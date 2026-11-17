"""Safe batch queue preparation for the college research workbook.

This module does not scrape schedules. It prepares a traceable work queue from
IPEDS college records and explicitly marks schedule URLs as undiscovered.
Existing single-college research code is not modified.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import pandas as pd


REQUIRED_COLUMNS = {"UNITID", "INSTNM", "WEBADDR", "CITY", "STABBR"}


def _normalize_website(value: object) -> str:
    if pd.isna(value):
        return ""
    url = str(value).strip()
    if not url:
        return ""
    if not urlparse(url).scheme:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def build_batch_queue(
    workbook_path: str | Path,
    term: str,
    sheet_name: str = "Selected 100",
) -> pd.DataFrame:
    """Load selected colleges and prepare a schedule-portal discovery queue."""
    if not str(term).strip():
        raise ValueError("term is required, e.g. Fall 2026.")

    path = Path(workbook_path)
    if not path.exists():
        raise FileNotFoundError(f"College master workbook not found: {path}")

    colleges = pd.read_excel(path, sheet_name=sheet_name, dtype={"UNITID": str})
    missing = REQUIRED_COLUMNS.difference(colleges.columns)
    if missing:
        raise ValueError(
            f"Sheet '{sheet_name}' is missing required columns: "
            + ", ".join(sorted(missing))
        )

    queue = colleges.copy()
    queue["WEBADDR"] = queue["WEBADDR"].apply(_normalize_website)
    queue["TERM"] = str(term).strip()
    queue["SCHEDULE_URL"] = ""
    queue["PORTAL_TYPE"] = "unknown"
    queue["DISCOVERY_STATUS"] = queue["WEBADDR"].apply(
        lambda value: "NEEDS_SCHEDULE_URL" if value else "MISSING_COLLEGE_WEBSITE"
    )
    queue["COURSE_COUNT"] = 0
    queue["INSTRUCTOR_COUNT"] = 0
    queue["LAST_CHECKED"] = ""
    queue["ERROR_DETAILS"] = ""

    # Stable identifiers make reruns and client-specific tracking safer.
    if "MASTER_LIST_ID" not in queue.columns:
        queue.insert(0, "MASTER_LIST_ID", range(1, len(queue) + 1))
    queue["BATCH_KEY"] = (
        queue["UNITID"].fillna("").astype(str).str.strip()
        + "|"
        + queue["TERM"]
    )
    return queue


def save_batch_queue(
    queue: pd.DataFrame,
    output_path: str | Path,
    sheet_name: str = "Schedule Discovery",
) -> Path:
    """Write a separate queue workbook; never overwrite the source master."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        queue.to_excel(writer, sheet_name=sheet_name, index=False)
        ws = writer.book[sheet_name]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cells in ws.columns:
            width = min(max(len(str(c.value or "")) for c in cells) + 2, 48)
            ws.column_dimensions[cells[0].column_letter].width = max(12, width)
    return output
