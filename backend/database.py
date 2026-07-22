import json
import logging
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
import hashlib
import os
from urllib.parse import urlparse

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None
    psycopg2_extras = None
    try:
        import psycopg
        import psycopg.rows
    except ImportError:
        psycopg = None
        psycopg_rows = None
    else:
        psycopg_rows = psycopg.rows
else:
    psycopg2_extras = psycopg2.extras
    psycopg = None
    psycopg_rows = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "resumeai.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USING_POSTGRES = DATABASE_URL.lower().startswith("postgres")
VALID_ROLES = {"admin", "recruiter"}
TOP_PYTHON_MATCHES = 8
TOP_QWEN_MATCHES = 3
DEFAULT_QWEN_MODEL = "qwen2.5-coder-3b-instruct"
DEFAULT_QWEN_BASE_URL = "http://localhost:1234/v1"
MATCH_CACHE_VERSION = "2026-07-14a"
_MATCH_DEBUG_PRINTED = False
SCRAPED_JOB_EDITABLE_COLUMNS = [
    "title",
    "location",
    "department",
    "employment_type",
    "job_number",
    "salary",
    "description",
    "responsibilities",
    "qualifications",
    "benefits",
    "additional_notes",
    "active",
]
SCRAPED_JOBS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS scraped_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        url TEXT NOT NULL UNIQUE,
        job_key TEXT,
        source_job_id TEXT,
        location TEXT,
        department TEXT,
        employment_type TEXT,
        job_number TEXT,
        salary TEXT,
        description TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        last_scraped TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source TEXT,
        company TEXT,
        responsibilities TEXT,
        qualifications TEXT,
        benefits TEXT,
        apply_email_or_link TEXT,
        apply_url TEXT,
        workplace_type TEXT,
        posted_date TEXT,
        manual_edited INTEGER NOT NULL DEFAULT 0,
        manual_edited_at TIMESTAMP,
        manual_edited_by TEXT,
        additional_notes TEXT
    )
