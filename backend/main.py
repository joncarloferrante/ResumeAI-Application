from fastapi import Cookie, Depends, FastAPI, Response, UploadFile, File, HTTPException, Query, Request
from pathlib import Path
import base64
import hashlib
import hmac
import json
import os
import logging
import re
import shutil
import sqlite3
import time
import threading
from dotenv import load_dotenv
load_dotenv()

try:
    import psycopg2
except ImportError:
    try:
        import psycopg
    except ImportError:
        psycopg2 = None
        psycopg = None
    else:
        psycopg2 = None
else:
    psycopg = None

if psycopg2 is not None:
    DB_INTEGRITY_ERROR = (sqlite3.IntegrityError, psycopg2.IntegrityError)
elif psycopg is not None:
    DB_INTEGRITY_ERROR = (sqlite3.IntegrityError, psycopg.IntegrityError)
else:
    DB_INTEGRITY_ERROR = (sqlite3.IntegrityError,)

from .auth_utils import hash_password, verify_password
from .resume_parser import parse_resume_file_with_text
from .resume_upload_pipeline import (
    ExtractionResult,
    MIN_TEXT_CHARS,
    MIN_WORD_COUNT,
    extract_text_with_ocr,
    is_meaningful_text,
    validate_resume_content,
)
from .database import (
    create_audit_log,
    apply_qwen_final_review,
    build_safe_resume_filename,
    create_user,
    create_resume_upload_record,
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
    get_scraped_job_by_id,
    get_user_by_email,
    get_user_by_id,
    get_scraped_jobs,
    get_scraped_job_matches,
    get_scraped_job_cached_matches,
    get_resume_uploads_for_user,
    get_resume_upload_by_candidate_id,
    mark_user_login,
    init_db,
    reset_user_password,
    save_candidate,
    set_user_lock_status,
    sanitize_resume_filename,
    update_resume_upload_status,
    ensure_scraped_job_edit_columns,
    update_scraped_job,
    update_user,
    get_candidates,
    count_users_by_role,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .logging_config import configure_logging, get_logger
from .ats.service import register_or_update_job_source, sync_job_source
from .database import get_job_sources, set_job_source_disabled
from .ats.registry import detect_adapter

configure_logging()
logger = get_logger("main")
api_logger = get_logger("api")
auth_logger = get_logger("auth")
upload_logger = get_logger("upload")
parser_logger = get_logger("parser")
file_logger = get_logger("file")
user_logger = get_logger("users")
startup_logger = get_logger("startup")

app = FastAPI()


def _normalize_origin(origin: str) -> str:
    return origin.strip().rstrip("/")


allowed_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://resumeai-frontend-pahy.onrender.com",
]

frontend_origin_env = os.environ.get("FRONTEND_ORIGIN", "")
if frontend_origin_env.strip():
    allowed_origins.append(_normalize_origin(frontend_origin_env))

additional_origins_env = os.environ.get("CORS_ALLOWED_ORIGINS", "")
if additional_origins_env.strip():
    allowed_origins.extend(
        _normalize_origin(origin)
        for origin in additional_origins_env.split(",")
        if origin.strip()
    )

allowed_origins = list(dict.fromkeys(allowed_origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - start) * 1000)
    api_logger.info("%s %s completed in %s ms (%s)", request.method, request.url.path, duration_ms, response.status_code)
    return response

startup_logger.info("Backend starting...")
db_start = time.perf_counter()
init_db()
startup_logger.info("Database initialization complete in %.2f seconds", time.perf_counter() - db_start)
startup_logger.info("API routes loaded")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = PROJECT_ROOT / "uploads"
if not UPLOAD_DIR.exists():
    UPLOAD_DIR.mkdir(exist_ok=True)
    file_logger.info("Upload directory created | path=%s", UPLOAD_DIR)
