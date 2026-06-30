import json
import sqlite3
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "resumeai.db"
VALID_ROLES = {"admin", "recruiter"}


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
            role TEXT NOT NULL DEFAULT 'recruiter',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("PRAGMA table_info(users)")
    user_columns = {row[1] for row in cursor.fetchall()}
    if "role" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'recruiter'")

    cursor.execute("""
        UPDATE users
        SET role = 'recruiter'
        WHERE role IS NULL OR role NOT IN ('admin', 'recruiter')
    """)

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

    cursor.execute("PRAGMA table_info(audit_logs)")
    audit_log_columns = {row[1] for row in cursor.fetchall()}
    if "timestamp" not in audit_log_columns:
        cursor.execute("ALTER TABLE audit_logs ADD COLUMN timestamp TEXT")
    if "user_email" not in audit_log_columns:
        cursor.execute("ALTER TABLE audit_logs ADD COLUMN user_email TEXT")
    if "status" not in audit_log_columns:
        cursor.execute("ALTER TABLE audit_logs ADD COLUMN status TEXT NOT NULL DEFAULT 'success'")

    conn.commit()
    conn.close()


def validate_role(role: str) -> str:
    normalized_role = role.lower().strip()
    if normalized_role not in VALID_ROLES:
        raise ValueError("Role must be either admin or recruiter.")

    return normalized_role


def create_user(email: str, password_hash: str, role: str = "recruiter") -> int:
    normalized_role = validate_role(role)
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO users (email, password_hash, role)
        VALUES (?, ?, ?)
    """, (email, password_hash, normalized_role))

    user_id = cursor.lastrowid
    conn.commit()
    conn.close()

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


def split_normalized_skills(skills: str | None) -> list[str]:
    if not skills:
        return []

    normalized_skills = []
    for skill in str(skills).replace(";", ",").split(","):
        clean_skill = skill.strip()
        if clean_skill and clean_skill.lower() not in {"none", "not found", "skills not found"}:
            normalized_skills.append(clean_skill)

    return normalized_skills


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
