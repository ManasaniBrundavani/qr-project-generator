import csv
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile


FIELD_ALIASES = {
    "name": [
        "name",
        "student_name",
        "student",
        "team leader name",
        "team_leader_name",
    ],
    "roll": [
        "roll",
        "roll_number",
        "id_no",
        "team leader roll no",
        "team_leader_roll_no",
    ],
    "project_title": [
        "project_title",
        "title",
        "project",
        "title of the project",
    ],
    "project_description": [
        "project_description",
        "description",
        "desc",
        "project description",
    ],
    "video_link": [
        "video_link",
        "video link",
        "video",
        "video_url",
        "video url",
    ],
}


REQUIRED_FIELDS = ["name", "roll", "project_title", "project_description"]

BULK_REQUIRED_COLUMNS_TEXT = (
    "Required columns: Team Leader Name, Team Leader Roll No, "
    "Title of the Project, Project Description"
)


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize_key(value):
    return re.sub(r"[^a-z0-9]+", "_", clean_text(value).strip().lower()).strip("_")


def normalize_record(record):
    normalized = {normalize_key(k): clean_text(v) for k, v in record.items()}
    mapped = {}
    for field, aliases in FIELD_ALIASES.items():
        value = ""
        for alias in aliases:
            key = normalize_key(alias)
            if key in normalized and normalized[key]:
                value = normalized[key]
                break
        mapped[field] = value
    return mapped


def validate_record(record):
    return all(clean_text(record.get(field, "")) for field in REQUIRED_FIELDS)


def parse_uploaded_records(uploaded_file):
    ext = uploaded_file.name.lower().rsplit(".", 1)[-1]

    if ext == "csv":
        text_data = uploaded_file.getvalue().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text_data))
        return [normalize_record(row) for row in reader]

    if ext == "json":
        raw = json.loads(uploaded_file.getvalue().decode("utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("projects", [])
        if not isinstance(raw, list):
            raise ValueError("JSON must be a list or {\"projects\": [...]} format.")
        return [normalize_record(row) for row in raw if isinstance(row, dict)]

    if ext == "xlsx":
        try:
            import pandas as pd

            frame = pd.read_excel(uploaded_file).fillna("")
            return [normalize_record(row) for row in frame.to_dict(orient="records")]
        except Exception:
            return parse_xlsx_without_dependencies(uploaded_file.getvalue())

    if ext == "xls":
        try:
            import pandas as pd

            frame = pd.read_excel(uploaded_file).fillna("")
            return [normalize_record(row) for row in frame.to_dict(orient="records")]
        except Exception as exc:
            raise ValueError(
                "Legacy .xls import needs optional Excel dependencies. "
                "Use CSV/JSON/XLSX, or install: pip install pandas xlrd"
            ) from exc

    raise ValueError("Unsupported file type. Use CSV, JSON, XLSX, or XLS.")


def parse_xlsx_without_dependencies(raw_bytes):
    ns = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }

    def text_or_empty(node):
        return "" if node is None or node.text is None else node.text

    def col_letters(cell_ref):
        return "".join(ch for ch in cell_ref if ch.isalpha())

    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            shared_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("main:si", ns):
                parts = [text_or_empty(t) for t in item.findall(".//main:t", ns)]
                shared.append("".join(parts))

        workbook_root = ET.fromstring(zf.read("xl/workbook.xml"))
        workbook_rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))

        rel_map = {
            rel.attrib.get("Id"): rel.attrib.get("Target", "")
            for rel in workbook_rels.findall("rel:Relationship", ns)
        }

        first_sheet = workbook_root.find("main:sheets/main:sheet", ns)
        if first_sheet is None:
            return []

        rel_id = first_sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        target = rel_map.get(rel_id, "")
        if not target:
            return []

        sheet_path = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
        sheet_root = ET.fromstring(zf.read(sheet_path))

        rows = []
        for row in sheet_root.findall("main:sheetData/main:row", ns):
            row_data = {}
            for cell in row.findall("main:c", ns):
                ref = cell.attrib.get("r", "")
                key = col_letters(ref)
                ctype = cell.attrib.get("t", "")
                value = ""

                if ctype == "inlineStr":
                    value = text_or_empty(cell.find("main:is/main:t", ns))
                elif ctype == "s":
                    idx_text = text_or_empty(cell.find("main:v", ns))
                    if idx_text.isdigit():
                        idx = int(idx_text)
                        value = shared[idx] if 0 <= idx < len(shared) else ""
                else:
                    value = text_or_empty(cell.find("main:v", ns))

                row_data[key] = clean_text(value)
            rows.append(row_data)

    if not rows:
        return []

    headers_by_col = {
        col: clean_text(value)
        for col, value in rows[0].items()
        if clean_text(value)
    }
    if not headers_by_col:
        return []

    records = []
    for row in rows[1:]:
        record = {}
        for col, header in headers_by_col.items():
            record[header] = clean_text(row.get(col, ""))
        records.append(normalize_record(record))
    return records


def sample_csv_template():
    return (
        "Team Leader Name,Team Leader Roll No,Title of the Project,Project Description,video_link\n"
        "Alice,101,Face Recognition,Detects faces in live stream,\n"
        "Bob,102,QR Attendance,Scan based attendance system,\n"
    )
