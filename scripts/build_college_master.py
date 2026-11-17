"""
Build a 100-college master list from an IPEDS HD2025 directory text file.

Input:
  data/raw/ipeds/Pasted text(20261116-073413).txt
  (tab-separated IPEDS directory file; change INPUT_FILE if your filename differs)

Output:
  data/processed/college_master.xlsx
  data/processed/college_master_100.csv

This script does NOT collect course schedules. IPEDS directory files do not contain
Fall 2026 CRNs, sections, class meeting times, or assigned instructors.
"""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = ROOT / "data" / "raw" / "ipeds" / "Pasted text(20261116-073413).txt"
OUTPUT_DIR = ROOT / "data" / "processed"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_SIZE = 100
RANDOM_SEED = 20261116

# Columns useful for a college master list.
KEEP_COLUMNS = [
    "UNITID", "INSTNM", "IALIAS", "ADDR", "CITY", "STABBR", "ZIP",
    "WEBADDR", "ADMINURL", "FAIDURL", "APPLURL", "NPRICURL",
    "SECTOR", "ICLEVEL", "CONTROL", "HLOFFER", "UGOFFER", "GROFFER",
    "HDEGOFR1", "DEGGRANT", "ACT", "CYACTIVE", "POSTSEC",
    "COUNTYNM", "LONGITUD", "LATITUDE"
]

def clean_website(value):
    value = str(value).strip()
    if not value or value.lower() in {"nan", "none"}:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value.lstrip("/")
    return value

def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"IPEDS file not found: {INPUT_FILE}\n"
            "Place your tab-separated HD2025 file in data/raw/ipeds/."
        )

    # Your supplied file is tab-delimited, not a ZIP or comma-separated file.
    df = pd.read_csv(INPUT_FILE, sep="\t", dtype=str, low_memory=False)
    df.columns = df.columns.str.strip().str.upper()

    required = {"UNITID", "INSTNM", "DEGGRANT", "ACT", "SECTOR"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing expected IPEDS columns: {sorted(missing)}")

    # IPEDS code meanings used:
    # DEGGRANT=1 means degree-granting; ACT=A means active.
    # Sector 1-9 represents institution sectors. Sector 0 is an administrative
    #/system office rather than a standard college sector; 99 is unknown.
    eligible = df[
        (df["DEGGRANT"].str.strip() == "1") &
        (df["ACT"].str.strip().str.upper() == "A") &
        (df["SECTOR"].str.strip().isin([str(i) for i in range(1, 10)]))
    ].copy()

    # Keep only fields actually present in the supplied file.
    available = [c for c in KEEP_COLUMNS if c in eligible.columns]
    eligible = eligible[available].copy()

    eligible["UNITID"] = eligible["UNITID"].astype(str).str.strip()
    eligible["INSTNM"] = eligible["INSTNM"].astype(str).str.strip()
    eligible = eligible.drop_duplicates(subset=["UNITID"])
    eligible["WEBADDR"] = eligible["WEBADDR"].apply(clean_website) if "WEBADDR" in eligible else ""

    # Reproducible random sample so the list is not biased toward the first states
    # in the source file. Replace this with a client-supplied list if they specify
    # the 100 institutions they want.
    if len(eligible) < SAMPLE_SIZE:
        raise ValueError(
            f"Only {len(eligible)} eligible institutions found; "
            f"cannot select {SAMPLE_SIZE}."
        )

    selected = (
        eligible.sample(n=SAMPLE_SIZE, random_state=RANDOM_SEED)
        .sort_values(["STABBR", "INSTNM"], na_position="last")
        .reset_index(drop=True)
    )
    selected.insert(0, "MASTER_LIST_ID", range(1, len(selected) + 1))
    selected["DATA_SOURCE"] = "IPEDS HD2025 Directory"
    selected["SOURCE_YEAR"] = "2025"
    selected["SCHEDULE_TERM_REQUESTED"] = "Fall 2026"
    selected["SCHEDULE_STATUS"] = "Not yet collected"

    # Empty, correctly headed templates for the next phase of the project.
    course_template = pd.DataFrame(columns=[
        "UNITID", "COLLEGE_NAME", "TERM", "SUBJECT", "COURSE_NUMBER",
        "COURSE_TITLE", "CRN", "SECTION", "INSTRUCTOR_NAME", "CREDITS",
        "DAYS", "START_TIME", "END_TIME", "CAMPUS", "BUILDING", "ROOM",
        "SCHEDULE_SOURCE_URL", "VERIFIED_DATE", "STATUS"
    ])
    instructor_template = pd.DataFrame(columns=[
        "UNITID", "COLLEGE_NAME", "INSTRUCTOR_NAME", "FIRST_NAME",
        "LAST_NAME", "DEPARTMENT", "JOB_TITLE", "EMAIL",
        "FACULTY_PROFILE_URL", "VERIFIED_DATE", "STATUS"
    ])
    evidence_template = pd.DataFrame(columns=[
        "UNITID", "COLLEGE_NAME", "RECORD_TYPE", "RECORD_KEY",
        "SOURCE_URL", "SOURCE_TITLE", "SOURCE_DATE", "CHECKED_DATE",
        "EVIDENCE_NOTES", "VERIFICATION_STATUS"
    ])
    readme = pd.DataFrame({
        "Item": [
            "Purpose", "Source file", "Eligibility filter",
            "Selection method", "Requested schedule term",
            "Important limitation", "Next step"
        ],
        "Details": [
            "Master list of 100 active, degree-granting U.S. institutions.",
            "IPEDS HD2025 directory file supplied by the user.",
            "DEGGRANT=1, ACT=A, SECTOR=1 through 9. Sector 0 system offices and 99 unknown excluded.",
            f"Reproducible random sample of 100 from eligible institutions; seed={RANDOM_SEED}.",
            "Fall 2026.",
            "IPEDS does not contain individual Fall 2026 course sections, CRNs, class times, or assigned instructors.",
            "Use the existing college-specific schedule adapters and official college sites to populate the course, instructor, and evidence sheets."
        ]
    })

    xlsx_path = OUTPUT_DIR / "college_master.xlsx"
    csv_path = OUTPUT_DIR / "college_master_100.csv"

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        selected.to_excel(writer, sheet_name="Selected 100", index=False)
        eligible.to_excel(writer, sheet_name="All Eligible", index=False)
        course_template.to_excel(writer, sheet_name="Course Schedule", index=False)
        instructor_template.to_excel(writer, sheet_name="Instructor Details", index=False)
        evidence_template.to_excel(writer, sheet_name="Evidence", index=False)
        readme.to_excel(writer, sheet_name="README", index=False)

    selected.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # Basic workbook formatting.
    wb = load_workbook(xlsx_path)
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[1].height = 30
        for col_cells in ws.columns:
            max_len = max((len(str(c.value)) if c.value is not None else 0) for c in col_cells[:200])
            ws.column_dimensions[get_column_letter(col_cells[0].column)].width = min(max(max_len + 2, 12), 42)
    wb.save(xlsx_path)

    print(f"Input rows: {len(df):,}")
    print(f"Eligible active degree-granting institutions: {len(eligible):,}")
    print(f"Selected colleges: {len(selected):,}")
    print(f"Excel: {xlsx_path}")
    print(f"CSV:   {csv_path}")

if __name__ == "__main__":
    main()