"""

from .logging_config import get_logger

db_logger = get_logger("database")
matcher_logger = get_logger("matcher")


def _normalize_username_base(value: str) -> str:
    base_value = re.sub(r"[^a-z0-9]+", ".", value.lower().strip())
    base_value = base_value.strip(".")
    return base_value or "user"


def _display_name_from_email(email: str) -> str:
    local_part = email.split("@", 1)[0]
    display_name = re.sub(r"[._-]+", " ", local_part).strip()
    return display_name.title() or "New User"


def _unique_username_from_email(email: str, used_usernames: set[str]) -> str:
    base_username = _normalize_username_base(email.split("@", 1)[0])
    candidate_username = base_username
    suffix = 2

    while candidate_username.lower() in used_usernames:
        candidate_username = f"{base_username}{suffix}"
        suffix += 1

    used_usernames.add(candidate_username.lower())
    return candidate_username


def _fetch_table_columns(conn, table_name: str) -> set[str]:
    cursor = conn.cursor()
    if USING_POSTGRES:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = CURRENT_SCHEMA()
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        return {str(row["column_name"]) for row in cursor.fetchall()}

    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


class _PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None
        self.rowcount = -1

    def _normalize_sql(self, sql: str) -> str:
        normalized = sql.strip()
        if normalized.startswith("PRAGMA table_info("):
            table_name = normalized[len("PRAGMA table_info("):-1].strip('"')
            return (
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = CURRENT_SCHEMA() AND table_name = '%s' "
                "ORDER BY ordinal_position"
            ) % table_name
        if "DATE('now')" in normalized:
            normalized = normalized.replace("DATE('now')", "CURRENT_DATE")
        if "json_extract(" in normalized:
            normalized = normalized.replace(
                "CAST(json_extract(mc.match_json, '$.match_score') AS REAL)",
                "CAST((mc.match_json::jsonb ->> 'match_score') AS REAL)",
            )
            normalized = normalized.replace(
                "CAST(json_extract(mc.match_json, '$.match_percentage') AS REAL)",
                "CAST((mc.match_json::jsonb ->> 'match_percentage') AS REAL)",
            )
        normalized = normalized.replace(
            """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'recruiter',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
            """CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'recruiter',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        )
        normalized = normalized.replace(
            """CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            candidate TEXT,
            current_title TEXT,
            email TEXT,
            phone TEXT,
            employment_status TEXT,
            graduation_year TEXT,
            total_experience_years REAL,
            career_span_years REAL,
            skills TEXT,
            normalized_skills TEXT,
            current_position TEXT,
            current_company TEXT,
            resume_summary TEXT,
            needs_review TEXT,
            industries TEXT,
            certifications TEXT,
            education TEXT,
            keywords TEXT,
            file_hash TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
            """CREATE TABLE IF NOT EXISTS candidates (
            id SERIAL PRIMARY KEY,
            filename TEXT,
            candidate TEXT,
            current_title TEXT,
            email TEXT,
            phone TEXT,
            employment_status TEXT,
            graduation_year TEXT,
            total_experience_years REAL,
            career_span_years REAL,
            skills TEXT,
            normalized_skills TEXT,
            current_position TEXT,
            current_company TEXT,
            resume_summary TEXT,
            needs_review TEXT,
            industries TEXT,
            certifications TEXT,
            education TEXT,
            keywords TEXT,
            file_hash TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        )
        normalized = normalized.replace(
            """CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_email TEXT,
            action TEXT NOT NULL,
            details TEXT,
            status TEXT NOT NULL
        )""",
            """CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_email TEXT,
            action TEXT NOT NULL,
            details TEXT,
            status TEXT NOT NULL
        )""",
        )
        normalized = normalized.replace(
            """CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT UNIQUE,
            title TEXT NOT NULL,
            department TEXT,
            location TEXT,
            job_type TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            description TEXT,
            required_skills TEXT,
            preferred_skills TEXT,
            years_required TEXT,
            industry TEXT,
            certifications TEXT,
            keywords TEXT,
            salary TEXT,
            created_by TEXT,
            updated_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
            """CREATE TABLE IF NOT EXISTS jobs (
            id SERIAL PRIMARY KEY,
            job_id TEXT UNIQUE,
            title TEXT NOT NULL,
            department TEXT,
            location TEXT,
            job_type TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            description TEXT,
            required_skills TEXT,
            preferred_skills TEXT,
            years_required TEXT,
            industry TEXT,
            certifications TEXT,
            keywords TEXT,
            salary TEXT,
            created_by TEXT,
            updated_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        )
        normalized = normalized.replace(
            """CREATE TABLE IF NOT EXISTS match_cache (
            job_id INTEGER NOT NULL,
            candidate_id INTEGER NOT NULL,
            job_fingerprint TEXT NOT NULL,
            candidate_fingerprint TEXT NOT NULL,
            is_stale INTEGER NOT NULL DEFAULT 0,
            match_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (job_id, candidate_id)
            )""",
            """CREATE TABLE IF NOT EXISTS match_cache (
            job_id INTEGER NOT NULL,
            candidate_id INTEGER NOT NULL,
            job_fingerprint TEXT NOT NULL,
            candidate_fingerprint TEXT NOT NULL,
            is_stale INTEGER NOT NULL DEFAULT 0,
            match_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (job_id, candidate_id)
        )""",
        )
        normalized = normalized.replace(
            """CREATE TABLE IF NOT EXISTS scraped_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            url TEXT NOT NULL UNIQUE,
            job_key TEXT,
            source_job_id TEXT,
            location TEXT,
            department TEXT,
            employment_type TEXT,
            job_number TEXT,
            salary TEXT,
            description TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            last_scraped TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source TEXT,
            company TEXT,
            responsibilities TEXT,
            qualifications TEXT,
            benefits TEXT,
            apply_email_or_link TEXT,
            apply_url TEXT,
            workplace_type TEXT,
            posted_date TEXT,
            manual_edited INTEGER NOT NULL DEFAULT 0,
            manual_edited_at TIMESTAMP,
            manual_edited_by TEXT,
            additional_notes TEXT
        )""",
            """CREATE TABLE IF NOT EXISTS scraped_jobs (
            id SERIAL PRIMARY KEY,
            title TEXT,
            url TEXT NOT NULL UNIQUE,
            job_key TEXT,
            source_job_id TEXT,
            location TEXT,
            department TEXT,
            employment_type TEXT,
            job_number TEXT,
            salary TEXT,
            description TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            last_scraped TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source TEXT,
            company TEXT,
            responsibilities TEXT,
            qualifications TEXT,
            benefits TEXT,
            apply_email_or_link TEXT,
            apply_url TEXT,
            workplace_type TEXT,
            posted_date TEXT,
            manual_edited INTEGER NOT NULL DEFAULT 0,
            manual_edited_at TIMESTAMP,
            manual_edited_by TEXT,
            additional_notes TEXT
        )""",
        )
        normalized = normalized.replace("?", "%s")
        normalized = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bAUTOINCREMENT\b", "", normalized, flags=re.IGNORECASE)
        return normalized

    def execute(self, sql, params=None):
        translated = self._normalize_sql(sql)
        params = tuple(params or ())
        if translated.lstrip().upper().startswith("INSERT INTO USERS") and "RETURNING" not in translated.upper():
            translated = translated.rstrip() + " RETURNING id"
        elif translated.lstrip().upper().startswith("INSERT INTO CANDIDATES") and "RETURNING" not in translated.upper():
            translated = translated.rstrip() + " RETURNING id"
        elif translated.lstrip().upper().startswith("INSERT INTO AUDIT_LOGS") and "RETURNING" not in translated.upper():
            translated = translated.rstrip() + " RETURNING id"
        elif translated.lstrip().upper().startswith("INSERT INTO JOBS") and "RETURNING" not in translated.upper():
            translated = translated.rstrip() + " RETURNING id"
        self._cursor.execute(translated, params)
        self.rowcount = self._cursor.rowcount
        if translated.lstrip().upper().startswith("INSERT INTO") and "RETURNING ID" in translated.upper():
            try:
                row = self._cursor.fetchone()
            except Exception:
                row = None
            if row:
                self.lastrowid = row[0] if not isinstance(row, dict) else next(iter(row.values()))
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __getattr__(self, item):
        return getattr(self._cursor, item)


class _PostgresConnection:
    def __init__(self, conn):
        self._conn = conn
        self.autocommit = False

    def cursor(self):
        if psycopg2_extras is not None:
            return _PostgresCursor(self._conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor))
        return _PostgresCursor(self._conn.cursor(row_factory=psycopg_rows.dict_row))

    def commit(self):
        return self._conn.commit()

    def close(self):
        return self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self.close()
        return False

    def __getattr__(self, item):
        return getattr(self._conn, item)


def get_connection():
    DB_PATH.parent.mkdir(exist_ok=True)
    if USING_POSTGRES:
        if psycopg2 is not None:
            conn = psycopg2.connect(DATABASE_URL)
        elif psycopg is not None:
            conn = psycopg.connect(DATABASE_URL)
        else:
            raise RuntimeError("A PostgreSQL driver is required when DATABASE_URL is set.")
        db_logger.info("PostgreSQL connected")
        return _PostgresConnection(conn)

    conn = sqlite3.connect(DB_PATH)
    db_logger.info("SQLite connected")
    return conn


def ensure_scraped_job_edit_columns(conn: sqlite3.Connection) -> None:
    columns = _fetch_table_columns(conn, "scraped_jobs")
    cursor = conn.cursor()
    for column_name, column_definition in {
        "manual_edited": "INTEGER NOT NULL DEFAULT 0",
        "manual_edited_at": "TIMESTAMP",
        "manual_edited_by": "TEXT",
    }.items():
        if column_name not in columns:
            if USING_POSTGRES:
                cursor.execute(f"ALTER TABLE scraped_jobs ADD COLUMN IF NOT EXISTS {column_name} {column_definition}")
            else:
                cursor.execute(f"ALTER TABLE scraped_jobs ADD COLUMN {column_name} {column_definition}")


def init_db():
    start = time.perf_counter()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'recruiter',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    user_columns = _fetch_table_columns(conn, "users")
    if "name" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN name TEXT")
    if "username" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")
    if "role" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'recruiter'")
    if "is_locked" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN is_locked INTEGER NOT NULL DEFAULT 0")
    if "last_login" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_login TIMESTAMP")

    cursor.execute("""
        UPDATE users
        SET role = 'recruiter'
        WHERE role IS NULL OR role NOT IN ('admin', 'recruiter')
    """)

    cursor.execute("""
        SELECT id, email, name, username
        FROM users
        ORDER BY id
    """)
    existing_usernames: set[str] = set()
    for row in cursor.fetchall():
        normalized_username = str(row[3] or "").strip().lower()
        desired_username = normalized_username or _unique_username_from_email(str(row[1] or ""), existing_usernames)
        if normalized_username and normalized_username in existing_usernames:
            desired_username = _unique_username_from_email(str(row[1] or ""), existing_usernames)
        else:
            existing_usernames.add(desired_username.lower())

        desired_name = str(row[2] or "").strip() or _display_name_from_email(str(row[1] or ""))

        if desired_name != row[2] or desired_username != row[3]:
            cursor.execute("""
                UPDATE users
                SET name = ?, username = ?
                WHERE id = ?
            """, (desired_name, desired_username, row[0]))

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username
        ON users(username)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            candidate TEXT,
            current_title TEXT,
            email TEXT,
            phone TEXT,
            employment_status TEXT,
            graduation_year TEXT,
            total_experience_years REAL,
            career_span_years REAL,
            skills TEXT,
            normalized_skills TEXT,
            current_position TEXT,
            current_company TEXT,
            resume_summary TEXT,
            needs_review TEXT,
            industries TEXT,
            certifications TEXT,
            education TEXT,
            keywords TEXT,
            file_hash TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    candidate_columns = _fetch_table_columns(conn, "candidates")
    for column_name, column_definition in {
        "current_title": "TEXT",
        "industries": "TEXT",
        "certifications": "TEXT",
        "education": "TEXT",
        "keywords": "TEXT",
        "file_hash": "TEXT",
    }.items():
        if column_name not in candidate_columns:
            cursor.execute(f"ALTER TABLE candidates ADD COLUMN {column_name} {column_definition}")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_email TEXT,
            action TEXT NOT NULL,
            details TEXT,
            status TEXT NOT NULL
        )
    """)

    audit_log_columns = _fetch_table_columns(conn, "audit_logs")
    if "timestamp" not in audit_log_columns:
        cursor.execute("ALTER TABLE audit_logs ADD COLUMN timestamp TEXT")
    if "user_email" not in audit_log_columns:
        cursor.execute("ALTER TABLE audit_logs ADD COLUMN user_email TEXT")
    if "status" not in audit_log_columns:
        cursor.execute("ALTER TABLE audit_logs ADD COLUMN status TEXT NOT NULL DEFAULT 'success'")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT UNIQUE,
            title TEXT NOT NULL,
            department TEXT,
            location TEXT,
            job_type TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            description TEXT,
            required_skills TEXT,
            preferred_skills TEXT,
            years_required TEXT,
            industry TEXT,
            certifications TEXT,
            keywords TEXT,
            salary TEXT,
            created_by TEXT,
            updated_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS match_cache (
            job_id INTEGER NOT NULL,
            candidate_id INTEGER NOT NULL,
            job_fingerprint TEXT NOT NULL,
            candidate_fingerprint TEXT NOT NULL,
            is_stale INTEGER NOT NULL DEFAULT 0,
            match_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (job_id, candidate_id)
        )
    """)

    job_columns = _fetch_table_columns(conn, "jobs")
    for column_name, column_definition in {
        "job_id": "TEXT UNIQUE",
        "department": "TEXT",
        "location": "TEXT",
        "job_type": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'open'",
        "description": "TEXT",
        "required_skills": "TEXT",
        "preferred_skills": "TEXT",
        "years_required": "TEXT",
        "industry": "TEXT",
            "certifications": "TEXT",
            "keywords": "TEXT",
            "salary": "TEXT",
            "created_by": "TEXT",
            "updated_by": "TEXT",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    }.items():
        if column_name not in job_columns:
            cursor.execute(f"ALTER TABLE jobs ADD COLUMN {column_name} {column_definition}")

    match_cache_columns = _fetch_table_columns(conn, "match_cache")
    if "is_stale" not in match_cache_columns:
        cursor.execute("ALTER TABLE match_cache ADD COLUMN is_stale INTEGER NOT NULL DEFAULT 0")

    cursor.execute(SCRAPED_JOBS_TABLE_SQL)
    scraped_job_columns = _fetch_table_columns(conn, "scraped_jobs")
    for column_name, column_definition in {
        "job_key": "TEXT",
        "source_job_id": "TEXT",
        "apply_url": "TEXT",
        "workplace_type": "TEXT",
        "posted_date": "TEXT",
        "manual_edited": "INTEGER NOT NULL DEFAULT 0",
        "manual_edited_at": "TIMESTAMP",
        "manual_edited_by": "TEXT",
        "source": "TEXT",
        "company": "TEXT",
        "responsibilities": "TEXT",
        "qualifications": "TEXT",
        "benefits": "TEXT",
        "apply_email_or_link": "TEXT",
        "additional_notes": "TEXT",
    }.items():
        if column_name not in scraped_job_columns:
            if USING_POSTGRES:
                cursor.execute(f"ALTER TABLE scraped_jobs ADD COLUMN IF NOT EXISTS {column_name} {column_definition}")
            else:
                cursor.execute(f"ALTER TABLE scraped_jobs ADD COLUMN {column_name} {column_definition}")

    conn.commit()
    conn.close()
    db_logger.info("Database initialization complete in %.2f seconds", time.perf_counter() - start)


def validate_role(role: str) -> str:
    normalized_role = role.lower().strip()
    if normalized_role not in VALID_ROLES:
        raise ValueError("Role must be either admin or recruiter.")

    return normalized_role


def create_user(
    email: str,
    password_hash: str,
    role: str = "recruiter",
    name: str | None = None,
    username: str | None = None,
) -> int:
    normalized_role = validate_role(role)
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT username
        FROM users
        WHERE username IS NOT NULL AND TRIM(username) != ''
    """)
    used_usernames = {str(row["username"]).strip().lower() for row in cursor.fetchall()}
    generated_username = _unique_username_from_email(email, used_usernames)
    generated_name = _display_name_from_email(email)
    normalized_username = username.strip().lower() if username and username.strip() else generated_username
    normalized_name = name.strip() if name and name.strip() else generated_name

    cursor.execute("""
        INSERT INTO users (name, username, email, password_hash, role, is_locked)
        VALUES (?, ?, ?, ?, ?, 0)
    """, (normalized_name, normalized_username, email, password_hash, normalized_role))

    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    db_logger.info("User created | user_id=%s | email=%s | role=%s", user_id, email, normalized_role)

    return user_id