MAX_RESUME_UPLOAD_MB = max(1, int(os.environ.get("MAX_RESUME_UPLOAD_MB", "20") or 20))
MAX_RESUME_UPLOAD_BYTES = MAX_RESUME_UPLOAD_MB * 1024 * 1024
SESSION_COOKIE_NAME = "resumeai_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 8
SESSION_SECRET = os.environ.get("RESUMEAI_SESSION_SECRET", "dev-only-change-me")
ENABLE_REGISTRATION = os.environ.get("ENABLE_REGISTRATION", "").lower() == "true"
DUPLICATE_RESUME_MESSAGE = "This resume was already uploaded."
SUPPORTED_RESUME_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
SUPPORTED_RESUME_EXTENSIONS = {".pdf", ".docx"}


def _is_production_cookie_mode() -> bool:
    """Use secure cross-site cookies when explicitly enabled in the environment."""
    return os.getenv("COOKIE_SECURE", "false").strip().lower() == "true"


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


class ScrapedJobUpdatePayload(BaseModel):
    title: str | None = None
    location: str | None = None
    department: str | None = None
    employment_type: str | None = None
    job_number: str | None = None
    salary: str | None = None
    description: str | None = None
    responsibilities: str | None = None
    qualifications: str | None = None
    benefits: str | None = None
    additional_notes: str | None = None
    active: bool | None = None


class AtsImportPayload(BaseModel):
    careers_url: str


class JobSourceActionPayload(BaseModel):
    source_id: int


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

    secure_cookie = _is_production_cookie_mode()

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session,
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        secure=secure_cookie,
        samesite="none" if secure_cookie else "lax",
    )


def clear_session_cookie(response: Response) -> None:
    secure_cookie = _is_production_cookie_mode()

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=secure_cookie,
        samesite="none" if secure_cookie else "lax",
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
        file_logger.info("Resume deleted | filename=%s", filename)
        return True

    file_logger.warning("File not found | filename=%s", filename)
    return False


def _get_resume_file_extension(filename: str | None) -> str:
    return Path(str(filename or "")).suffix.lower().strip()


def _detect_resume_signature(path: Path) -> tuple[str, str | None]:
    try:
        with path.open("rb") as handle:
            header = handle.read(8)
    except OSError as exc:
        return "invalid", str(exc)

    if header.startswith(b"%PDF-"):
        return "pdf", None

    if header.startswith(b"PK"):
        try:
            import zipfile

            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" in names and any(name.startswith("word/") for name in names):
                    return "docx", None
        except zipfile.BadZipFile:
            return "invalid", "The uploaded file is not a valid DOCX archive."
        except RuntimeError as exc:
            return "invalid", str(exc)

    return "invalid", "Unsupported file signature."


def _is_password_protected_pdf(path: Path) -> bool:
    try:
        from pypdf import PdfReader
    except ImportError:
        return False

    try:
        reader = PdfReader(str(path))
        return bool(getattr(reader, "is_encrypted", False))
    except Exception:
        return False


def _is_password_protected_docx(path: Path) -> bool:
    try:
        import zipfile

        return zipfile.is_zipfile(path) and any(part.startswith("EncryptedPackage") for part in zipfile.ZipFile(path).namelist())
    except Exception:
        return False


def _validate_upload_metadata(file: UploadFile, save_path: Path, file_size: int) -> tuple[str, str, str | None]:
    extension = _get_resume_file_extension(file.filename)
    if extension not in SUPPORTED_RESUME_EXTENSIONS:
        return "rejected", "Unsupported file type. Please upload a PDF or DOCX resume.", "unsupported_extension"

    if file_size <= 0:
        return "rejected", "The uploaded file is empty.", "empty_file"

    if file_size > MAX_RESUME_UPLOAD_BYTES:
        return "rejected", f"File is too large. Maximum size is {MAX_RESUME_UPLOAD_MB} MB.", "file_too_large"

    detected_type, signature_error = _detect_resume_signature(save_path)
    if detected_type == "invalid":
        return "rejected", signature_error or "The file could not be validated.", "invalid_signature"

    if detected_type == "pdf" and _is_password_protected_pdf(save_path):
        return "rejected", "Password-protected PDFs cannot be processed.", "password_protected"

    if detected_type == "docx" and _is_password_protected_docx(save_path):
        return "rejected", "Password-protected DOCX files cannot be processed.", "password_protected"

    supplied_type = str(file.content_type or "").lower()
    if supplied_type and supplied_type not in SUPPORTED_RESUME_MIME_TYPES:
        return "rejected", "The file type does not match a supported resume format.", "mime_mismatch"

    return "ready", "", detected_type


