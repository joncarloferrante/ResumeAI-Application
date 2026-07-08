from fastapi import Cookie, Depends, FastAPI, Response, UploadFile, File, HTTPException
from pathlib import Path
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import time
from dotenv import load_dotenv
load_dotenv()

from auth_utils import hash_password, verify_password
from resume_parser import parse_resume_file
from database import (
    create_audit_log,
    create_user,
    calculate_job_match,
    delete_candidate,
    delete_user_record,
    get_security_dashboard_data,
    get_users,
    find_duplicate_candidate,
    get_audit_logs,
    get_candidate_by_id,
    get_dashboard_analytics,
    get_connection,
    get_user_by_email,
    get_user_by_id,
    mark_user_login,
    init_db,
    reset_user_password,
    save_candidate,
    set_user_lock_status,
    update_user,
    get_candidates,
    count_users_by_role,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = PROJECT_ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
SESSION_COOKIE_NAME = "resumeai_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 8
SESSION_SECRET = os.environ.get("RESUMEAI_SESSION_SECRET", "dev-only-change-me")
ENABLE_REGISTRATION = os.environ.get("ENABLE_REGISTRATION", "").lower() == "true"
DUPLICATE_RESUME_MESSAGE = "This resume was already uploaded."


class AuthCredentials(BaseModel):
    email: str
    password: str


class UserCreatePayload(BaseModel):
    name: str
    username: str
    email: str
    password: str
    role: str = "recruiter"


class UserUpdatePayload(BaseModel):
    name: str
    username: str
    email: str
    role: str


class PasswordResetPayload(BaseModel):
    password: str


def normalize_email(email: str) -> str:
    normalized_email = email.lower().strip()
    if "@" not in normalized_email or "." not in normalized_email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")

    return normalized_email


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "name": user.get("name"),
        "username": user.get("username"),
        "email": user["email"],
        "role": user.get("role", "recruiter"),
        "is_locked": bool(user.get("is_locked", 0)),
        "last_login": user.get("last_login"),
        "created_at": user.get("created_at"),
    }


def normalize_username(username: str) -> str:
    normalized_username = username.lower().strip()
    if not normalized_username:
        raise HTTPException(status_code=400, detail="Enter a valid username.")

    if " " in normalized_username:
        raise HTTPException(status_code=400, detail="Username cannot contain spaces.")

    return normalized_username


def sign_payload(payload: dict) -> str:
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("utf-8")
    signature = hmac.new(
        SESSION_SECRET.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return f"{encoded_payload}.{signature}"


def read_signed_payload(session: str | None) -> dict | None:
    if not session:
        return None

    try:
        encoded_payload, signature = session.rsplit(".", 1)
    except ValueError:
        return None

    expected_signature = hmac.new(
        SESSION_SECRET.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload.encode("utf-8")))
    except (ValueError, json.JSONDecodeError):
        return None

    if payload.get("expires_at", 0) < int(time.time()):
        return None

    return payload


def set_session_cookie(response: Response, user_id: int) -> None:
    session = sign_payload({
        "user_id": user_id,
        "expires_at": int(time.time()) + SESSION_MAX_AGE_SECONDS,
    })

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session,
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        secure=False,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=False,
        samesite="lax",
    )


def calculate_file_hash(path: Path) -> str:
    file_hash = hashlib.sha256()
    with path.open("rb") as resume_file:
        for chunk in iter(lambda: resume_file.read(1024 * 1024), b""):
            file_hash.update(chunk)

    return file_hash.hexdigest()


def delete_uploaded_resume_file(filename: str | None) -> bool:
    """Delete only files that resolve inside the configured uploads directory."""
    if not filename:
        return False

    upload_root = UPLOAD_DIR.resolve()
    candidate_path = (UPLOAD_DIR / filename).resolve()

    try:
        candidate_path.relative_to(upload_root)
    except ValueError:
        return False

    if candidate_path.is_file():
        candidate_path.unlink()
        return True

    return False


def log_parsed_resume_debug(filename: str, parsed_data: dict) -> None:
    """Temporary upload pipeline logging for inspecting parser output."""
    debug_fields = {
        "filename": filename,
        "candidate": parsed_data.get("Candidate"),
        "email": parsed_data.get("Email"),
        "phone": parsed_data.get("Phone Number"),
        "current_position": parsed_data.get("Current Position"),
        "current_company": parsed_data.get("Current Company"),
        "total_experience_years": parsed_data.get("Total Experience (Years)"),
        "career_span_years": parsed_data.get("Career Span (Years)"),
        "skills": parsed_data.get("Skills"),
        "normalized_skills": parsed_data.get("Normalized Skills"),
        "resume_summary": parsed_data.get("Resume Summary"),
    }
    print(f"[resume-upload-debug] parsed_data={json.dumps(debug_fields, default=str)}", flush=True)