def update_user_role(email: str, role: str) -> None:
    normalized_role = validate_role(role)
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET role = ?
        WHERE email = ?
    """, (normalized_role, email))

    conn.commit()
    conn.close()
    db_logger.info("Role changed | email=%s | role=%s", email, normalized_role)


def get_user_by_username(username: str):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, username, email, password_hash, role, is_locked, last_login, created_at
        FROM users
        WHERE LOWER(username) = LOWER(?)
    """, (username,))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def get_user_by_email(email: str):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, username, email, password_hash, role, is_locked, last_login, created_at
        FROM users
        WHERE email = ?
    """, (email,))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def get_user_by_id(user_id: int):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, username, email, role, is_locked, last_login, created_at
        FROM users
        WHERE id = ?
    """, (user_id,))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def find_duplicate_candidate(filename: str, email: str, file_hash: str | None = None):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    duplicate_checks = []
    params = []

    if file_hash:
        duplicate_checks.append("file_hash = ?")
        params.append(file_hash)

    if filename:
        duplicate_checks.append("filename = ?")
        params.append(filename)

    if email:
        duplicate_checks.append("LOWER(email) = LOWER(?)")
        params.append(email)

    if not duplicate_checks:
        conn.close()
        return None

    cursor.execute(f"""
        SELECT id, filename, email, file_hash
        FROM candidates
        WHERE {" OR ".join(duplicate_checks)}
        ORDER BY id DESC
        LIMIT 1
    """, params)

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def save_candidate(filename: str, parsed_data: dict, file_hash: str | None = None) -> int | None:
    email = str(parsed_data.get("Email", "")).strip()
    if find_duplicate_candidate(filename, email, file_hash):
        db_logger.warning("Duplicate candidate skipped | filename=%s | email=%s", filename, email)
        return None

    candidate_name = str(parsed_data.get("Candidate", "")).strip()
    current_title = str(parsed_data.get("Current Position", "")).strip()
    skills_text = str(parsed_data.get("Normalized Skills", "") or parsed_data.get("Skills", "")).strip()
    industries = _split_certifications(parsed_data.get("Industries", ""))
    certifications = _split_certifications(parsed_data.get("Certifications", ""))
    education = _safe_text_blob(parsed_data.get("Education", ""))
    keywords = _simple_keywords(
        " ".join([
            candidate_name,
            current_title,
            skills_text,
            str(parsed_data.get("Resume Summary", "")),
            education,
        ]),
        18,
    )

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO candidates (
            filename,
            candidate,
            current_title,
            email,
            phone,
            employment_status,
            graduation_year,
            total_experience_years,
            career_span_years,
            skills,
            normalized_skills,
            current_position,
            current_company,
            resume_summary,
            needs_review,
            industries,
            certifications,
            education,
            keywords,
            file_hash,
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        filename,
        candidate_name,
        current_title,
        email,
        parsed_data.get("Phone Number", ""),
        parsed_data.get("Employment Status", ""),
        parsed_data.get("Graduation Year", ""),
        parsed_data.get("Total Experience (Years)", 0),
        parsed_data.get("Career Span (Years)", 0),
        parsed_data.get("Skills", ""),
        parsed_data.get("Normalized Skills", ""),
        parsed_data.get("Current Position", ""),
        parsed_data.get("Current Company", ""),
        parsed_data.get("Resume Summary", ""),
        parsed_data.get("Needs Review", ""),
        ", ".join(industries),
        ", ".join(certifications),
        education,
        ", ".join(keywords),
        file_hash,
        json.dumps(parsed_data)
    ))

    candidate_id = cursor.lastrowid
    conn.commit()
    conn.close()
    touch_candidate_match_staleness(candidate_id)
    db_logger.info("Candidate saved (ID=%s)", candidate_id)

    return candidate_id


def get_candidates():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            filename,
            candidate,
            email,
            phone,
            employment_status,
            graduation_year,
            total_experience_years,
            career_span_years,
            skills,
            normalized_skills,
            current_position,
            current_company,
            resume_summary,
            needs_review,
            current_title,
            industries,
            certifications,
            education,
            keywords,
            created_at
        FROM candidates
        ORDER BY id DESC
    """)

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return rows


def split_normalized_skills(skills: str | None) -> list[str]:
    if not skills:
        return []

    normalized_skills = []
    for skill in str(skills).replace(";", ",").split(","):
        clean_skill = skill.strip()
        if clean_skill and clean_skill.lower() not in {"none", "not found", "skills not found"}:
            normalized_skills.append(clean_skill)

    return normalized_skills


def normalize_skill_key(skill: str | None) -> str:
    return re.sub(r"[^a-z0-9+#.]+", " ", str(skill or "").lower()).strip()


def split_job_skills(skills: str | None) -> list[str]:
    if not skills:
        return []

    parsed_skills = []
    for skill in re.split(r"[,;\n]+", str(skills)):
        clean_skill = skill.strip()
        if clean_skill:
            parsed_skills.append(clean_skill)

    return parsed_skills


def _safe_text_blob(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_safe_text_blob(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_safe_text_blob(item) for item in value.values())
    return re.sub(r"\s+", " ", str(value)).strip()


def _simple_keywords(text: str | None, limit: int = 12) -> list[str]:
    tokens = []
    for token in re.findall(r"[A-Za-z0-9#+.&-]{3,}", str(text or "").lower()):
        if token not in tokens:
            tokens.append(token)
    return tokens[:limit]


COMMON_SKILL_PHRASES = [
    "excel",
    "microsoft excel",
    "word",
    "powerpoint",
    "financial modeling",
    "financial analysis",
    "accounting",
    "bookkeeping",
    "audit",
    "auditing",
    "sql",
    "python",
    "data analysis",
    "data analytics",
    "power bi",
    "tableau",
    "vba",
    "access",
    "reconciliation",
    "forecasting",
    "budgeting",
    "valuation",
    "reporting",
    "compliance",
    "recruiting",
    "talent acquisition",
    "salesforce",
    "crm",
    "project management",
    "risk management",
    "operations",
    "client service",
    "communication",
]


def _extract_skill_phrases(text: str | None, limit: int = 12) -> list[str]:
    normalized = str(text or "").lower()
    found = []
    for phrase in COMMON_SKILL_PHRASES:
        if phrase in normalized and phrase not in found:
            found.append(phrase)
    for token in re.findall(r"[A-Za-z0-9#+.&-]{3,}", normalized):
        if token not in found:
            found.append(token)
    return found[:limit]


def _has_meaningful_text(value: str | None) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.lower() not in {"not found", "n/a", "none"}


def _extract_years_required(value) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def _extract_experience_keywords(text: str | None, limit: int = 10) -> list[str]:
    source = str(text or "").lower()
    keywords = []
    for phrase in COMMON_SKILL_PHRASES:
        if phrase in source and phrase not in keywords:
            keywords.append(phrase)
    for token in re.findall(r"\b[a-z][a-z&+/.-]{2,}\b", source):
        if token not in keywords and token not in {"and", "the", "with", "for", "from", "this", "that", "will", "you"}:
            keywords.append(token)
    return keywords[:limit]


def _split_certifications(value) -> list[str]:
    text = _safe_text_blob(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,;\n|]+", text) if item.strip()][:12]


def _create_qwen_client():
    if OpenAI is None:
        return None
    try:
        return OpenAI(base_url=DEFAULT_QWEN_BASE_URL, api_key="lm-studio")
    except Exception:
        return None


def _stable_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fingerprint(value: dict) -> str:
    payload = {
        "version": MATCH_CACHE_VERSION,
        "value": value,
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _extract_json_object(text: str) -> dict:
    content = str(text or "").strip().replace("```json", "").replace("```", "").strip()
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise json.JSONDecodeError("No JSON object found", content, 0)

    json_text = content[start : end + 1]
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", json_text)
        parsed = json.loads(cleaned)

    if not isinstance(parsed, dict):
        raise ValueError("LLM response was not a JSON object.")

    return parsed


def _compact_text(value: str | None, limit: int) -> str:
    """Keep prompts small by trimming noisy whitespace and capping text length."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _candidate_match_context(candidate: dict) -> dict:
    """Build a privacy-aware snapshot of candidate data for matching."""
    parsed_resume = {}
    raw_json = candidate.get("raw_json")
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            parsed_resume = json.loads(raw_json)
        except json.JSONDecodeError:
            parsed_resume = {}
    elif isinstance(raw_json, dict):
        parsed_resume = raw_json

    return {
        "candidate_name": _compact_text(candidate.get("candidate"), 120),
        "current_position": _compact_text(candidate.get("current_position"), 160),
        "current_company": _compact_text(candidate.get("current_company"), 160),
        "years_experience": candidate.get("total_experience_years"),
        "career_span_years": candidate.get("career_span_years"),
        "skills": split_normalized_skills(candidate.get("normalized_skills")) or split_normalized_skills(candidate.get("skills")),
        "summary": _compact_text(candidate.get("resume_summary"), 1200),
        "employment_status": _compact_text(candidate.get("employment_status"), 80),
        "needs_review": _compact_text(candidate.get("needs_review"), 40),
        "industries": _split_certifications(candidate.get("industries")),
        "certifications": _split_certifications(candidate.get("certifications")),
        "education": _compact_text(candidate.get("education"), 1200),
        "keywords": _simple_keywords(candidate.get("keywords"), 18),
        "skill_phrases": _extract_skill_phrases(
            " ".join([
                _compact_text(candidate.get("skills"), 1200),
                _compact_text(candidate.get("normalized_skills"), 1200),
                _compact_text(candidate.get("resume_summary"), 1200),
                _compact_text(candidate.get("education"), 1200),
                _compact_text(candidate.get("current_position"), 160),
                _safe_text_blob(candidate.get("raw_json")),
            ]),
            20,
        ),
        "resume_text": _compact_text(
            parsed_resume.get("Resume Summary")
            or parsed_resume.get("Resume Text")
            or parsed_resume.get("Parsed Text")
            or parsed_resume.get("full_text"),
            3500,
        ),
        "extract_status": {
            "current_position": "extracted" if _has_meaningful_text(candidate.get("current_position")) else "not extracted",
            "skills": "extracted" if split_normalized_skills(candidate.get("normalized_skills")) or split_normalized_skills(candidate.get("skills")) else "not extracted",
            "years_experience": "extracted" if candidate.get("total_experience_years") not in {None, ""} else "not extracted",
            "certifications": "extracted" if _has_meaningful_text(candidate.get("certifications")) else "not extracted",
            "education": "extracted" if _has_meaningful_text(candidate.get("education")) else "not extracted",
            "industry": "extracted" if _has_meaningful_text(candidate.get("industries")) else "not extracted",
            "keywords": "extracted" if _has_meaningful_text(candidate.get("keywords")) else "not extracted",
            "summary": "extracted" if _has_meaningful_text(candidate.get("resume_summary")) else "not extracted",
            "resume_text": "extracted" if _has_meaningful_text(parsed_resume.get("Resume Summary") or parsed_resume.get("Resume Text") or parsed_resume.get("Parsed Text") or parsed_resume.get("full_text")) else "not extracted",
        },
    }


