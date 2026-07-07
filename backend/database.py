import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "resumeai.db"
VALID_ROLES = {"admin", "recruiter"}


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
            salary TEXT,
            created_by TEXT,
            updated_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("PRAGMA table_info(jobs)")
    job_columns = {row[1] for row in cursor.fetchall()}
    for column_name, column_definition in {
        "job_id": "TEXT UNIQUE",
        "department": "TEXT",
        "location": "TEXT",
        "job_type": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'open'",
        "description": "TEXT",
        "required_skills": "TEXT",
        "salary": "TEXT",
        "created_by": "TEXT",
        "updated_by": "TEXT",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    }.items():
        if column_name not in job_columns:
            cursor.execute(f"ALTER TABLE jobs ADD COLUMN {column_name} {column_definition}")

    conn.commit()
    conn.close()


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
            salary,
            created_by,
            updated_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        job_data.get("title", "").strip(),
        job_data.get("department", "").strip(),
        job_data.get("location", "").strip(),
        job_data.get("job_type", "").strip(),
        job_data.get("status", "open").strip().lower(),
        job_data.get("description", "").strip(),
        job_data.get("required_skills", "").strip(),
        job_data.get("salary", "").strip(),
        user_email,
        user_email,
    ))

    created_id = cursor.lastrowid
    conn.commit()
    conn.close()

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
        job_data.get("salary", "").strip(),
        user_email,
        job_pk,
    ))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


def delete_job(job_pk: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM jobs WHERE id = ?", (job_pk,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return deleted


def get_job_by_id(job_identifier: int | str):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, job_id, title, department, location, job_type, status, description,
            required_skills, salary, created_by, updated_by, created_at, updated_at
        FROM jobs
        WHERE id = ? OR job_id = ?
    """, (job_identifier, str(job_identifier)))

    row = cursor.fetchone()
    conn.close()

    return _row_to_job(row)


def calculate_job_match(job: dict, candidate: dict) -> dict:
    required_skills = split_job_skills(job.get("required_skills"))
    candidate_skills = split_normalized_skills(candidate.get("normalized_skills")) or split_normalized_skills(candidate.get("skills"))
    candidate_skill_keys = {normalize_skill_key(skill) for skill in candidate_skills}

    matched_skills = []
    missing_skills = []
    for skill in required_skills:
        skill_key = normalize_skill_key(skill)
        if skill_key and skill_key in candidate_skill_keys:
            matched_skills.append(skill)
        else:
            missing_skills.append(skill)

    if required_skills:
        match_percentage = round((len(matched_skills) / len(required_skills)) * 100)
    else:
        title_text = normalize_skill_key(job.get("title"))
        role_text = normalize_skill_key(candidate.get("current_position"))
        match_percentage = 50 if title_text and title_text in role_text else 0

    return {
        "candidate_id": candidate.get("id"),
        "candidate_name": candidate.get("candidate"),
        "current_position": candidate.get("current_position"),
        "current_company": candidate.get("current_company"),
        "location": candidate.get("location") or "Not found",
        "years_experience": candidate.get("total_experience_years"),
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "match_percentage": int(match_percentage),
    }


def get_job_matches(job_identifier: int | str):
    job = get_job_by_id(job_identifier)
    if not job:
        return None

    matches = [
        calculate_job_match(job, candidate)
        for candidate in get_candidates()
    ]
    matches.sort(key=lambda match: match["match_percentage"], reverse=True)

    return {
        "job": job,
        "matches": matches,
        "top_matches": [match for match in matches if match["match_percentage"] >= 70],
    }


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
        match_data = get_job_matches(job["id"]) or {"matches": []}
        matches = match_data["matches"]
        top_match_percentage = matches[0]["match_percentage"] if matches else 0
        strong_match_count = len([match for match in matches if match["match_percentage"] >= 70])
        normalized_status = str(job.get("status") or "").lower()
        if normalized_status == "open":
            open_jobs += 1
        if normalized_status in {"offer", "offer sent", "offers sent"}:
            offers_sent += 1
        all_top_match_counts += strong_match_count

        enriched_jobs.append({
            **job,
            "applicants": candidate_count,
            "top_match_percentage": top_match_percentage,
            "strong_match_count": strong_match_count,
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
