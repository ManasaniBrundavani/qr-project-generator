import csv
import io
import json
import os
import re
import socket
import sqlite3
import uuid

import streamlit as st

from bulk_import import BULK_REQUIRED_COLUMNS_TEXT, parse_xlsx_without_dependencies, sample_csv_template
from database import (
    find_existing_project_id,
    get_all_projects,
    init_db,
    insert_project,
    set_project_expiry,
    update_project,
)
from settings_store import load_settings, save_settings
from utils.qr_generator import generate_qr


REQUIRED_IMPORT_FIELDS = ["name", "roll", "project_title", "project_description"]
FIELD_LABELS = {
    "name": "Name",
    "roll": "Roll",
    "project_title": "Project Title",
    "project_description": "Description",
    "video_link": "Video Link",
}
VIDEO_UPLOAD_DIR = "uploaded_videos"
ALLOWED_VIDEO_EXTS = {".mp4", ".webm", ".ogg", ".m4v", ".mov"}


def detect_lan_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize_key(value):
    return re.sub(r"[^a-z0-9]+", "_", clean_text(value).lower()).strip("_")


def safe_filename(text):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", clean_text(text))
    return cleaned.strip("_") or "project"


def is_valid_image_path(path_value):
    path = clean_text(path_value)
    if not path or path.lower() == "path":
        return False
    return os.path.exists(path)


def set_qr_path(project_id, qr_path):
    conn = sqlite3.connect("expo.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE projects SET qr_path = ? WHERE id = ?", (qr_path, project_id))
    conn.commit()
    conn.close()


def save_uploaded_video(uploaded_file, qr_base_url):
    if uploaded_file is None:
        return ""

    original_name = clean_text(uploaded_file.name)
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ALLOWED_VIDEO_EXTS:
        return ""

    os.makedirs(VIDEO_UPLOAD_DIR, exist_ok=True)
    safe_base = safe_filename(os.path.splitext(original_name)[0])
    file_name = f"{safe_base}_{uuid.uuid4().hex[:10]}{ext}"
    file_path = os.path.join(VIDEO_UPLOAD_DIR, file_name)

    with open(file_path, "wb") as file_obj:
        file_obj.write(uploaded_file.getbuffer())

    return f"{qr_base_url}/uploaded_videos/{file_name}"


def compute_qr_base_url(settings):
    flask_port = int(settings.get("flask_port", 5000))
    public_url = clean_text(settings.get("public_base_url", "")).rstrip("/")
    manual_url = clean_text(settings.get("manual_qr_base_url", "")).rstrip("/")
    if public_url:
        return public_url
    if settings.get("auto_detect_ip", True):
        return f"http://{detect_lan_ip()}:{flask_port}"
    if manual_url:
        return manual_url
    return f"http://{detect_lan_ip()}:{flask_port}"


def create_project_and_qr(
    name,
    roll,
    project_title,
    project_description,
    video_link,
    qr_base_url,
    settings,
):
    existing_id = find_existing_project_id(roll, project_title)
    if existing_id:
        project_id = existing_id
        update_project(
            project_id,
            name,
            roll,
            project_title,
            project_description,
            video_link,
            video_link,
        )
        set_project_expiry(
            project_id,
            expiry_enabled=settings.get("expiry_enabled", True),
            expiry_days=settings.get("expiry_days", 150),
        )
    else:
        project_id = insert_project(
            name,
            roll,
            project_title,
            project_description,
            video_link,
            video_link,
            "",
            expiry_enabled=settings.get("expiry_enabled", True),
            expiry_days=settings.get("expiry_days", 150),
        )
    unique_url = f"{qr_base_url}/?id={project_id}"
    file_name = safe_filename(f"{roll}_{name}_{project_title}_{project_id}")
    qr_label = f"{name} | {roll}"
    qr_path = generate_qr(unique_url, file_name, label=qr_label)
    set_qr_path(project_id, qr_path)
    return project_id, unique_url, qr_path


def regenerate_qr_for_row(row, qr_base_url, settings, force=False):
    # DB order: id,name,roll,title,description,website,video,qr_path,created_at,expires_at
    project_id = row[0]
    name = clean_text(row[1])
    roll = clean_text(row[2])
    project_title = clean_text(row[3])
    qr_path = clean_text(row[7] if len(row) > 7 else "")

    if is_valid_image_path(qr_path) and not force:
        return False

    unique_url = f"{qr_base_url}/?id={project_id}"
    file_name = safe_filename(f"{roll}_{name}_{project_title}_{project_id}")
    qr_label = f"{name} | {roll}"
    new_path = generate_qr(unique_url, file_name, label=qr_label)
    set_qr_path(project_id, new_path)
    set_project_expiry(
        project_id,
        expiry_enabled=settings.get("expiry_enabled", True),
        expiry_days=settings.get("expiry_days", 150),
    )
    return True