def _job_match_context(job: dict) -> dict:
    """Build a compact job snapshot for the recruiter prompt."""
    description_text = _compact_text(job.get("description"), 6000)
    title_text = _compact_text(job.get("title"), 160)
    explicit_required = split_job_skills(job.get("required_skills"))
    explicit_preferred = split_job_skills(job.get("preferred_skills"))
    extracted_required = _extract_skill_phrases(
        " ".join([
            description_text,
            _compact_text(job.get("department"), 120),
            _compact_text(job.get("industry"), 80),
            _compact_text(job.get("certifications"), 200),
            _compact_text(job.get("keywords"), 200),
        ]),
        18,
    )
    extracted_preferred = _extract_skill_phrases(
        " ".join([
            description_text,
            _compact_text(job.get("qualifications"), 1200),
            _compact_text(job.get("requirements"), 1200),
            _compact_text(job.get("preferred_qualifications"), 1200),
            _compact_text(job.get("preferred_qualifications"), 1200),
        ]),
        12,
    )
    required_skills = list(dict.fromkeys(explicit_required + extracted_required))
    preferred_skills = list(dict.fromkeys(explicit_preferred + extracted_preferred))
    years_required = _extract_years_required(job.get("years_required"))
    return {
        "title": title_text,
        "department": _compact_text(job.get("department"), 120),
        "location": _compact_text(job.get("location"), 120),
        "job_type": _compact_text(job.get("job_type"), 80),
        "status": _compact_text(job.get("status"), 40),
        "description": description_text,
        "required_skills": required_skills,
        "preferred_skills": preferred_skills,
        "years_required": years_required,
        "industry": _compact_text(job.get("industry"), 80),
        "certifications": _split_certifications(job.get("certifications")),
        "keywords": _simple_keywords(job.get("keywords"), 18),
        "description_skills": _extract_skill_phrases(description_text, 12),
        "extract_status": {
            "required_skills": "extracted" if required_skills else "not extracted",
            "preferred_skills": "extracted" if preferred_skills else "not extracted",
            "years_required": "extracted" if years_required is not None else "not specified",
            "certifications": "extracted" if _split_certifications(job.get("certifications")) else "not specified",
            "education": "not specified",
            "industry": "extracted" if _has_meaningful_text(job.get("industry")) else "not specified",
            "location": "extracted" if _has_meaningful_text(job.get("location")) else "not specified",
        },
    }


def _normalize_match_level(score: int) -> str:
    if score >= 85:
        return "Excellent Match"
    if score >= 70:
        return "Strong Match"
    if score >= 45:
        return "Possible Match"
    return "Weak Match"


def _normalize_recommended_action(score: int) -> str:
    if score >= 70:
        return "Interview"
    if score >= 45:
        return "Review"
    return "Reject"