def get_current_user(resumeai_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)):
    user = get_user_from_session(resumeai_session)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.get("is_locked"):
        raise HTTPException(status_code=401, detail="Account is locked")

    return user


def get_user_from_session(resumeai_session: str | None):
    payload = read_signed_payload(resumeai_session)
    if not payload:
        return None

    user = get_user_by_id(payload["user_id"])

    return user


def require_admin(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    return current_user


@app.get("/")
def home():
    return {"message": "ResumeAI backend is running"}


@app.post("/auth/register")
def register(credentials: AuthCredentials, response: Response):
    if not ENABLE_REGISTRATION:
        raise HTTPException(status_code=403, detail="Registration is disabled.")

    email = normalize_email(credentials.email)

    if len(credentials.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    try:
        user_id = create_user(email, hash_password(credentials.password))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="An account with that email already exists.")

    user = get_user_by_id(user_id)
    set_session_cookie(response, user_id)

    return {"user": public_user(user)}


@app.post("/auth/login")
def login(credentials: AuthCredentials, response: Response):
    email = normalize_email(credentials.email)
    user = get_user_by_email(email)

    if not user:
        create_audit_log(email, "Login", "Invalid email or password.", "failed")
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if user.get("is_locked"):
        create_audit_log(user["email"], "Login", "Account is locked.", "denied")
        raise HTTPException(status_code=403, detail="Account is locked.")

    if not verify_password(credentials.password, user["password_hash"]):
        create_audit_log(email, "Login", "Invalid email or password.", "failed")
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    set_session_cookie(response, user["id"])
    mark_user_login(user["id"])
    create_audit_log(user["email"], "Login", "User signed in.", "success")

    return {"user": public_user(get_user_by_id(user["id"]))}


@app.post("/auth/logout")
def logout(
    response: Response,
    resumeai_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    user = get_user_from_session(resumeai_session)
    if user:
        create_audit_log(user["email"], "Logout", "User signed out.", "success")

    clear_session_cookie(response)
    return {"status": "logged_out"}


@app.get("/auth/me")
def me(current_user: dict = Depends(get_current_user)):
    return {"user": public_user(current_user)}


@app.get("/users")
def list_users(current_user: dict = Depends(require_admin)):
    return [public_user(user) for user in get_users()]


@app.post("/users")
def create_user_account(payload: UserCreatePayload, current_user: dict = Depends(require_admin)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")

    username = normalize_username(payload.username)
    email = normalize_email(payload.email)

    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    try:
        user_id = create_user(
            email,
            hash_password(payload.password),
            role=payload.role,
            name=name,
            username=username,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="That email or username is already in use.")

    created_user = get_user_by_id(user_id)
    create_audit_log(
        current_user["email"],
        "User Created",
        {
            "user_id": user_id,
            "name": created_user.get("name"),
            "username": created_user.get("username"),
            "email": created_user.get("email"),
            "role": created_user.get("role"),
        },
        "success",
    )
    return public_user(created_user)


@app.patch("/users/{user_id}")
def update_user_account(user_id: int, payload: UserUpdatePayload, current_user: dict = Depends(require_admin)):
    existing_user = get_user_by_id(user_id)
    if not existing_user:
        raise HTTPException(status_code=404, detail="User not found")

    if existing_user.get("role") == "admin" and payload.role != "admin" and count_users_by_role("admin") <= 1:
        raise HTTPException(status_code=400, detail="At least one admin must remain active.")

    name = payload.name.strip()
    username = normalize_username(payload.username)
    email = normalize_email(payload.email)

    try:
        update_user(user_id, name, username, email, payload.role)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="That email or username is already in use.")

    updated_user = get_user_by_id(user_id)
    if existing_user.get("role") != updated_user.get("role"):
        create_audit_log(
            current_user["email"],
            "Role Changed",
            {
                "user_id": user_id,
                "name": updated_user.get("name"),
                "username": updated_user.get("username"),
                "email": updated_user.get("email"),
                "role": updated_user.get("role"),
            },
            "success",
        )

    create_audit_log(
        current_user["email"],
        "User Updated",
        {
            "user_id": user_id,
            "name": updated_user.get("name"),
            "username": updated_user.get("username"),
            "email": updated_user.get("email"),
        },
        "success",
    )
    return public_user(updated_user)


@app.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, payload: PasswordResetPayload, current_user: dict = Depends(require_admin)):
    existing_user = get_user_by_id(user_id)
    if not existing_user:
        raise HTTPException(status_code=404, detail="User not found")

    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    reset_user_password(user_id, hash_password(payload.password))
    create_audit_log(
        current_user["email"],
        "Password Reset",
        {
            "user_id": user_id,
            "name": existing_user.get("name"),
            "username": existing_user.get("username"),
            "email": existing_user.get("email"),
        },
        "success",
    )
    return {"status": "password_reset"}


@app.post("/users/{user_id}/lock")
def lock_user(user_id: int, current_user: dict = Depends(require_admin)):
    existing_user = get_user_by_id(user_id)
    if not existing_user:
        raise HTTPException(status_code=404, detail="User not found")

    if existing_user.get("role") == "admin" and count_users_by_role("admin") <= 1:
        raise HTTPException(status_code=400, detail="At least one admin must remain active.")

    set_user_lock_status(user_id, True)
    create_audit_log(
        current_user["email"],
        "Account Locked",
        {
            "user_id": user_id,
            "name": existing_user.get("name"),
            "username": existing_user.get("username"),
            "email": existing_user.get("email"),
        },
        "success",
    )
    return {"status": "locked"}


@app.post("/users/{user_id}/unlock")
def unlock_user(user_id: int, current_user: dict = Depends(require_admin)):
    existing_user = get_user_by_id(user_id)
    if not existing_user:
        raise HTTPException(status_code=404, detail="User not found")

    set_user_lock_status(user_id, False)
    create_audit_log(
        current_user["email"],
        "Account Unlocked",
        {
            "user_id": user_id,
            "name": existing_user.get("name"),
            "username": existing_user.get("username"),
            "email": existing_user.get("email"),
        },
        "success",
    )
    return {"status": "unlocked"}


@app.delete("/users/{user_id}")
def remove_user(user_id: int, current_user: dict = Depends(require_admin)):
    existing_user = get_user_by_id(user_id)
    if not existing_user:
        raise HTTPException(status_code=404, detail="User not found")

    if existing_user.get("role") == "admin" and count_users_by_role("admin") <= 1:
        raise HTTPException(status_code=400, detail="At least one admin must remain active.")

    deleted = delete_user_record(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")

    create_audit_log(
        current_user["email"],
        "User Deleted",
        {
            "user_id": user_id,
            "name": existing_user.get("name"),
            "username": existing_user.get("username"),
            "email": existing_user.get("email"),
            "role": existing_user.get("role"),
        },
        "success",
    )
    return {"status": "deleted"}


@app.get("/security-dashboard")
def security_dashboard(current_user: dict = Depends(require_admin)):
    return get_security_dashboard_data()


@app.post("/upload")
async def upload_resume(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    save_path = UPLOAD_DIR / file.filename

    with save_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_hash = calculate_file_hash(save_path)
    if find_duplicate_candidate(file.filename, "", file_hash):
        create_audit_log(
            current_user["email"],
            "Resume Upload",
            {"filename": file.filename, "reason": DUPLICATE_RESUME_MESSAGE},
            "failed",
        )
        raise HTTPException(status_code=409, detail=DUPLICATE_RESUME_MESSAGE)

    try:
        parsed_data = parse_resume_file(save_path)
    except Exception as exc:
        create_audit_log(
            current_user["email"],
            "Resume Upload",
            {"filename": file.filename, "reason": str(exc)},
            "failed",
        )
        raise HTTPException(status_code=400, detail=str(exc))

    log_parsed_resume_debug(file.filename, parsed_data)

    candidate_id = save_candidate(file.filename, parsed_data, file_hash)
    if candidate_id is None:
        create_audit_log(
            current_user["email"],
            "Resume Upload",
            {"filename": file.filename, "reason": DUPLICATE_RESUME_MESSAGE},
            "failed",
        )
        raise HTTPException(status_code=409, detail=DUPLICATE_RESUME_MESSAGE)

    create_audit_log(
        current_user["email"],
        "Resume Upload",
        {"candidate_id": candidate_id, "filename": file.filename},
        "success",
    )

    return {
        "filename": file.filename,
        "status": "uploaded_parsed_and_saved",
        "candidate_id": candidate_id,
        "saved_to": str(save_path),
        "parsed_data": parsed_data,
    }


@app.get("/candidates")
def list_candidates(current_user: dict = Depends(get_current_user)):
    return get_candidates()


@app.get("/analytics")
def analytics(current_user: dict = Depends(get_current_user)):
    return get_dashboard_analytics()


@app.get("/candidates/{candidate_id}")
def view_candidate(candidate_id: int, current_user: dict = Depends(get_current_user)):
    candidate = get_candidate_by_id(candidate_id)
    if not candidate:
        create_audit_log(
            current_user["email"],
            "Candidate View",
            {"candidate_id": candidate_id, "reason": "Candidate not found"},
            "failed",
        )
        raise HTTPException(status_code=404, detail="Candidate not found")

    create_audit_log(
        current_user["email"],
        "Candidate View",
        {
            "candidate_id": candidate_id,
            "candidate": candidate.get("candidate"),
            "filename": candidate.get("filename"),
        },
        "success",
    )

    return candidate


@app.delete("/candidates/{candidate_id}")
def remove_candidate(candidate_id: int, current_user: dict = Depends(get_current_user)):
    # Role-based deletion is enforced on the server; hiding frontend controls is not enough.
    if current_user.get("role") != "admin":
        create_audit_log(
            current_user["email"],
            "Candidate Delete",
            {"candidate_id": candidate_id, "reason": "Admins only"},
            "failed",
        )
        raise HTTPException(status_code=403, detail="Admins only")

    candidate = get_candidate_by_id(candidate_id)
    if not candidate:
        create_audit_log(
            current_user["email"],
            "Candidate Delete",
            {"candidate_id": candidate_id, "reason": "Candidate not found"},
            "failed",
        )
        raise HTTPException(status_code=404, detail="Candidate not found")

    file_deleted = delete_uploaded_resume_file(candidate.get("filename"))
    delete_candidate(candidate_id)
    create_audit_log(
        current_user["email"],
        "Candidate Delete",
        {
            "candidate_id": candidate_id,
            "filename": candidate.get("filename"),
            "file_deleted": file_deleted,
        },
        "success",
    )

    return {
        "status": "deleted",
        "message": "Candidate deleted successfully.",
        "candidate_id": candidate_id,
        "file_deleted": file_deleted,
    }


@app.get("/audit-logs")
def list_audit_logs(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        create_audit_log(
            current_user["email"],
            "Audit Logs View",
            "Admins only",
            "denied",
        )
        raise HTTPException(status_code=403, detail="Admins only")

    create_audit_log(current_user["email"], "Audit Logs View", "Viewed audit logs.", "success")
    return get_audit_logs()


@app.get("/api/scraped-jobs")
def list_scraped_jobs():
    with get_connection() as conn:
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
                last_scraped
            FROM scraped_jobs
            WHERE COALESCE(active, 1) = 1
            ORDER BY id DESC
        """)
        rows = [dict(row) for row in cursor.fetchall()]

    return {"jobs": rows}


@app.get("/api/scraped-jobs/{job_id}/matches")
def list_scraped_job_matches(job_id: int):
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, title, department, location, employment_type, job_number, salary, description, url, active, last_scraped
            FROM scraped_jobs
            WHERE id = ?
        """, (job_id,))
        job_row = cursor.fetchone()

    if not job_row:
        raise HTTPException(status_code=404, detail="Scraped job not found")

    scraped_job = dict(job_row)
    candidates = get_candidates()
    if not candidates:
        return {
            "job": scraped_job,
            "matches": [],
            "total_resumes": 0,
            "strong_matches": [],
        }

    # Reuse the existing match calculator by turning the scraped job title and department into a keyword list.
    required_skills = ", ".join(
        dict.fromkeys(
            part
            for part in re.split(r"[\s,/|-]+", f"{scraped_job.get('title', '')} {scraped_job.get('department', '')}")
            if part and len(part) > 2
        )
    )
    synthetic_job = {
        "id": scraped_job["id"],
        "title": scraped_job["title"],
        "department": scraped_job.get("department", ""),
        "location": scraped_job.get("location", ""),
        "job_type": scraped_job.get("employment_type", ""),
        "status": "open" if scraped_job.get("active", 1) else "closed",
        "description": scraped_job.get("description", ""),
        "required_skills": required_skills,
        "salary": scraped_job.get("salary", ""),
    }

    matches = [calculate_job_match(synthetic_job, candidate) for candidate in candidates]
    matches.sort(key=lambda match: int(match["match_percentage"] or 0), reverse=True)

    strong_matches = [match for match in matches if int(match["match_percentage"] or 0) >= 75]

    return {
        "job": scraped_job,
        "matches": matches,
        "total_resumes": len(candidates),
        "strong_matches": strong_matches,
    }