def _record_upload_stage(upload_id: int, *, stage: str, status: str, reason: str | None = None, ocr_required: bool | None = None) -> None:
    update_resume_upload_status(
        upload_id,
        processing_stage=stage,
        status=status,
        failure_reason=reason,
        ocr_required=ocr_required,
    )


def _mark_upload_failed(upload_id: int, reason: str, status: str = "failed") -> None:
    update_resume_upload_status(
        upload_id,
        status=status,
        failure_reason=reason,
        processing_stage="failed",
    )


def log_parsed_resume_debug(filename: str, parsed_data: dict) -> None:
    """Structured summary of parsed output without raw resume text."""
    parser_logger.debug(
        "Candidate extracted | filename=%s | candidate=%s | email=%s | current_position=%s | current_company=%s",
        filename,
        parsed_data.get("Candidate"),
        parsed_data.get("Email"),
        parsed_data.get("Current Position"),
        parsed_data.get("Current Company"),
    )


def get_current_user(resumeai_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)):
    user = get_user_from_session(resumeai_session)
    if not user:
        auth_logger.warning("Unauthorized access attempt")
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.get("is_locked"):
        auth_logger.warning("Locked account access denied | user_id=%s | email=%s", user.get("id"), user.get("email"))
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
        auth_logger.warning("Role-based access denied | user_id=%s | email=%s | role=%s", current_user.get("id"), current_user.get("email"), current_user.get("role"))
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
    auth_logger.info("Login attempt via registration | email=%s", email)

    if len(credentials.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    try:
        user_id = create_user(email, hash_password(credentials.password))
    except DB_INTEGRITY_ERROR:
        raise HTTPException(status_code=400, detail="An account with that email already exists.")

    user = get_user_by_id(user_id)
    set_session_cookie(response, user_id)

    return {"user": public_user(user)}


@app.post("/auth/login")
def login(credentials: AuthCredentials, response: Response):
    email = normalize_email(credentials.email)
    auth_logger.info("Login attempt | email=%s", email)
    user = get_user_by_email(email)

    if not user:
        create_audit_log(email, "Login", "Invalid email or password.", "failed")
        auth_logger.warning("Failed login | email=%s | reason=unknown user", email)
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if user.get("is_locked"):
        create_audit_log(user["email"], "Login", "Account is locked.", "denied")
        auth_logger.warning("Failed login | email=%s | reason=locked", user["email"])
        raise HTTPException(status_code=403, detail="Account is locked.")

    if not verify_password(credentials.password, user["password_hash"]):
        create_audit_log(email, "Login", "Invalid email or password.", "failed")
        auth_logger.warning("Failed login | email=%s | reason=invalid password", email)
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    set_session_cookie(response, user["id"])
    mark_user_login(user["id"])
    create_audit_log(user["email"], "Login", "User signed in.", "success")
    auth_logger.info("Successful login | user_id=%s | email=%s", user["id"], user["email"])

    return {"user": public_user(get_user_by_id(user["id"]))}


@app.post("/auth/logout")
def logout(
    response: Response,
    resumeai_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    user = get_user_from_session(resumeai_session)
    if user:
        create_audit_log(user["email"], "Logout", "User signed out.", "success")
        auth_logger.info("Logout | user_id=%s | email=%s", user.get("id"), user.get("email"))

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
    except DB_INTEGRITY_ERROR:
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
    user_logger.info("User created | user_id=%s | email=%s | role=%s", user_id, created_user.get("email"), created_user.get("role"))
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
    except DB_INTEGRITY_ERROR:
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
    user_logger.info("User updated | user_id=%s | email=%s | role=%s", user_id, updated_user.get("email"), updated_user.get("role"))
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
    user_logger.info("Password reset | user_id=%s | email=%s", user_id, existing_user.get("email"))
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
    user_logger.info("Account locked | user_id=%s | email=%s", user_id, existing_user.get("email"))
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
    user_logger.info("Account unlocked | user_id=%s | email=%s", user_id, existing_user.get("email"))
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
    user_logger.info("User deleted | user_id=%s | email=%s", user_id, existing_user.get("email"))
    return {"status": "deleted"}


@app.get("/security-dashboard")
def security_dashboard(current_user: dict = Depends(require_admin)):
    return get_security_dashboard_data()


@app.post("/upload")
async def upload_resume(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    original_filename = sanitize_resume_filename(file.filename)
    upload_logger.info(
        "Upload started | user_id=%s | filename=%s | content_type=%s",
        current_user.get("id"),
        original_filename,
        file.content_type,
    )
    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)
    upload_logger.info("Uploaded filename=%s | size=%s bytes | type=%s", original_filename, file_size, file.content_type)

    if file_size > MAX_RESUME_UPLOAD_BYTES:
        create_audit_log(
            current_user["email"],
            "Resume Upload",
            {"filename": original_filename, "reason": f"File is too large. Maximum size is {MAX_RESUME_UPLOAD_MB} MB."},
            "failed",
        )
        raise HTTPException(status_code=400, detail=f"File is too large. Maximum size is {MAX_RESUME_UPLOAD_MB} MB.")

    safe_filename = build_safe_resume_filename(original_filename)
    save_path = UPLOAD_DIR / safe_filename
    upload_id = create_resume_upload_record(
        uploader_email=current_user["email"],
        original_filename=original_filename,
        safe_filename=safe_filename,
        file_type=file.content_type,
        file_size_bytes=file_size,
        status="uploaded",
        processing_stage="uploaded",
    )

    _record_upload_stage(upload_id, stage="security_check", status="security_check")

    with save_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_hash = calculate_file_hash(save_path)
    validation_status, validation_message, detected_type = _validate_upload_metadata(file, save_path, file_size)
    if validation_status != "ready":
        _record_upload_stage(
            upload_id,
            stage="rejected",
            status=validation_status,
            reason=validation_message,
        )
        create_audit_log(
            current_user["email"],
            "Resume Upload",
            {"filename": original_filename, "reason": validation_message, "status": validation_status},
            "failed",
        )
        upload_logger.warning("Upload rejected | filename=%s | reason=%s", original_filename, validation_message)
        raise HTTPException(status_code=400, detail=validation_message)

    if find_duplicate_candidate(original_filename, "", file_hash):
        create_audit_log(
            current_user["email"],
            "Resume Upload",
            {"filename": original_filename, "reason": DUPLICATE_RESUME_MESSAGE},
            "failed",
        )
        _mark_upload_failed(upload_id, DUPLICATE_RESUME_MESSAGE, status="rejected")
        upload_logger.warning("Upload failed | filename=%s | reason=duplicate", original_filename)
        raise HTTPException(status_code=409, detail=DUPLICATE_RESUME_MESSAGE)

    try:
        _record_upload_stage(upload_id, stage="extracting_text", status="extracting_text")
        parser_logger.info("Text extraction started | filename=%s", original_filename)
        extraction = extract_text_with_ocr(save_path)
        extracted_text = extraction.text or ""

        if extraction.ocr_attempted and not extraction.ocr_available:
            _record_upload_stage(
                upload_id,
                stage="needs_review",
                status="needs_review",
                reason="OCR is not configured for this environment.",
                ocr_required=True,
            )
            create_audit_log(
                current_user["email"],
                "Resume Upload",
                {
                    "filename": original_filename,
                    "safe_filename": safe_filename,
                    "reason": "OCR is not configured for this environment.",
                    "status": "needs_review",
                },
                "success",
            )
            return {
                "filename": original_filename,
                "safe_filename": safe_filename,
                "status": "needs_review",
                "candidate_id": None,
                "saved_to": str(save_path),
                "reason": "We couldn't confidently read enough information from this resume.",
            }

        if not is_meaningful_text(extracted_text, extraction.page_count):
            if extraction.is_scanned:
                if extraction.ocr_available:
                    _record_upload_stage(
                        upload_id,
                        stage="ocr_processing",
                        status="ocr_processing",
                        reason="OCR is processing a scanned document.",
                        ocr_required=True,
                    )
                    extracted_text = extraction.text or ""
                    if not is_meaningful_text(extracted_text, extraction.page_count):
                        _record_upload_stage(
                            upload_id,
                            stage="unreadable",
                            status="unreadable",
                            reason="OCR could not recover enough readable text.",
                            ocr_required=True,
                        )
                        create_audit_log(
                            current_user["email"],
                            "Resume Upload",
                            {"filename": original_filename, "safe_filename": safe_filename, "reason": "OCR could not recover enough readable text.", "status": "unreadable"},
                            "success",
                        )
                        return {
                            "filename": original_filename,
                            "safe_filename": safe_filename,
                            "status": "unreadable",
                            "candidate_id": None,
                            "saved_to": str(save_path),
                            "reason": "No readable text could be extracted.",
                        }
                else:
                    _record_upload_stage(
                        upload_id,
                        stage="needs_review",
                        status="needs_review",
                        reason="The document appears to be scanned and requires OCR.",
                        ocr_required=True,
                    )
                    create_audit_log(
                        current_user["email"],
                        "Resume Upload",
                        {"filename": original_filename, "safe_filename": safe_filename, "reason": "The document appears to be scanned and requires OCR.", "status": "needs_review"},
                        "success",
                    )
                    return {
                        "filename": original_filename,
                        "safe_filename": safe_filename,
                        "status": "needs_review",
                        "candidate_id": None,
                        "saved_to": str(save_path),
                        "reason": "The document appears to be scanned and requires OCR.",
                    }
            else:
                _record_upload_stage(
                    upload_id,
                    stage="needs_review",
                    status="needs_review",
                    reason="Not enough readable text could be extracted.",
                )
                create_audit_log(
                    current_user["email"],
                    "Resume Upload",
                    {"filename": original_filename, "safe_filename": safe_filename, "reason": "Not enough readable text could be extracted.", "status": "needs_review"},
                    "success",
                )
                return {
                    "filename": original_filename,
                    "safe_filename": safe_filename,
                    "status": "needs_review",
                    "candidate_id": None,
                    "saved_to": str(save_path),
                    "reason": "We couldn't confidently read enough information from this resume.",
                }

        _record_upload_stage(upload_id, stage="validating", status="validating")
        content_validation = validate_resume_content(extracted_text)
        if not content_validation.is_resume:
            rejection_reason = "This document does not appear to be a resume."
            _record_upload_stage(
                upload_id,
                stage="rejected",
                status="rejected",
                reason=rejection_reason,
            )
            create_audit_log(
                current_user["email"],
                "Resume Upload",
                {
                    "filename": original_filename,
                    "safe_filename": safe_filename,
                    "reason": rejection_reason,
                    "status": "rejected",
                    "validation_signals": content_validation.signals,
                },
                "failed",
            )
            return {
                "filename": original_filename,
                "safe_filename": safe_filename,
                "status": "rejected",
                "candidate_id": None,
                "saved_to": str(save_path),
                "reason": rejection_reason,
            }

        _record_upload_stage(upload_id, stage="parsing", status="parsing")
        parser_logger.info("Parsing started | filename=%s", original_filename)
        parsed_data = parse_resume_file_with_text(save_path, extracted_text)
        parser_logger.info("Parsing completed | filename=%s", original_filename)
    except Exception as exc:
        _mark_upload_failed(upload_id, str(exc))
        create_audit_log(
            current_user["email"],
            "Resume Upload",
            {"filename": original_filename, "reason": str(exc)},
            "failed",
        )
        upload_logger.exception("Upload failed | filename=%s", original_filename)
        raise HTTPException(status_code=400, detail=str(exc))

    log_parsed_resume_debug(original_filename, parsed_data)

    candidate_id = save_candidate(original_filename, parsed_data, file_hash)
    if candidate_id is None:
        _mark_upload_failed(upload_id, DUPLICATE_RESUME_MESSAGE, status="rejected")
        create_audit_log(
            current_user["email"],
            "Resume Upload",
            {"filename": original_filename, "reason": DUPLICATE_RESUME_MESSAGE},
            "failed",
        )
        upload_logger.warning("Upload failed | filename=%s | reason=duplicate after parse", original_filename)
        raise HTTPException(status_code=409, detail=DUPLICATE_RESUME_MESSAGE)

    update_resume_upload_status(
        upload_id,
        candidate_id=candidate_id,
        status="ready",
        processing_stage="ready",
        ready=True,
        failure_reason=None,
    )

    create_audit_log(
        current_user["email"],
        "Resume Upload",
        {"candidate_id": candidate_id, "filename": original_filename, "safe_filename": safe_filename},
        "success",
    )
    upload_logger.info("Upload completed | filename=%s | candidate_id=%s", original_filename, candidate_id)

    return {
        "filename": original_filename,
        "safe_filename": safe_filename,
        "status": "ready",
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


@app.get("/resume-uploads")
def resume_uploads(current_user: dict = Depends(get_current_user), limit: int = Query(default=10, ge=1, le=50)):
    uploads = get_resume_uploads_for_user(
        current_user.get("email"),
        limit=limit,
        is_admin=current_user.get("role") == "admin",
    )
    return {
        "uploads": [
            {
                "id": upload.get("id"),
                "candidate_id": upload.get("candidate_id"),
                "original_filename": upload.get("original_filename"),
                "safe_filename": upload.get("safe_filename"),
                "file_type": upload.get("file_type"),
                "file_size_bytes": upload.get("file_size_bytes"),
                "status": upload.get("status"),
                "processing_stage": upload.get("processing_stage"),
                "ocr_required": bool(upload.get("ocr_required")),
                "created_at": upload.get("created_at"),
                "updated_at": upload.get("updated_at"),
                "ready_at": upload.get("ready_at"),
            }
            for upload in uploads
        ]
    }


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

    upload_record = get_resume_upload_by_candidate_id(candidate_id)
    file_deleted = delete_uploaded_resume_file(upload_record.get("safe_filename") if upload_record else candidate.get("filename"))
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
    return get_scraped_jobs()


@app.get("/api/admin/job-sources")
def list_job_sources(current_user: dict = Depends(require_admin)):
    return get_job_sources()


@app.post("/api/admin/ats/import")
def import_ats_jobs(payload: AtsImportPayload, current_user: dict = Depends(require_admin)):
    source = register_or_update_job_source(payload.careers_url)
    result = sync_job_source(int(source["id"]))
    create_audit_log(
        current_user["email"],
        "ATS Import",
        {
            "source_id": source["id"],
            "careers_url": source["careers_url"],
            "source_type": source["source_type"],
            "company_name": source["company_name"],
            "jobs_found": result["jobs_found"],
            "jobs_added": result["jobs_added"],
            "jobs_updated": result["jobs_updated"],
            "jobs_skipped": result["jobs_skipped"],
            "jobs_failed": result["jobs_failed"],
        },
        "success" if result["jobs_failed"] == 0 else "warning",
    )
    return {"source": source, "result": result}


@app.post("/api/admin/job-sources/{source_id}/sync")
def sync_saved_job_source(source_id: int, current_user: dict = Depends(require_admin)):
    result = sync_job_source(source_id)
    create_audit_log(current_user["email"], "ATS Sync", result, "success" if result["jobs_failed"] == 0 else "warning")
    return result


@app.patch("/api/admin/job-sources/{source_id}/disable")
def disable_saved_job_source(source_id: int, current_user: dict = Depends(require_admin)):
    source = set_job_source_disabled(source_id, True)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    create_audit_log(current_user["email"], "ATS Source Disabled", {"source_id": source_id}, "success")
    return source


@app.patch("/api/scraped-jobs/{job_id}")
def update_scraped_job_details(job_id: int, payload: ScrapedJobUpdatePayload, current_user: dict = Depends(get_current_user)):
    allowed_roles = {"admin", "recruiter"}
    if current_user.get("role") not in allowed_roles:
        create_audit_log(
            current_user["email"],
            "Job Updated",
            f"Denied update for job_id={job_id}",
            "denied",
        )
        raise HTTPException(status_code=403, detail="Not authorized to edit jobs.")

    existing_job = get_scraped_job_by_id(job_id)
    if not existing_job:
        create_audit_log(
            current_user["email"],
            "Job Updated",
            f"Job not found for job_id={job_id}",
            "failed",
        )
        raise HTTPException(status_code=404, detail="Scraped job not found")

    update_data = {}
    changed_fields = []
    for field in [
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
    ]:
        value = getattr(payload, field)
        if value is None:
            continue
        update_data[field] = value.strip() if isinstance(value, str) else value
        changed_fields.append(field)

    if "title" in update_data and not str(update_data["title"]).strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty.")
    if "job_number" in update_data and not str(update_data["job_number"]).strip() and existing_job.get("job_number"):
        raise HTTPException(status_code=400, detail="Job number cannot be cleared once it exists.")

    normalized_update_data = {
        **{key: existing_job.get(key) for key in update_data.keys()},
        **update_data,
    }
    updated_job = update_scraped_job(job_id, normalized_update_data, current_user.get("email"))
    if not updated_job:
        create_audit_log(
            current_user["email"],
            "Job Updated",
            f"Update failed for job_id={job_id}",
            "failed",
        )
        raise HTTPException(status_code=404, detail="Scraped job not found")

    create_audit_log(
        current_user["email"],
        "Job Updated",
        f"job_id={job_id}; fields={', '.join(changed_fields) if changed_fields else 'none'}",
        "success",
    )
    return updated_job


@app.get("/api/scraped-jobs/{job_id}/matches")
def list_scraped_job_matches(job_id: int, refresh: bool = Query(default=False)):
    matcher_logger = get_logger("matcher")
    matcher_logger.info("Matching started | job_id=%s | refresh=%s", job_id, refresh)
    with _job_match_locks_guard:
        job_lock = _job_match_locks.setdefault(job_id, threading.Lock())

    if not job_lock.acquire(blocking=False):
        matcher_logger.info("Duplicate processing prevented | job_id=%s", job_id)
        with job_lock:
            pass
        result = get_scraped_job_matches(job_id, refresh=False)
        if not result:
            raise HTTPException(status_code=404, detail="Scraped job not found")
        return result

    try:
        if refresh:
            matcher_logger.info("Recalculation requested | job_id=%s", job_id)
        result = get_scraped_job_matches(job_id, refresh=refresh)
        if not result:
            raise HTTPException(status_code=404, detail="Scraped job not found")
        return result
    finally:
        job_lock.release()


@app.get("/api/scraped-jobs/{job_id}/cached-matches")
def list_scraped_job_cached_matches(job_id: int):
    result = get_scraped_job_cached_matches(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Scraped job not found")
    return result


@app.on_event("startup")
async def announce_server_ready():
    startup_logger.info("Server ready")

_job_match_locks: dict[int, threading.Lock] = {}
_job_match_locks_guard = threading.Lock()