def _get_cached_match(job_id: int, candidate_id: int, job_fingerprint: str, candidate_fingerprint: str) -> dict | None:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT match_json, is_stale, created_at, updated_at
        FROM match_cache
        WHERE job_id = ? AND candidate_id = ? AND job_fingerprint = ? AND candidate_fingerprint = ?
    """, (job_id, candidate_id, job_fingerprint, candidate_fingerprint))
    row = cursor.fetchone()
    conn.close()
    if not row:
        matcher_logger.debug("Match cache miss | job_id=%s | candidate_id=%s", job_id, candidate_id)
        return None
    try:
        cached = json.loads(row["match_json"])
        if isinstance(cached, dict):
            if row["is_stale"]:
                matcher_logger.info("Stale match | job_id=%s | candidate_id=%s", job_id, candidate_id)
                cached["is_stale"] = True
                return cached
            matcher_logger.info("Match cache hit | job_id=%s | candidate_id=%s", job_id, candidate_id)
            cached["is_cached"] = True
            cached["cached"] = True
            cached["is_stale"] = bool(row["is_stale"])
            cached["cache_created_at"] = row["created_at"]
            cached["cache_updated_at"] = row["updated_at"]
            return cached
        return None
    except json.JSONDecodeError:
        return None


def _safe_string_list(value, limit: int = 8) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in re.split(r"[,;\n]+", value) if item.strip()]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = []
    return items[:limit]


def _normalize_qwen_score(value, python_score: int) -> tuple[int, object]:
    if value in {None, ""}:
        return python_score, value
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return python_score, value
    if 0 < numeric < 1:
        numeric *= 100
    score = int(round(numeric))
    if score <= 0 and python_score > 0:
        return python_score, value
    return max(0, min(100, score)), value


def _normalize_match_level(score: int) -> str:
    if score >= 85:
        return "Excellent Match"
    if score >= 70:
        return "Strong Match"
    if score >= 45:
        return "Possible Match"
    return "Weak Match"


def _normalize_recommendation(label: str | None, score: int) -> str:
    cleaned = str(label or "").strip().lower()
    if cleaned in {"strong match", "possible match", "weak match"}:
        return cleaned
    return (
        "strong match" if score >= 70 else "possible match" if score >= 45 else "weak match"
    )


def _create_python_match(job: dict, candidate: dict) -> dict:
    job_struct = _job_match_context(job)
    candidate_struct = _candidate_match_context(candidate)

    required_skills = job_struct["required_skills"]
    preferred_skills = job_struct["preferred_skills"]
    candidate_skills = candidate_struct["skills"]
    candidate_skill_phrases = candidate_struct["skill_phrases"]
    candidate_skill_keys = {normalize_skill_key(skill) for skill in candidate_skills}
    candidate_phrase_keys = {normalize_skill_key(skill) for skill in candidate_skill_phrases}

    matched_skills = [skill for skill in required_skills if normalize_skill_key(skill) in candidate_skill_keys or normalize_skill_key(skill) in candidate_phrase_keys]
    missing_skills = [skill for skill in required_skills if normalize_skill_key(skill) not in candidate_skill_keys and normalize_skill_key(skill) not in candidate_phrase_keys]
    preferred_matches = [skill for skill in preferred_skills if normalize_skill_key(skill) in candidate_skill_keys or normalize_skill_key(skill) in candidate_phrase_keys]
    missing_preferred = [skill for skill in preferred_skills if normalize_skill_key(skill) not in candidate_skill_keys and normalize_skill_key(skill) not in candidate_phrase_keys]

    # The Python layer ranks everyone first using direct signals from the job and
    # resume data. This keeps the system fast and prevents unnecessary model calls.
    job_blob = _safe_text_blob(
        [
            job_struct["title"],
            job_struct["department"],
            job_struct["location"],
            job_struct["job_type"],
            job_struct["status"],
            job_struct["description"],
            job_struct["industry"],
            job_struct["certifications"],
            job_struct["keywords"],
            required_skills,
            preferred_skills,
        ]
    ).lower()
    candidate_blob = _safe_text_blob(
        [
            candidate_struct["candidate_name"],
            candidate_struct["current_position"],
            candidate_struct["current_company"],
            candidate_struct["summary"],
            candidate_struct["resume_text"],
            candidate_struct["skills"],
            candidate_struct["industries"],
            candidate_struct["certifications"],
            candidate_struct["education"],
            candidate_struct["keywords"],
        ]
    ).lower()

    job_terms = set(re.findall(r"[a-z0-9#+.&-]{3,}", job_blob))
    candidate_terms = set(re.findall(r"[a-z0-9#+.&-]{3,}", candidate_blob))

    title_terms = set(re.findall(r"[a-z0-9#+.&-]{3,}", job_struct["title"].lower()))
    position_terms = set(re.findall(r"[a-z0-9#+.&-]{3,}", candidate_struct["current_position"].lower()))
    title_overlap = len(title_terms & position_terms)
    title_exact = job_struct["title"].lower() == candidate_struct["current_position"].lower()
    title_close_match = bool(title_terms & position_terms)

    skill_score = 0.0
    if required_skills:
        required_ratio = len(matched_skills) / len(required_skills)
        skill_score += required_ratio * 50
        if matched_skills:
            skill_score += min(6, len(matched_skills) * 1.2)
    if preferred_skills:
        skill_score += (len(preferred_matches) / max(1, len(preferred_skills))) * 12

    title_score = 0.0
    if title_exact:
        title_score = 22
    elif title_close_match:
        title_score = min(18, title_overlap * 6)
    else:
        title_score = min(10, len(job_terms & candidate_terms) * 0.9)
    if title_terms and any(term in candidate_struct["current_position"].lower() for term in title_terms):
        title_score += 2

    relevance_score = min(10, len(job_terms & candidate_terms) * 0.75)
    description_skill_matches = [skill for skill in job_struct["description_skills"] if normalize_skill_key(skill) in candidate_skill_keys or normalize_skill_key(skill) in candidate_phrase_keys]
    if description_skill_matches:
        relevance_score += min(6, len(description_skill_matches) * 1.2)

    years_required = 0.0
    years_required = float(job_struct["years_required"] or 0)

    try:
        years_experience = float(candidate.get("total_experience_years") or 0)
    except (TypeError, ValueError):
        years_experience = 0.0

    years_score = 0.0
    if years_required > 0:
        delta = years_experience - years_required
        years_score = 12 if delta >= 2 else 10 if delta >= 0 else 6 if delta >= -1 else 2 if delta >= -2 else 0
    elif years_experience > 0:
        years_score = min(10, years_experience * 0.9)
    years_detail = {
        "required_years": years_required or None,
        "candidate_years": years_experience or None,
        "meets_requirement": bool(years_required > 0 and years_experience >= years_required),
    }

    location_score = 0.0
    job_location = str(job_struct["location"]).lower()
    candidate_location_blob = " ".join(
        filter(None, [
            candidate_struct["current_company"],
            candidate_struct["summary"],
            candidate_struct["resume_text"],
        ])
    ).lower()
    if job_location in {"remote", "hybrid"}:
        location_score = 4 if "remote" in candidate_location_blob or "hybrid" in candidate_location_blob else 2
    elif job_location and job_location in candidate_location_blob:
        location_score = 6
    location_detail = {
        "job_location": job_struct["location"] or None,
        "candidate_location_match": bool(location_score > 0),
    }

    certification_score = 0.0
    job_certs = job_struct["certifications"]
    candidate_certs = candidate_struct["certifications"]
    candidate_cert_keys = {normalize_skill_key(item) for item in candidate_certs}
    cert_matches = [cert for cert in job_certs if normalize_skill_key(cert) in candidate_cert_keys]
    if job_certs:
        certification_score = (len(cert_matches) / len(job_certs)) * 8
        if not cert_matches:
            certification_score = max(0, certification_score - 2)
    certification_detail = {
        "required_certifications": job_certs,
        "matched_certifications": cert_matches,
        "missing_certifications": [cert for cert in job_certs if cert not in cert_matches],
    }

    industry_score = 0.0
    job_industry = job_struct["industry"].lower()
    candidate_industry_blob = " ".join(candidate_struct["industries"]).lower()
    if job_industry and job_industry in candidate_industry_blob:
        industry_score = 8
    elif job_industry and job_industry in candidate_blob:
        industry_score = 5
    industry_detail = {
        "job_industry": job_struct["industry"] or None,
        "candidate_industries": candidate_struct["industries"],
        "industry_match": bool(industry_score > 0),
    }

    education_score = 0.0
    education_text = candidate_struct["education"].lower()
    if education_text:
        degree_terms = {"bachelor", "bachelors", "master", "masters", "mba", "phd", "ph.d", "ms", "ma", "bs", "ba", "bba", "jd", "md", "cfa", "cpa"}
        if any(term in education_text for term in degree_terms):
            education_score = 4
        if any(term in education_text for term in {"accounting", "finance", "engineering", "computer science", "business", "data", "information systems"}):
            education_score += 2
    if job_certs and education_text:
        education_score = min(6, education_score)
    education_detail = {
        "candidate_education": candidate_struct["education"] or None,
        "education_signal": bool(education_score > 0),
    }

    keyword_score = min(4, len(job_terms & candidate_terms) * 0.25)
    summary_bonus = 4 if candidate_struct["summary"] else 0
    resume_bonus = 4 if candidate_struct["resume_text"] else 0
    transferable_bonus = min(6, len(set(job_terms) & set(candidate_struct["skill_phrases"])) * 0.5)

    score = skill_score + title_score + relevance_score + years_score + location_score + certification_score + industry_score + education_score + keyword_score + summary_bonus + resume_bonus + transferable_bonus
    if required_skills and not matched_skills:
        score *= 0.8
    if preferred_skills and preferred_matches:
        score += min(4, len(preferred_matches) * 1.5)
    if not title_close_match and not matched_skills:
        score *= 0.88
    if years_required > 0 and years_experience >= years_required:
        score += 2

    match_score = max(0, min(100, int(round(score))))
    matcher_logger.debug(
        "Python score components | job_title=%s | candidate_id=%s | title_score=%.2f | required_skills_score=%.2f | preferred_skills_score=%.2f | experience_score=%.2f | industry_score=%.2f | education_score=%.2f | certification_score=%.2f | location_score=%.2f | python_total_score=%s",
        job_struct["title"],
        candidate.get("id"),
        title_score,
        skill_score,
        (len(preferred_matches) / max(1, len(preferred_skills))) * 12 if preferred_skills else 0.0,
        years_score,
        industry_score,
        education_score,
        certification_score,
        location_score,
        match_score,
    )
    return {
        "match_source": "Python Match",
        "match_score": match_score,
        "match_percentage": match_score,
        "match_level": _normalize_match_level(match_score),
        "matched_skills": matched_skills[:12],
        "missing_skills": missing_skills[:12],
        "required_skills": required_skills,
        "preferred_skills": preferred_skills,
        "matched_preferred_skills": preferred_matches[:12],
        "missing_preferred_skills": missing_preferred[:12],
        "strengths": matched_skills[:5],
        "gaps": missing_skills[:5],
        "job_title_fit": {
            "job_title": job_struct["title"] or None,
            "candidate_title": candidate_struct["current_position"] or None,
            "exact_match": title_exact,
            "close_match": title_close_match,
        },
        "years_of_experience": years_detail,
        "industry_match": industry_detail,
        "certification_match": certification_detail,
        "education_match": education_detail,
        "location_match": location_detail,
        "extraction_status": {
            "job": job_struct["extract_status"],
            "candidate": candidate_struct["extract_status"],
        },
        "recruiter_summary": (
            "Strong Python match: core skills and title fit are aligned, with supporting experience."
            if match_score >= 70
            else "Possible Python match: some core fit exists, but the candidate is missing part of the required profile."
            if match_score >= 45
            else "Weak Python match: limited overlap with required skills, role fit, or experience."
        ),
        "recommended_action": "Interview" if match_score >= 70 else "Review" if match_score >= 45 else "Reject",
    }


def _store_cached_match(job_id: int, candidate_id: int, job_fingerprint: str, candidate_fingerprint: str, match: dict) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO match_cache (job_id, candidate_id, job_fingerprint, candidate_fingerprint, is_stale, match_json, updated_at)
        VALUES (?, ?, ?, ?, 0, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(job_id, candidate_id) DO UPDATE SET
            job_fingerprint = excluded.job_fingerprint,
            candidate_fingerprint = excluded.candidate_fingerprint,
            is_stale = 0,
            match_json = excluded.match_json,
            updated_at = CURRENT_TIMESTAMP
    """, (job_id, candidate_id, job_fingerprint, candidate_fingerprint, json.dumps(match, separators=(",", ":"))))
    conn.commit()
    cursor.execute("""
        SELECT job_id, candidate_id, job_fingerprint, candidate_fingerprint, is_stale, match_json
        FROM match_cache
        WHERE job_id = ? AND candidate_id = ?
    """, (job_id, candidate_id))
    saved_row = cursor.fetchone()
    conn.close()
    if saved_row:
        saved_match = {}
        try:
            saved_match = json.loads(saved_row[5] or "{}")
        except json.JSONDecodeError:
            saved_match = {}
        matcher_logger.info(
            "Match saved | job_id=%s | candidate_id=%s | stale=%s | saved_match_score=%s | saved_match_percentage=%s",
            saved_row[0],
            saved_row[1],
            bool(saved_row[4]),
            saved_match.get("match_score"),
            saved_match.get("match_percentage"),
        )
    else:
        matcher_logger.warning("Match save verification failed | job_id=%s | candidate_id=%s", job_id, candidate_id)


