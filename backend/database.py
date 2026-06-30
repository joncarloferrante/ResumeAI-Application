import json
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "resumeai.db"


def get_connection():
    DB_PATH.parent.mkdir(exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("PRAGMA table_info(users)")
    user_columns = {row[1] for row in cursor.fetchall()}
    if "role" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            candidate TEXT,
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
            file_hash TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("PRAGMA table_info(candidates)")
    candidate_columns = {row[1] for row in cursor.fetchall()}
    if "file_hash" not in candidate_columns:
        cursor.execute("ALTER TABLE candidates ADD COLUMN file_hash TEXT")

    conn.commit()
    conn.close()


def create_user(email: str, password_hash: str, role: str = "user") -> int:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO users (email, password_hash, role)
        VALUES (?, ?, ?)
    """, (email, password_hash, role))

    user_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return user_id


def update_user_role(email: str, role: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET role = ?
        WHERE email = ?
    """, (role, email))

    conn.commit()
    conn.close()


def get_user_by_email(email: str):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, email, password_hash, role, created_at
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
        SELECT id, email, role, created_at
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
        return None

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO candidates (
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
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        filename,
        parsed_data.get("Candidate", ""),
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
        file_hash,
        json.dumps(parsed_data)
    ))

    candidate_id = cursor.lastrowid
    conn.commit()
    conn.close()

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
            created_at
        FROM candidates
        ORDER BY id DESC
    """)

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return rows


def get_candidate_by_id(candidate_id: int):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, filename, candidate, email, file_hash
        FROM candidates
        WHERE id = ?
    """, (candidate_id,))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


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

    return deleted


def create_audit_log_if_available(user_id: int, action: str, details: dict) -> None:
    """Write to an existing audit_logs table without requiring one to exist."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = 'audit_logs'
    """)
    if not cursor.fetchone():
        conn.close()
        return

    cursor.execute("PRAGMA table_info(audit_logs)")
    columns = {row[1] for row in cursor.fetchall()}

    if {"user_id", "action", "details"}.issubset(columns):
        cursor.execute("""
            INSERT INTO audit_logs (user_id, action, details)
            VALUES (?, ?, ?)
        """, (user_id, action, json.dumps(details)))
        conn.commit()

    conn.close()
