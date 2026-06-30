from fastapi import Cookie, Depends, FastAPI, Response, UploadFile, File, HTTPException
from pathlib import Path
import base64
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import time

from auth_utils import hash_password, verify_password
from resume_parser import parse_resume_file
from database import (
    create_audit_log,
    create_user,
    delete_candidate,
    find_duplicate_candidate,
    get_audit_logs,
    get_candidate_by_id,
    get_dashboard_analytics,
    get_user_by_email,
    get_user_by_id,
    init_db,
    save_candidate,
    get_candidates,
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


def normalize_email(email: str) -> str:
    normalized_email = email.lower().strip()
    if "@" not in normalized_email or "." not in normalized_email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")

    return normalized_email


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user.get("role", "recruiter"),
        "created_at": user.get("created_at"),
    }


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

    if not user or not verify_password(credentials.password, user["password_hash"]):
        create_audit_log(email, "Login", "Invalid email or password.", "failed")
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    set_session_cookie(response, user["id"])
    create_audit_log(user["email"], "Login", "User signed in.", "success")

    return {"user": public_user(user)}


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