def _mark_match_stale(job_id: int, candidate_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE match_cache
        SET is_stale = 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE job_id = ? AND candidate_id = ?
    """, (job_id, candidate_id))
    conn.commit()
    conn.close()


def touch_job_match_staleness(job_id: int) -> None:
    """Mark any cached match rows for a job as stale after the source job changes."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE match_cache
        SET is_stale = 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE job_id = ?
    """, (job_id,))
    conn.commit()
    conn.close()


def touch_candidate_match_staleness(candidate_id: int) -> None:
    """Mark any cached match rows for a candidate as stale after a new upload."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE match_cache
        SET is_stale = 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE candidate_id = ?
    """, (candidate_id,))
    conn.commit()
    conn.close()


def _qwen_final_review(job: dict, candidate: dict, python_match: dict) -> dict:
    client = _create_qwen_client()
    if client is None:
        return {}

    prompt = f"""
You are a recruiter doing a final review after Python pre-ranking.
Return ONLY valid JSON and nothing else.

Schema:
{{
  "final_match_score": 0,
  "explanation": "",
  "strengths": [],
  "gaps": [],
  "recommendation": "weak match"
}}

Rules:
- final_match_score must be 0-100.
- recommendation must be exactly one of: strong match, possible match, weak match.
- Keep the response concise and recruiter-style.
- Base the review on the job, candidate, and Python pre-score.
- Do not invent experience or skills.

Job:
{json.dumps(_job_match_context(job), ensure_ascii=True)}

Candidate:
{json.dumps(_candidate_match_context(candidate), ensure_ascii=True)}

Python pre-score:
{json.dumps(python_match, ensure_ascii=True)}
"""

    try:
        response = client.chat.completions.create(
            model=DEFAULT_QWEN_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        raw_response = response.choices[0].message.content or ""
        parsed = _extract_json_object(raw_response)
    except Exception:
        return {}

    python_score = int(python_match.get("match_score", 0) or 0)
    score_value = parsed.get("final_match_score", parsed.get("score", parsed.get("match_score", parsed.get("match_percentage", None))))
    score, original_score_value = _normalize_qwen_score(score_value, python_score)
    matcher_logger.info(
        "Qwen review parsed | job_id=%s | candidate_id=%s | python_match_score=%s | qwen_raw_response=%s | qwen_parsed_score=%s",
        job.get("id"),
        candidate.get("id"),
        python_score,
        _compact_text(raw_response, 240),
        score,
    )

    return {
        "match_source": "Qwen Final Review",
        "match_score": score,
        "match_percentage": score,
        "match_level": _normalize_match_level(score),
        "matched_skills": _safe_string_list(parsed.get("strengths")) or python_match.get("matched_skills", []),
        "missing_skills": _safe_string_list(parsed.get("gaps")) or python_match.get("missing_skills", []),
        "strengths": _safe_string_list(parsed.get("strengths")) or python_match.get("strengths", []),
        "gaps": _safe_string_list(parsed.get("gaps")) or python_match.get("gaps", []),
        "qwen_final_review": {
            "explanation": _compact_text(parsed.get("explanation"), 1200),
            "strengths": _safe_string_list(parsed.get("strengths")),
            "gaps": _safe_string_list(parsed.get("gaps")),
            "recommendation": _normalize_recommendation(parsed.get("recommendation"), score),
            "raw_final_match_score": original_score_value,
        },
        "recruiter_summary": _compact_text(parsed.get("explanation"), 1200) or python_match.get("recruiter_summary", ""),
        "recommended_action": _normalize_recommendation(parsed.get("recommendation"), score),
    }


def apply_qwen_final_review(job: dict, candidate: dict, python_match: dict) -> dict:
    """Run the optional Qwen review step for a pre-ranked candidate."""
    review = _qwen_final_review(job, candidate, python_match)
    if not review:
        return python_match

    merged = {**python_match, **review}
    matcher_logger.info(
        "Final score before save | job_id=%s | candidate_id=%s | score=%s | percentage=%s",
        job.get("id"),
        candidate.get("id"),
        merged.get("match_score"),
        merged.get("match_percentage"),
    )
    _store_cached_match(
        job.get("id"),
        candidate.get("id"),
        _fingerprint(_job_match_context(job)),
        _fingerprint(_candidate_match_context(candidate)),
        merged,
    )
    return merged


def get_analytics_summary():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Aggregate dashboard totals in one pass so the summary cards use a consistent snapshot.
    cursor.execute("""
        SELECT
            COUNT(*) AS total_candidates,
            SUM(CASE WHEN DATE(created_at) = DATE('now') THEN 1 ELSE 0 END) AS uploaded_today,
            SUM(
                CASE
                    WHEN LOWER(TRIM(COALESCE(needs_review, ''))) IN ('yes', 'true', '1', 'needs review')
                    THEN 1
                    ELSE 0
                END
            ) AS needs_review,
            AVG(CASE WHEN total_experience_years > 0 THEN total_experience_years END) AS average_experience_years,
            AVG(CASE WHEN career_span_years > 0 THEN career_span_years END) AS average_career_span_years
        FROM candidates
    """)

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else {
        "total_candidates": 0,
        "uploaded_today": 0,
        "needs_review": 0,
        "average_experience_years": None,
        "average_career_span_years": None,
    }


def get_top_normalized_skills(limit: int = 10):
    conn = get_connection()
    cursor = conn.cursor()

    # Normalized skills are stored as a delimited text field, so split in Python and count each skill.
    cursor.execute("""
        SELECT normalized_skills
        FROM candidates
        WHERE normalized_skills IS NOT NULL AND TRIM(normalized_skills) != ''
    """)

    skill_counter = Counter()
    for (skills,) in cursor.fetchall():
        skill_counter.update(split_normalized_skills(skills))

    conn.close()

    return [
        {"skill": skill, "count": count}
        for skill, count in skill_counter.most_common(limit)
    ]


def get_top_current_roles(limit: int = 10):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Group non-empty current positions to surface the most common roles on the dashboard.
    cursor.execute("""
        SELECT current_position AS role, COUNT(*) AS count
        FROM candidates
        WHERE current_position IS NOT NULL
            AND TRIM(current_position) != ''
            AND LOWER(TRIM(current_position)) NOT IN ('none', 'not found', 'no current position')
        GROUP BY current_position
        ORDER BY count DESC, role ASC
        LIMIT ?
    """, (limit,))

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return rows


def get_recent_uploads(limit: int = 5):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Recent uploads are ordered by id first because ids reflect insert order even if timestamps tie.
    cursor.execute("""
        SELECT id, candidate, email, current_position, filename, created_at
        FROM candidates
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return rows


def get_dashboard_analytics():
    summary = get_analytics_summary()
    total_candidates = summary.get("total_candidates") or 0
    uploaded_today = summary.get("uploaded_today") or 0
    needs_review_count = summary.get("needs_review") or 0
    average_experience_years = summary.get("average_experience_years")
    average_career_span_years = summary.get("average_career_span_years")
    top_skills = get_top_normalized_skills(10)
    top_current_roles = get_top_current_roles(10)
    recent_uploads = get_recent_uploads(5)

    return {
        "total_candidates": total_candidates,
        "uploaded_today": uploaded_today,
        "needs_review_count": needs_review_count,
        "average_experience_years": average_experience_years,
        "average_career_span_years": average_career_span_years,
        "top_skills": top_skills,
        "top_current_roles": top_current_roles,
        "recent_uploads": recent_uploads,
        "summary": {
            "total_candidates": total_candidates,
            "uploaded_today": uploaded_today,
            "needs_review": needs_review_count,
            "average_experience_years": average_experience_years,
            "average_career_span_years": average_career_span_years,
        },
        "top_roles": top_current_roles,
    }


def get_candidate_by_id(candidate_id: int):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            filename,
            candidate,
            email,
            phone,
            employment_status,
            graduation_year,
            total_experience_years,
            career_span_years,
            skills,
            normalized_skills,
            current_position,
            current_company,
            resume_summary,
            needs_review,
            file_hash,
            created_at
        FROM candidates
        WHERE id = ?
    """, (candidate_id,))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def _row_to_job(row: sqlite3.Row | dict | None) -> dict | None:
    if not row:
        return None

    job = dict(row)
    required_skills = split_job_skills(job.get("required_skills"))
    job["required_skills_list"] = required_skills
    job["preferred_skills_list"] = split_job_skills(job.get("preferred_skills"))
    job["certifications_list"] = _split_certifications(job.get("certifications"))
    job["keywords_list"] = _simple_keywords(job.get("keywords"), 18)
    return job


def _next_job_public_id(cursor) -> str:
    cursor.execute("SELECT MAX(id) FROM jobs")
    next_id = int(cursor.fetchone()[0] or 0) + 1
    return f"REQ-{next_id:04d}"


def create_job(job_data: dict, user_email: str | None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    job_id = _next_job_public_id(cursor)

    cursor.execute("""
        INSERT INTO jobs (
            job_id,
            title,
            department,
            location,
            job_type,
            status,
            description,
            required_skills,
            preferred_skills,
            years_required,
            industry,
            certifications,
            keywords,
            salary,
            created_by,
            updated_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        job_data.get("title", "").strip(),
        job_data.get("department", "").strip(),
        job_data.get("location", "").strip(),
        job_data.get("job_type", "").strip(),
        job_data.get("status", "open").strip().lower(),
        job_data.get("description", "").strip(),
        job_data.get("required_skills", "").strip(),
        job_data.get("preferred_skills", "").strip(),
        job_data.get("years_required", "").strip(),
        job_data.get("industry", "").strip(),
        job_data.get("certifications", "").strip(),
        job_data.get("keywords", "").strip(),
        job_data.get("salary", "").strip(),
        user_email,
        user_email,
    ))

    created_id = cursor.lastrowid
    conn.commit()
    conn.close()
    touch_job_match_staleness(created_id)
    db_logger.info("Job inserted (ID=%s)", created_id)

    return created_id


def update_job(job_pk: int, job_data: dict, user_email: str | None) -> bool:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE jobs
        SET title = ?,
            department = ?,
            location = ?,
            job_type = ?,
            status = ?,
            description = ?,
            required_skills = ?,
            preferred_skills = ?,
            years_required = ?,
            industry = ?,
            certifications = ?,
            keywords = ?,
            salary = ?,
            updated_by = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        job_data.get("title", "").strip(),
        job_data.get("department", "").strip(),
        job_data.get("location", "").strip(),
        job_data.get("job_type", "").strip(),
        job_data.get("status", "open").strip().lower(),
        job_data.get("description", "").strip(),
        job_data.get("required_skills", "").strip(),
        job_data.get("preferred_skills", "").strip(),
        job_data.get("years_required", "").strip(),
        job_data.get("industry", "").strip(),
        job_data.get("certifications", "").strip(),
        job_data.get("keywords", "").strip(),
        job_data.get("salary", "").strip(),
        user_email,
        job_pk,
    ))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if updated:
        touch_job_match_staleness(job_pk)
        db_logger.info("Job updated (ID=%s)", job_pk)

    return updated


