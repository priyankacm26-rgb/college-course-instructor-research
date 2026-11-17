from io import BytesIO
import pandas as pd
from .schemas import COURSE_COLUMNS, INSTRUCTOR_COLUMNS

def build_workbook(course_rows, instructor_rows, evidence_rows):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(course_rows, columns=COURSE_COLUMNS).to_excel(writer, sheet_name="Course_Data", index=False)
        pd.DataFrame(instructor_rows, columns=INSTRUCTOR_COLUMNS).to_excel(writer, sheet_name="Instructor_Data", index=False)
        pd.DataFrame(evidence_rows).to_excel(writer, sheet_name="Evidence_Report", index=False)
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for column_cells in ws.columns:
                max_len = min(max(len(str(c.value or "")) for c in column_cells) + 2, 55)
                ws.column_dimensions[column_cells[0].column_letter].width = max(12, max_len)
    output.seek(0)
    return output.getvalue()
