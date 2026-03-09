import sqlite3
from datetime import datetime, timedelta

def init_db():
    conn = sqlite3.connect("expo.db")
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            roll TEXT,
            project_title TEXT,
            project_description TEXT,
            website_link TEXT,
            video_link TEXT,
            qr_path TEXT
        )
    """)
    # ensure video_link column exists for older databases
    c.execute("PRAGMA table_info(projects)")
    cols = [row[1] for row in c.fetchall()]
    if 'video_link' not in cols:
        c.execute("ALTER TABLE projects ADD COLUMN video_link TEXT")
    if "created_at" not in cols:
        c.execute("ALTER TABLE projects ADD COLUMN created_at TEXT")
    if "expires_at" not in cols:
        c.execute("ALTER TABLE projects ADD COLUMN expires_at TEXT")

    conn.commit()
    conn.close()


def insert_project(
    name,
    roll,
    title,
    description,
    link,
    video,
    qr_path,
    expiry_enabled=True,
    expiry_days=150,
):
    conn = sqlite3.connect("expo.db")
    c = conn.cursor()
    now = datetime.utcnow()
    expires_text = None
    if expiry_enabled:
        expires = now + timedelta(days=max(1, int(expiry_days)))
        expires_text = expires.isoformat(timespec="seconds")

    c.execute("""
        INSERT INTO projects 
        (name, roll, project_title, project_description, website_link, video_link, qr_path, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name,
        roll,
        title,
        description,
        link,
        video,
        qr_path,
        now.isoformat(timespec="seconds"),
        expires_text,
    ))

    project_id = c.lastrowid
    conn.commit()
    conn.close()
    return project_id


def find_existing_project_id(roll, title):
    conn = sqlite3.connect("expo.db")
    c = conn.cursor()
    c.execute(
        """
        SELECT id
        FROM projects
        WHERE lower(trim(roll)) = lower(trim(?))
          AND lower(trim(project_title)) = lower(trim(?))
        ORDER BY id ASC
        LIMIT 1
        """,
        (roll, title),
    )
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def update_project(project_id, name, roll, title, description, link, video):
    conn = sqlite3.connect("expo.db")
    c = conn.cursor()
    c.execute(
        """
        UPDATE projects
        SET
            name = ?,
            roll = ?,
            project_title = ?,
            project_description = ?,
            website_link = ?,
            video_link = ?
        WHERE id = ?
        """,
        (name, roll, title, description, link, video, project_id),
    )
    conn.commit()
    conn.close()


def deduplicate_projects():
    conn = sqlite3.connect("expo.db")
    c = conn.cursor()
    c.execute(
        """
        SELECT
            id,
            name,
            roll,
            project_title,
            project_description,
            website_link,
            video_link
        FROM projects
        ORDER BY lower(trim(roll)), lower(trim(project_title)), id ASC
        """
    )
    rows = c.fetchall()

    groups = {}
    for row in rows:
        key = ((row[2] or "").strip().lower(), (row[3] or "").strip().lower())
        groups.setdefault(key, []).append(row)

    removed_count = 0
    deduped_groups = 0
    for _, group_rows in groups.items():
        if len(group_rows) < 2:
            continue

        keeper = group_rows[0]
        keeper_id = keeper[0]

        def latest_non_empty(index):
            for candidate in reversed(group_rows):
                value = candidate[index]
                if value is not None and str(value).strip():
                    return value
            return keeper[index]

        c.execute(
            """
            UPDATE projects
            SET
                name = ?,
                roll = ?,
                project_title = ?,
                project_description = ?,
                website_link = ?,
                video_link = ?
            WHERE id = ?
            """,
            (
                latest_non_empty(1),
                latest_non_empty(2),
                latest_non_empty(3),
                latest_non_empty(4),
                latest_non_empty(5),
                latest_non_empty(6),
                keeper_id,
            ),
        )

        duplicate_ids = [row[0] for row in group_rows[1:]]
        c.executemany("DELETE FROM projects WHERE id = ?", [(pid,) for pid in duplicate_ids])
        removed_count += len(duplicate_ids)
        deduped_groups += 1

    conn.commit()
    conn.close()
    return removed_count, deduped_groups


def get_all_projects():
    conn = sqlite3.connect("expo.db")
    c = conn.cursor()

    c.execute(
        """
        SELECT
            id,
            name,
            roll,
            project_title,
            project_description,
            website_link,
            video_link,
            qr_path,
            created_at,
            expires_at
        FROM projects
        """
    )
    data = c.fetchall()

    conn.close()
    return data


def set_project_expiry(project_id, expiry_enabled=True, expiry_days=150):
    conn = sqlite3.connect("expo.db")
    c = conn.cursor()
    expires_text = None
    if expiry_enabled:
        expires_text = (datetime.utcnow() + timedelta(days=max(1, int(expiry_days)))).isoformat(
            timespec="seconds"
        )
    c.execute("UPDATE projects SET expires_at = ? WHERE id = ?", (expires_text, project_id))
    conn.commit()
    conn.close()