def delete_job(job_pk: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM jobs WHERE id = ?", (job_pk,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if deleted:
        db_logger.info("Job deleted (ID=%s)", job_pk)

    return deleted


def get_job_by_id(job_identifier: int | str):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, job_id, title, department, location, job_type, status, description,
            required_skills, preferred_skills, years_required, industry, certifications, keywords,
            salary, created_by, updated_by, created_at, updated_at
        FROM jobs
        WHERE id = ? OR job_id = ?
    """, (job_identifier, str(job_identifier)))

    row = cursor.fetchone()
    conn.close()

    return _row_to_job(row)


def calculate_job_match(job: dict, candidate: dict, *, use_cache: bool = True, force_refresh: bool = False, persist: bool = True) -> dict:
    matcher_logger.debug("Matching score calculated | job_id=%s | candidate_id=%s", job.get("id"), candidate.get("id"))
    python_match = _create_python_match(job, candidate)
    job_fingerprint = _fingerprint(_job_match_context(job))
    candidate_fingerprint = _fingerprint(_candidate_match_context(candidate))

    if use_cache and not force_refresh:
        cached_match = _get_cached_match(job.get("id"), candidate.get("id"), job_fingerprint, candidate_fingerprint)
        if cached_match and not cached_match.get("is_stale"):
            python_match = {**python_match, **cached_match}
            python_match["cached"] = True
            python_match["is_cached"] = True
            return {
                "candidate_id": candidate.get("id"),
                "candidate_name": candidate.get("candidate"),
                "current_position": candidate.get("current_position"),
                "current_company": candidate.get("current_company"),
                "location": candidate.get("location") or "Not found",
                "years_experience": candidate.get("total_experience_years"),
                **python_match,
            }

    if persist:
        _store_cached_match(
            job.get("id"),
            candidate.get("id"),
            job_fingerprint,
            candidate_fingerprint,
            python_match,
        )

    return {
        "candidate_id": candidate.get("id"),
        "candidate_name": candidate.get("candidate"),
        "current_position": candidate.get("current_position"),
        "current_company": candidate.get("current_company"),
        "location": candidate.get("location") or "Not found",
        "years_experience": candidate.get("total_experience_years"),
        **python_match,
    }


def get_scraped_job_matches(job_identifier: int | str, *, refresh: bool = False):
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, department, location, employment_type, job_number, salary, description, url, active, last_scraped, source, company
            FROM scraped_jobs
            WHERE id = ?
        """, (job_identifier,))
        job_row = cursor.fetchone()

    if not job_row:
        return None

    job = dict(job_row)
    candidates = get_candidates()
    if not candidates:
        return {"job": job, "matches": [], "total_resumes": 0, "strong_matches": []}

    synthetic_job = {
        "id": job["id"],
        "title": job["title"],
        "department": job.get("department", ""),
        "location": job.get("location", ""),
        "job_type": job.get("employment_type", ""),
        "status": "open" if job.get("active", 1) else "closed",
        "description": job.get("description", ""),
        "required_skills": job.get("required_skills", "") or job.get("title", ""),
        "preferred_skills": job.get("preferred_skills", ""),
        "years_required": "",
        "industry": job.get("department", "") or job.get("industry", ""),
        "certifications": job.get("certifications", ""),
        "keywords": job.get("keywords", "") or f"{job.get('title', '')} {job.get('department', '')}",
        "salary": job.get("salary", ""),
    }

    matches = []
    job_context = _job_match_context(synthetic_job)
    job_fingerprint = _fingerprint(job_context)

    for candidate in candidates:
        candidate_context = _candidate_match_context(candidate)
        candidate_fingerprint = _fingerprint(candidate_context)
        cached_match = None if refresh else _get_cached_match(synthetic_job.get("id"), candidate.get("id"), job_fingerprint, candidate_fingerprint)
        if cached_match and not cached_match.get("is_stale"):
            matches.append({
                "candidate_id": candidate.get("id"),
                "candidate_name": candidate.get("candidate"),
                "current_position": candidate.get("current_position"),
                "current_company": candidate.get("current_company"),
                "location": candidate.get("location") or "Not found",
                "years_experience": candidate.get("total_experience_years"),
                **cached_match,
                "cached": True,
                "is_cached": True,
            })
            continue

        if cached_match and cached_match.get("is_stale"):
            matcher_logger.info("Stale match | job_id=%s | candidate_id=%s", synthetic_job.get("id"), candidate.get("id"))

        matches.append(
            calculate_job_match(
                synthetic_job,
                candidate,
                use_cache=not refresh,
                force_refresh=refresh,
                persist=True,
            )
        )

    matches.sort(key=lambda match: match["match_score"], reverse=True)

    for match in matches[:TOP_QWEN_MATCHES]:
        candidate = next((item for item in candidates if item.get("id") == match["candidate_id"]), None)
        if not candidate:
            continue
        if refresh or match.get("match_source") != "Qwen Final Review" or not match.get("qwen_final_review"):
            review = apply_qwen_final_review(synthetic_job, candidate, match)
            if review:
                match.update(review)
            else:
                matcher_logger.info("Qwen fallback used | job_id=%s | candidate_id=%s", synthetic_job.get("id"), candidate.get("id"))
    for match in matches[TOP_QWEN_MATCHES:]:
        if match.get("match_source") != "Qwen Final Review":
            match["match_source"] = "Python Match"
            match["recruiter_summary"] = "Not sent to Qwen because initial Python ranking was lower."

    if matches:
        api_match = matches[0]
        matcher_logger.info(
            "API response match | job_id=%s | candidate_id=%s | api_response_match_score=%s | api_response_match_percentage=%s",
            api_match.get("job_id", synthetic_job.get("id")),
            api_match.get("candidate_id"),
            api_match.get("match_score"),
            api_match.get("match_percentage"),
        )

    return {
        "job": job,
        "job_id": job.get("id"),
        "matches": matches,
        "total_resumes": len(candidates),
        "strong_matches": [match for match in matches if match["match_score"] >= 70],
    }


def get_job_matches(job_identifier: int | str, *, refresh: bool = False, force_llm: bool = False):
    job = get_job_by_id(job_identifier)
    if not job:
        return None

    candidates = get_candidates()
    if not candidates:
        return {"job": job, "matches": [], "top_matches": []}

    matches = []
    job_context = _job_match_context(job)
    job_fingerprint = _fingerprint(job_context)

    for candidate in candidates:
        matcher_logger.debug("Current candidate being evaluated | job_id=%s | candidate_id=%s", job.get("id"), candidate.get("id"))
        candidate_context = _candidate_match_context(candidate)
        candidate_fingerprint = _fingerprint(candidate_context)
        cached_match = None if refresh else _get_cached_match(job.get("id"), candidate.get("id"), job_fingerprint, candidate_fingerprint)
        if cached_match and not cached_match.get("is_stale"):
            matches.append({
                "candidate_id": candidate.get("id"),
                "candidate_name": candidate.get("candidate"),
                "current_position": candidate.get("current_position"),
                "current_company": candidate.get("current_company"),
                "location": candidate.get("location") or "Not found",
                "years_experience": candidate.get("total_experience_years"),
                **cached_match,
            })
            continue

        matches.append(calculate_job_match(job, candidate, use_cache=False, force_refresh=True))
    matches.sort(key=lambda match: match["match_score"], reverse=True)

    for match in matches[:TOP_QWEN_MATCHES]:
        candidate = next((item for item in candidates if item.get("id") == match["candidate_id"]), None)
        if not candidate:
            continue
        if refresh or match.get("match_source") != "Qwen Final Review" or not match.get("qwen_final_review"):
            match.update(apply_qwen_final_review(job, candidate, match))
    for match in matches[TOP_QWEN_MATCHES:]:
        if match.get("match_source") != "Qwen Final Review":
            match["match_source"] = "Python Match"
            match["recruiter_summary"] = "Not sent to Qwen because initial Python ranking was lower."

    return {
        "job": job,
        "job_id": job.get("id"),
        "matches": matches,
        "top_matches": [match for match in matches if match["match_score"] >= 70],
    }