def regenerate_all_qrs(qr_base_url, settings):
    rows = get_all_projects()
    changed = 0
    for row in rows:
        if regenerate_qr_for_row(row, qr_base_url, settings, force=True):
            changed += 1
    return changed


def parse_raw_uploaded_records(uploaded_file):
    ext = uploaded_file.name.lower().rsplit(".", 1)[-1]
    if ext == "csv":
        text_data = uploaded_file.getvalue().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text_data))
        return [dict(row) for row in reader]
    if ext == "json":
        raw = json.loads(uploaded_file.getvalue().decode("utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("projects", [])
        if not isinstance(raw, list):
            raise ValueError("JSON must be a list or {'projects': [...]} format.")
        return [row for row in raw if isinstance(row, dict)]
    if ext in {"xlsx", "xls"}:
        try:
            import pandas as pd
        except ImportError as exc:
            if ext == "xlsx":
                return parse_xlsx_without_dependencies(uploaded_file.getvalue())
            raise ValueError("Legacy .xls needs pandas + xlrd. Prefer .xlsx or .csv.") from exc
        try:
            frame = pd.read_excel(uploaded_file).fillna("")
            return frame.to_dict(orient="records")
        except Exception:
            if ext == "xlsx":
                return parse_xlsx_without_dependencies(uploaded_file.getvalue())
            raise
    raise ValueError("Unsupported file type. Use CSV, JSON, XLSX, or XLS.")


def default_mapping_from_columns(columns):
    normalized = {normalize_key(col): col for col in columns}
    alias_map = {
        "name": ["name", "student_name", "student", "team_leader_name"],
        "roll": ["roll", "roll_number", "id_no", "team_leader_roll_no"],
        "project_title": ["project_title", "title", "project", "title_of_the_project"],
        "project_description": ["project_description", "description", "desc"],
        "video_link": ["video_link", "video", "video_url", "url", "link"],
    }
    mapping = {}
    for field, aliases in alias_map.items():
        selected = ""
        for alias in aliases:
            if alias in normalized:
                selected = normalized[alias]
                break
        mapping[field] = selected
    return mapping


def map_raw_row(row, column_mapping):
    mapped = {}
    for field in FIELD_LABELS:
        source_col = clean_text(column_mapping.get(field, ""))
        mapped[field] = clean_text(row.get(source_col, "")) if source_col else ""
    return mapped


def is_valid_import_row(mapped_row):
    return all(clean_text(mapped_row.get(field, "")) for field in REQUIRED_IMPORT_FIELDS)


def apply_global_styles():
    st.markdown(
        """
        <style>
        section.main > div.block-container {
            max-width: 100%;
            padding-top: 1rem;
            padding-left: 1rem;
            padding-right: 1rem;
        }
        section[data-testid="stSidebar"] div[data-baseweb="select"] * {
            cursor: pointer !important;
        }
        section[data-testid="stSidebar"] [role="listbox"] *,
        section[data-testid="stSidebar"] [role="option"] * {
            cursor: pointer !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def show_admin_settings(settings, qr_base_url):
    st.header("Admin Settings")
    st.caption("All runtime options are managed here. No code edits required.")

    with st.form("admin_settings_form"):
        st.subheader("Network and QR URL")
        auto_detect_ip = st.checkbox("Auto-detect network IP", value=settings.get("auto_detect_ip", True))
        flask_port = st.number_input(
            "Flask Port",
            min_value=1,
            max_value=65535,
            value=int(settings.get("flask_port", 5000)),
            step=1,
        )
        public_base_url = st.text_input(
            "Public Base URL (optional, overrides LAN URL)",
            value=clean_text(settings.get("public_base_url", "")),
            placeholder="https://your-domain.com",
        )
        manual_qr_base_url = st.text_input(
            "Manual QR Base URL (used when auto-detect is off)",
            value=clean_text(settings.get("manual_qr_base_url", "")),
            placeholder="http://192.168.1.25:5000",
        )
        auto_update_qr_urls = st.checkbox(
            "Auto-update all stored QR URLs when base URL changes",
            value=settings.get("auto_update_qr_urls", True),
        )

        st.subheader("Validity")
        expiry_enabled = st.checkbox("Enable QR expiry", value=settings.get("expiry_enabled", True))
        expiry_days = st.number_input(
            "Expiry days",
            min_value=1,
            max_value=10000,
            value=int(settings.get("expiry_days", 150)),
            step=1,
            disabled=not expiry_enabled,
        )

        st.subheader("Mobile Layout")
        video_fit = st.selectbox(
            "Video fit mode",
            ["contain", "cover"],
            index=0 if settings.get("video_fit", "contain") == "contain" else 1,
        )
        font_scale = st.slider(
            "Font scale",
            min_value=0.8,
            max_value=1.8,
            value=float(settings.get("font_scale", 1.0)),
            step=0.05,
        )
        spacing_scale = st.slider(
            "Spacing scale",
            min_value=0.7,
            max_value=1.8,
            value=float(settings.get("spacing_scale", 1.0)),
            step=0.05,
        )
        grid_columns = st.slider(
            "Projects per row (View All Projects)",
            min_value=2,
            max_value=8,
            value=int(settings.get("grid_columns", 6)),
            step=1,
        )

        submitted = st.form_submit_button("Save Settings")

    st.info(f"Current QR base URL: {qr_base_url}")

    if submitted:
        updated = {
            **settings,
            "auto_detect_ip": auto_detect_ip,
            "flask_port": int(flask_port),
            "public_base_url": clean_text(public_base_url).rstrip("/"),
            "manual_qr_base_url": clean_text(manual_qr_base_url).rstrip("/"),
            "auto_update_qr_urls": auto_update_qr_urls,
            "expiry_enabled": expiry_enabled,
            "expiry_days": int(expiry_days),
            "video_fit": video_fit,
            "font_scale": float(font_scale),
            "spacing_scale": float(spacing_scale),
            "grid_columns": int(grid_columns),
        }
        updated_base = compute_qr_base_url(updated)
        old_base = clean_text(updated.get("last_qr_base_url", ""))

        if updated.get("auto_update_qr_urls", True) and old_base != updated_base:
            regenerated = regenerate_all_qrs(updated_base, updated)
            st.success(f"Settings saved. Base URL changed. Regenerated {regenerated} QR(s).")
            updated["last_qr_base_url"] = updated_base
        else:
            st.success("Settings saved.")

        save_settings(updated)
        st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Regenerate All QRs Now"):
            regenerated = regenerate_all_qrs(qr_base_url, settings)
            settings["last_qr_base_url"] = qr_base_url
            save_settings(settings)
            st.success(f"Regenerated {regenerated} QR(s).")
    with col2:
        if st.button("Apply Expiry Rules to Existing Projects"):
            count = 0
            for row in get_all_projects():
                set_project_expiry(
                    row[0],
                    expiry_enabled=settings.get("expiry_enabled", True),
                    expiry_days=settings.get("expiry_days", 150),
                )
                count += 1
            st.success(f"Updated expiry values for {count} project(s).")


init_db()
settings = load_settings()
QR_BASE_URL = compute_qr_base_url(settings)

if settings.get("auto_update_qr_urls", True):
    last_base = clean_text(settings.get("last_qr_base_url", ""))
    if last_base != QR_BASE_URL:
        regenerated_count = regenerate_all_qrs(QR_BASE_URL, settings)
        settings["last_qr_base_url"] = QR_BASE_URL
        save_settings(settings)
        st.session_state["auto_regenerated_notice"] = regenerated_count

st.set_page_config(
    page_title="NEX AI QR Registration",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_global_styles()
st.title("🎓 NEX AI Project Registration System")

if "auto_regenerated_notice" in st.session_state:
    st.info(f"Base URL changed. Auto-regenerated {st.session_state.pop('auto_regenerated_notice')} QR(s).")

menu = st.sidebar.selectbox(
    "Menu",
    ["Register Project", "Bulk Import", "View All Projects", "Admin Settings"],
)

if menu == "Register Project":
    st.header("Project Registration Form")
    with st.form("registration_form"):
        name = st.text_input("Student Name")
        roll = st.text_input("Roll Number")
        project_title = st.text_input("Project Title")
        video_web_link = st.text_input("Project Website Link")
        local_video_file = st.file_uploader(
            "Or choose video from local storage",
            type=["mp4", "webm", "ogg", "m4v", "mov"],
            accept_multiple_files=False,
        )
        project_description = st.text_area("Project Description")
        submit = st.form_submit_button("Generate QR & Register")

    if submit:
        name = clean_text(name)
        roll = clean_text(roll)
        project_title = clean_text(project_title)
        project_description = clean_text(project_description)
        video_web_link = clean_text(video_web_link)
        local_video_link = save_uploaded_video(local_video_file, QR_BASE_URL)
        # Prefer web link when both are provided.
        video_link = video_web_link or local_video_link

        if all([name, roll, project_title, project_description]):
            _, unique_url, qr_path = create_project_and_qr(
                name,
                roll,
                project_title,
                project_description,
                video_link,
                QR_BASE_URL,
                settings,
            )
            if video_web_link and local_video_link:
                st.info("Both link and local video were provided. Web link was used by priority.")
            st.success("Registration successful.")
            st.subheader("Generated QR Code")
            st.image(qr_path, width=250)
            st.subheader("QR Redirect URL")
            st.write(unique_url)
        else:
            st.error("Please fill all required fields.")

elif menu == "Bulk Import":
    st.header("Bulk Import Projects")
    st.write("Upload CSV, JSON, or Excel to register projects without manual entry.")
    st.caption(BULK_REQUIRED_COLUMNS_TEXT)
    st.download_button(
        "Download Sample CSV",
        data=sample_csv_template(),
        file_name="project_import_sample.csv",
        mime="text/csv",
    )

    uploaded = st.file_uploader("Upload file", type=["csv", "json", "xlsx", "xls"])
    if uploaded:
        try:
            raw_records = parse_raw_uploaded_records(uploaded)
        except Exception as exc:
            st.error(f"Import error: {exc}")
            raw_records = []

        if raw_records:
            columns = list(raw_records[0].keys())
            st.subheader("Column Mapping")
            default_map = default_mapping_from_columns(columns)
            mapping = {}
            options = [""] + columns
            for field, label in FIELD_LABELS.items():
                default_index = options.index(default_map.get(field, "")) if default_map.get(field, "") in options else 0
                mapping[field] = st.selectbox(
                    f"{label} column",
                    options,
                    index=default_index,
                    key=f"map_{field}",
                )

            mapped_rows = [map_raw_row(row, mapping) for row in raw_records]
            valid_rows = [row for row in mapped_rows if is_valid_import_row(row)]
            st.write(f"Rows found: {len(raw_records)}")
            st.write(f"Valid rows: {len(valid_rows)}")
            st.write(f"Invalid rows skipped: {len(raw_records) - len(valid_rows)}")

            if st.button("Import and Generate QRs"):
                success_count = 0
                for row in valid_rows:
                    create_project_and_qr(
                        row["name"],
                        row["roll"],
                        row["project_title"],
                        row["project_description"],
                        row["video_link"],
                        QR_BASE_URL,
                        settings,
                    )
                    success_count += 1
                st.success(f"Imported {success_count} project(s) and generated QRs.")

elif menu == "View All Projects":
    st.header("Registered Projects")
    data = get_all_projects()

    col_a, col_b, col_c = st.columns([1, 1, 2])
    with col_a:
        if st.button("Update All QRs"):
            regenerated = regenerate_all_qrs(QR_BASE_URL, settings)
            settings["last_qr_base_url"] = QR_BASE_URL
            save_settings(settings)
            st.success(f"Updated/Replaced {regenerated} QR(s).")
            data = get_all_projects()
    with col_b:
        if st.button("Replace Existing QRs"):
            replaced = 0
            for row in data:
                qr_path = clean_text(row[7] if len(row) > 7 else "")
                if qr_path:
                    if regenerate_qr_for_row(row, QR_BASE_URL, settings, force=True):
                        replaced += 1
            settings["last_qr_base_url"] = QR_BASE_URL
            save_settings(settings)
            st.success(f"Replaced {replaced} existing QR(s).")
            data = get_all_projects()
    with col_c:
        st.caption(f"QR base URL: {QR_BASE_URL}")

    if data:
        per_row = int(settings.get("grid_columns", 6))
        for start in range(0, len(data), per_row):
            cols = st.columns(per_row)
            chunk = data[start:start + per_row]
            for idx, row in enumerate(chunk):
                with cols[idx]:
                    st.markdown(f"**{clean_text(row[3])}**")
                    st.caption(f"{clean_text(row[1])} | {clean_text(row[2])}")
                    qr_path = row[7] if len(row) > 7 else ""
                    if is_valid_image_path(qr_path):
                        st.image(qr_path, width="stretch")
                    elif clean_text(qr_path):
                        st.caption("QR image missing")
                    video_value = clean_text(row[6] if len(row) > 6 else "")
                    if video_value:
                        st.markdown(f"[Open Link]({video_value})")
    else:
        st.info("No projects registered yet.")

elif menu == "Admin Settings":
    show_admin_settings(settings, QR_BASE_URL)