def get_scraped_jobs():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            title,
            location,
            department,
            employment_type,
            job_number,
            salary,
            description,
            url,
            active,
            last_scraped,
            source,
            company,
            responsibilities,
            qualifications,
            benefits,
            apply_email_or_link
        FROM scraped_jobs
        WHERE COALESCE(active, 1) = 1
        ORDER BY id DESC
    """)
    jobs = [dict(row) for row in cursor.fetchall()]

    cursor.execute("SELECT COUNT(*) AS total_candidates FROM candidates")
    total_candidates_row = cursor.fetchone()
    total_candidates = total_candidates_row["total_candidates"] if total_candidates_row else 0

    cursor.execute("""
        SELECT COUNT(*) AS strong_matches
        FROM match_cache mc
        JOIN candidates c ON c.id = mc.candidate_id
        JOIN scraped_jobs sj ON sj.id = mc.job_id
        WHERE mc.is_stale = 0
          AND COALESCE(sj.active, 1) = 1
          AND COALESCE(
                CAST(json_extract(mc.match_json, '$.match_score') AS REAL),
                CAST(json_extract(mc.match_json, '$.match_percentage') AS REAL)
          ) >= 75
    """)
    strong_matches_row = cursor.fetchone()
    strong_matches = strong_matches_row["strong_matches"] if strong_matches_row else 0

    cursor.execute("""
        SELECT COUNT(*) AS valid_cache_rows
        FROM match_cache mc
        JOIN candidates c ON c.id = mc.candidate_id
        JOIN scraped_jobs sj ON sj.id = mc.job_id
        WHERE mc.is_stale = 0
          AND COALESCE(sj.active, 1) = 1
    """)
    valid_cache_row = cursor.fetchone()
    valid_cache_rows = valid_cache_row["valid_cache_rows"] if valid_cache_row else 0

    cursor.execute("""
        SELECT COUNT(*) AS open_jobs
        FROM scraped_jobs
        WHERE COALESCE(active, 1) = 1
    """)
    open_jobs_row = cursor.fetchone()
    open_jobs = open_jobs_row["open_jobs"] if open_jobs_row else 0

    conn.close()

    summary = {
        "total_jobs": len(jobs),
        "open_jobs": open_jobs,
        "active_candidates": total_candidates,
        "strong_matches": strong_matches,
        "offers_sent": 0,
        "average_time_to_fill": "N/A",
    }
    matcher_logger.info(
        "Job board summary | db_path=%s | candidate_count=%s | scraped_job_count=%s | active_job_count=%s | valid_cache_row_count=%s | strong_match_count=%s | score_column=%s | strong_match_threshold=%s",
        DB_PATH,
        total_candidates,
        len(jobs),
        open_jobs,
        valid_cache_rows,
        strong_matches,
        "match_score/match_percentage",
        75,
    )
    return {"jobs": jobs, "summary": summary}


def get_scraped_job_by_id(job_identifier: int | str):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    ensure_scraped_job_edit_columns(conn)
    cursor.execute(
        """
        SELECT *
        FROM scraped_jobs
        WHERE id = ? OR CAST(job_number AS TEXT) = ? OR title = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (job_identifier, str(job_identifier), str(job_identifier)),
    )
    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def update_scraped_job(job_id: int, updates: dict, user_email: str | None) -> dict | None:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    ensure_scraped_job_edit_columns(conn)
    cursor.execute("SELECT * FROM scraped_jobs WHERE id = ?", (job_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        return None

    editable_values = {column: updates.get(column, existing[column]) for column in SCRAPED_JOB_EDITABLE_COLUMNS}
    editable_values["manual_edited"] = 1
    editable_values["manual_edited_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    editable_values["manual_edited_by"] = user_email

    cursor.execute(
        """
        UPDATE scraped_jobs
        SET title = ?,
            location = ?,
            department = ?,
            employment_type = ?,
            job_number = ?,
            salary = ?,
            description = ?,
            responsibilities = ?,
            qualifications = ?,
            benefits = ?,
            additional_notes = ?,
            active = ?,
            manual_edited = ?,
            manual_edited_at = ?,
            manual_edited_by = ?,
            last_scraped = last_scraped
        WHERE id = ?
        """,
        (
            editable_values["title"],
            editable_values["location"],
            editable_values["department"],
            editable_values["employment_type"],
            editable_values["job_number"],
            editable_values["salary"],
            editable_values["description"],
            editable_values["responsibilities"],
            editable_values["qualifications"],
            editable_values["benefits"],
            editable_values["additional_notes"],
            int(bool(editable_values["active"])),
            editable_values["manual_edited"],
            editable_values["manual_edited_at"],
            editable_values["manual_edited_by"],
            job_id,
        ),
    )
    conn.commit()
    cursor.execute("SELECT * FROM scraped_jobs WHERE id = ?", (job_id,))
    updated = cursor.fetchone()
    conn.close()
    return dict(updated) if updated else None


def get_jobs():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, job_id, title, department, location, job_type, status, description,
            required_skills, salary, created_by, updated_by, created_at, updated_at
        FROM jobs
        ORDER BY id DESC
    """)
    jobs = [_row_to_job(row) for row in cursor.fetchall()]
    conn.close()

    candidate_count = len(get_candidates())
    enriched_jobs = []
    all_top_match_counts = 0
    open_jobs = 0
    offers_sent = 0

    for job in jobs:
        normalized_status = str(job.get("status") or "").lower()
        if normalized_status == "open":
            open_jobs += 1
        if normalized_status in {"offer", "offer sent", "offers sent"}:
            offers_sent += 1

        enriched_jobs.append({
            **job,
            "applicants": candidate_count,
            "top_match_percentage": 0,
            "strong_match_count": 0,
        })

    return {
        "jobs": enriched_jobs,
        "summary": {
            "total_jobs": len(enriched_jobs),
            "open_jobs": open_jobs,
            "active_candidates": candidate_count,
            "strong_matches": all_top_match_counts,
            "offers_sent": offers_sent,
            "average_time_to_fill": "N/A",
        },
    }


def get_users():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, username, email, role, is_locked, last_login, created_at
        FROM users
        ORDER BY
            CASE role WHEN 'admin' THEN 0 ELSE 1 END,
            id ASC
    """)

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return rows


def update_user(user_id: int, name: str, username: str, email: str, role: str) -> None:
    normalized_role = validate_role(role)
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET name = ?, username = ?, email = ?, role = ?
        WHERE id = ?
    """, (name.strip(), username.strip().lower(), email.strip().lower(), normalized_role, user_id))

    conn.commit()
    conn.close()
    db_logger.info("User updated | user_id=%s | email=%s | role=%s", user_id, email, normalized_role)


def reset_user_password(user_id: int, password_hash: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET password_hash = ?
        WHERE id = ?
    """, (password_hash, user_id))

    conn.commit()
    conn.close()
    db_logger.info("Password reset | user_id=%s", user_id)


def set_user_lock_status(user_id: int, is_locked: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET is_locked = ?
        WHERE id = ?
    """, (1 if is_locked else 0, user_id))

    conn.commit()
    conn.close()
    db_logger.info("Account %s | user_id=%s", "locked" if is_locked else "unlocked", user_id)


def mark_user_login(user_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET last_login = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (user_id,))

    conn.commit()
    conn.close()
    db_logger.info("User login marked | user_id=%s", user_id)


def delete_user_record(user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM users
        WHERE id = ?
    """, (user_id,))

    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if deleted:
        db_logger.info("User deleted | user_id=%s", user_id)

    return deleted


def count_users_by_role(role: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE role = ?
    """, (validate_role(role),))

    row = cursor.fetchone()
    conn.close()

    return int(row[0] if row else 0)


def count_locked_users() -> int:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE COALESCE(is_locked, 0) = 1
    """)

    row = cursor.fetchone()
    conn.close()

    return int(row[0] if row else 0)


def count_total_users() -> int:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users")
    row = cursor.fetchone()
    conn.close()

    return int(row[0] if row else 0)


def count_audit_events_today() -> int:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM audit_logs
        WHERE DATE(timestamp) = DATE('now')
    """)

    row = cursor.fetchone()
    conn.close()

    return int(row[0] if row else 0)


def count_failed_login_attempts() -> int:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM audit_logs
        WHERE action = 'Login' AND LOWER(status) = 'failed'
    """)

    row = cursor.fetchone()
    conn.close()

    return int(row[0] if row else 0)


def get_security_dashboard_data(recent_limit: int = 20):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS total_users FROM users")
    total_users = cursor.fetchone()["total_users"]

    cursor.execute("SELECT COUNT(*) AS admin_users FROM users WHERE role = 'admin'")
    admin_users = cursor.fetchone()["admin_users"]

    cursor.execute("SELECT COUNT(*) AS recruiter_users FROM users WHERE role = 'recruiter'")
    recruiter_users = cursor.fetchone()["recruiter_users"]

    cursor.execute("SELECT COUNT(*) AS locked_accounts FROM users WHERE COALESCE(is_locked, 0) = 1")
    locked_accounts = cursor.fetchone()["locked_accounts"]

    cursor.execute("SELECT COUNT(*) AS total_candidates FROM candidates")
    total_candidates = cursor.fetchone()["total_candidates"]

    cursor.execute("""
        SELECT COUNT(*) AS total_resume_uploads
        FROM audit_logs
        WHERE action = 'Resume Upload' AND LOWER(status) = 'success'
    """)
    total_resume_uploads = cursor.fetchone()["total_resume_uploads"]

    cursor.execute("""
        SELECT COUNT(*) AS audit_events_today
        FROM audit_logs
        WHERE DATE(timestamp) = DATE('now')
    """)
    audit_events_today = cursor.fetchone()["audit_events_today"]

    cursor.execute("""
        SELECT COUNT(*) AS failed_login_attempts
        FROM audit_logs
        WHERE action = 'Login' AND LOWER(status) = 'failed'
    """)
    failed_login_attempts = cursor.fetchone()["failed_login_attempts"]

    cursor.execute("""
        SELECT id, timestamp, user_email, action, details, status
        FROM audit_logs
        ORDER BY id DESC
        LIMIT ?
    """, (recent_limit,))
    recent_activity = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {
        "summary": {
            "total_users": int(total_users or 0),
            "admin_users": int(admin_users or 0),
            "recruiter_users": int(recruiter_users or 0),
            "locked_accounts": int(locked_accounts or 0),
            "total_candidates": int(total_candidates or 0),
            "total_resume_uploads": int(total_resume_uploads or 0),
            "audit_events_today": int(audit_events_today or 0),
            "failed_login_attempts": int(failed_login_attempts or 0),
        },
        "recent_activity": recent_activity,
    }


def delete_candidate(candidate_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM candidates
        WHERE id = ?
    """, (candidate_id,))

    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if deleted:
        db_logger.info("Candidate deleted (ID=%s)", candidate_id)

    return deleted


def create_audit_log(user_email: str | None, action: str, details=None, status: str = "success") -> int:
    conn = get_connection()
    cursor = conn.cursor()

    if details is None:
        details_text = ""
    elif isinstance(details, (dict, list)):
        details_text = json.dumps(details)
    else:
        details_text = str(details)

    cursor.execute("""
        INSERT INTO audit_logs (timestamp, user_email, action, details, status)
        VALUES (CURRENT_TIMESTAMP, ?, ?, ?, ?)
    """, (user_email, action, details_text, status))

    audit_log_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return audit_log_id


def get_audit_logs(limit: int = 250):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, timestamp, user_email, action, details, status
        FROM audit_logs
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return rows
