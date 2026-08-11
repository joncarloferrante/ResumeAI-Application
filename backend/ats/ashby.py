from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from ..database import get_connection, _fetch_table_columns
from ..logging_config import get_logger
from .base import ATSAdapter, ATSImportResult

logger = get_logger("ats.ashby")
REQUEST_TIMEOUT = 30
ASHBY_HOST = "jobs.ashbyhq.com"
SOURCE_NAME = "Ashby"


def cleaned_text(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_careers_url(careers_url: str) -> str:
    raw_url = careers_url.strip()
    if raw_url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw_url):
        raw_url = f"https://{raw_url}"

    parsed = urlparse(raw_url)
    path = parsed.path.strip("/")
    parts = [segment for segment in path.split("/") if segment]
    if not parts:
        raise ValueError("Ashby careers URL must include a company slug.")

    company_slug = parts[0]
    normalized = parsed._replace(
        scheme=parsed.scheme or "https",
        netloc=ASHBY_HOST,
        path=f"/{company_slug}",
        params="",
        query="",
        fragment="",
    )
    return urlunparse(normalized)


def extract_company_slug(careers_url: str) -> str:
    parsed = urlparse(careers_url.strip())
    parts = [segment for segment in parsed.path.strip("/").split("/") if segment]
    return parts[0] if parts else ""


def company_name_from_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title() or slug


def fetch_page(url: str) -> tuple[requests.Response, BeautifulSoup]:
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            )
        },
    )
    response.raise_for_status()
    return response, BeautifulSoup(response.text, "html.parser")


def extract_app_data(html: str) -> dict:
    match = re.search(r"window\.__appData = (\{.*?\});", html, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def build_job_key(source_job_id: str) -> str:
    return f"ashby|{source_job_id}"


def normalize_employment_type(value: str | list[str] | None) -> str:
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value if item)
    text = cleaned_text(value)
    mapping = {
        "fulltime": "Full-time",
        "parttime": "Part-time",
        "contract": "Contract",
        "temporary": "Temporary",
        "intern": "Intern",
    }
    return mapping.get(text.replace("-", "").replace(" ", "").lower(), text)


def parse_job_description(description_html: str) -> dict[str, str]:
    soup = BeautifulSoup(description_html or "", "html.parser")
    text = cleaned_text(soup.get_text(" ", strip=True))
    return {
        "description": text,
        "responsibilities": "",
        "qualifications": "",
        "benefits": "",
        "additional_notes": "",
    }


def detect_location(job: dict) -> str:
    for field in ("locationExternalName", "locationName"):
        value = cleaned_text(job.get(field))
        if value:
            return value
    return ""


def detect_department(job: dict) -> str:
    for field in ("departmentExternalName", "departmentName", "teamExternalName", "teamName"):
        value = cleaned_text(job.get(field))
        if value:
            return value
    return ""


def detect_workplace_type(job: dict) -> str:
    value = cleaned_text(job.get("workplaceType"))
    return {
        "Remote": "remote",
        "Hybrid": "hybrid",
        "OnSite": "on-site",
        "Onsite": "on-site",
    }.get(value, value.lower())


def get_apply_url(base_url: str, source_job_id: str) -> str:
    return f"{base_url.rstrip('/')}/{source_job_id}/application"


def extract_job_url(base_url: str, source_job_id: str) -> str:
    return f"{base_url.rstrip('/')}/{source_job_id}"


def extract_title(job: dict, fallback_url: str) -> str:
    title = cleaned_text(job.get("title"))
    if title:
        return title
    return fallback_url.rsplit("/", 1)[-1].replace("-", " ").title()


def detect_ashby_board(careers_url: str) -> tuple[str, str, dict]:
    normalized_url = normalize_careers_url(careers_url)
    response, _ = fetch_page(f"{normalized_url}?embed=js")
    payload = extract_app_data(response.text)
    if not payload.get("jobBoard"):
        raise ValueError("This URL does not look like a public Ashby job board.")
    return normalized_url, extract_company_slug(normalized_url), payload


def load_jobs_from_board(careers_url: str) -> tuple[str, str, list[dict]]:
    normalized_url, company_slug, payload = detect_ashby_board(careers_url)
    job_board = payload.get("jobBoard") or {}
    postings = job_board.get("jobPostings") or []
    jobs: list[dict] = []

    for posting in postings:
        source_job_id = cleaned_text(posting.get("id"))
        if not source_job_id or not posting.get("isListed", True):
            continue

        detail_url = extract_job_url(normalized_url, source_job_id)
        detail_response, _ = fetch_page(detail_url)
        detail_payload = extract_app_data(detail_response.text)
        posting_details = detail_payload.get("posting") or {}
        description_parts = parse_job_description(posting_details.get("descriptionHtml") or "")
        title = extract_title(posting, detail_url)
        jobs.append(
            {
                "source": SOURCE_NAME,
                "company": company_name_from_slug(company_slug),
                "source_slug": company_slug,
                "source_job_id": source_job_id,
                "job_key": build_job_key(source_job_id),
                "title": title,
                "url": detail_url,
                "apply_url": get_apply_url(normalized_url, source_job_id),
                "location": detect_location(posting),
                "department": detect_department(posting),
                "employment_type": normalize_employment_type(posting.get("employmentType")),
                "workplace_type": detect_workplace_type(posting),
                "salary": cleaned_text(posting.get("scrapeableCompensationSalarySummary") or posting.get("compensationTierSummary")),
                "description": description_parts["description"],
                "responsibilities": description_parts["responsibilities"],
                "qualifications": description_parts["qualifications"],
                "benefits": description_parts["benefits"],
                "additional_notes": description_parts["additional_notes"],
                "posted_date": cleaned_text(posting.get("publishedDate")),
                "active": 1,
                "last_scraped": datetime.now(timezone.utc).isoformat(),
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return normalized_url, company_slug, jobs


def ensure_scraped_jobs_table() -> None:
    with get_connection() as conn:
        cursor = conn.cursor()
        columns = _fetch_table_columns(conn, "scraped_jobs")
        for column_name, definition in {
            "job_key": "TEXT",
            "source_job_id": "TEXT",
            "apply_url": "TEXT",
            "workplace_type": "TEXT",
            "posted_date": "TEXT",
            "last_seen_at": "TIMESTAMP",
            "manual_edited": "INTEGER NOT NULL DEFAULT 0",
            "manual_edited_at": "TIMESTAMP",
            "manual_edited_by": "TEXT",
            "source": "TEXT",
            "company": "TEXT",
            "responsibilities": "TEXT",
            "qualifications": "TEXT",
            "benefits": "TEXT",
            "additional_notes": "TEXT",
        }.items():
            if column_name not in columns:
                cursor.execute(f"ALTER TABLE scraped_jobs ADD COLUMN {column_name} {definition}")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_scraped_jobs_job_key ON scraped_jobs(job_key) WHERE job_key IS NOT NULL")
        conn.commit()


def _values_differ(existing: dict, stored: dict, fields: list[str]) -> bool:
    for field in fields:
        if str(existing.get(field) or "") != str(stored.get(field) or ""):
            return True
    return False


def upsert_normalized_jobs(source: dict, jobs: list[dict]) -> tuple[int, int, int]:
    ensure_scraped_jobs_table()
    inserted = updated = skipped = failed = 0
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        for job in jobs:
            try:
                job_key = job["job_key"]
                seen_keys.add(job_key)
                stored = {
                    "source": job["source"],
                    "company": job["company"],
                    "source_slug": job.get("source_slug", ""),
                    "source_job_id": job.get("source_job_id", ""),
                    "title": job.get("title", ""),
                    "url": job.get("url", ""),
                    "apply_url": job.get("apply_url", ""),
                    "location": job.get("location", ""),
                    "department": job.get("department", ""),
                    "employment_type": job.get("employment_type", ""),
                    "job_number": job.get("source_job_id", ""),
                    "workplace_type": job.get("workplace_type", ""),
                    "salary": job.get("salary", ""),
                    "description": job.get("description", ""),
                    "responsibilities": job.get("responsibilities", ""),
                    "qualifications": job.get("qualifications", ""),
                    "benefits": job.get("benefits", ""),
                    "posted_date": job.get("posted_date", ""),
                    "active": int(bool(job.get("active", 1))),
                    "last_scraped": job.get("last_scraped", datetime.now(timezone.utc).isoformat()),
                    "last_seen_at": job.get("last_seen_at", datetime.now(timezone.utc).isoformat()),
                    "job_key": job_key,
                    "additional_notes": job.get("additional_notes", ""),
                }
                cursor.execute("SELECT * FROM scraped_jobs WHERE job_key = ?", (job_key,))
                existing = cursor.fetchone()
                if existing:
                    if int(existing["manual_edited"] or 0):
                        cursor.execute(
                            "UPDATE scraped_jobs SET active = ?, last_scraped = ? WHERE job_key = ?",
                            (stored["active"], stored["last_scraped"], job_key),
                        )
                        skipped += 1
                        continue

                    if _values_differ(existing, stored, list(stored.keys())):
                        cursor.execute(
                            """
                            UPDATE scraped_jobs
                            SET source = ?, company = ?, source_job_id = ?, title = ?, url = ?, apply_url = ?,
                                location = ?, department = ?, employment_type = ?, job_number = ?, workplace_type = ?,
                                salary = ?, description = ?, responsibilities = ?, qualifications = ?, benefits = ?,
                                posted_date = ?, active = ?, last_scraped = ?, last_seen_at = ?, job_key = ?, additional_notes = ?
                            WHERE job_key = ?
                            """,
                            (
                                stored["source"], stored["company"], stored["source_job_id"], stored["title"], stored["url"], stored["apply_url"],
                                stored["location"], stored["department"], stored["employment_type"], stored["job_number"], stored["workplace_type"],
                                stored["salary"], stored["description"], stored["responsibilities"], stored["qualifications"], stored["benefits"],
                                stored["posted_date"], stored["active"], stored["last_scraped"], stored["last_seen_at"], stored["job_key"], stored["additional_notes"], job_key,
                            ),
                        )
                        updated += 1
                    else:
                        cursor.execute(
                            "UPDATE scraped_jobs SET active = ?, last_scraped = ?, last_seen_at = ? WHERE job_key = ?",
                            (stored["active"], stored["last_scraped"], stored["last_seen_at"], job_key),
                        )
                        skipped += 1
                else:
                    cursor.execute(
                        """
                        INSERT INTO scraped_jobs (
                            source, company, source_job_id, title, url, apply_url, location, department, employment_type,
                            source_slug, job_number, workplace_type, salary, description, responsibilities, qualifications, benefits,
                            posted_date, active, last_scraped, last_seen_at, job_key, manual_edited, manual_edited_at, manual_edited_by, additional_notes
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            stored["source"], stored["company"], stored["source_job_id"], stored["title"], stored["url"], stored["apply_url"],
                            stored["location"], stored["department"], stored["employment_type"], stored["source_slug"], stored["job_number"], stored["workplace_type"],
                            stored["salary"], stored["description"], stored["responsibilities"], stored["qualifications"], stored["benefits"],
                            stored["posted_date"], stored["active"], stored["last_scraped"], stored["last_seen_at"], stored["job_key"], 0, None, None, stored["additional_notes"],
                        ),
                    )
                    inserted += 1
            except Exception:
                logger.exception("ATS job import failed | careers_url=%s", source.get("careers_url"))
                failed += 1

        conn.commit()

    return inserted, updated, skipped


class AshbyAdapter(ATSAdapter):
    source_name = SOURCE_NAME

    def normalize_careers_url(self, careers_url: str) -> str:
        return normalize_careers_url(careers_url)

    def extract_company_slug(self, careers_url: str) -> str:
        return extract_company_slug(careers_url)

    def matches(self, careers_url: str) -> bool:
        raw_url = careers_url.strip()
        if raw_url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw_url):
            raw_url = f"https://{raw_url}"
        parsed = urlparse(raw_url)
        return ASHBY_HOST in parsed.netloc.lower() or ASHBY_HOST in parsed.path.lower()

    def fetch_jobs(self, careers_url: str) -> list[dict]:
        return load_jobs_from_board(careers_url)[2]

    def build_import_result(
        self,
        careers_url: str,
        jobs_found: int,
        jobs_added: int,
        jobs_updated: int,
        jobs_skipped: int,
        jobs_failed: int,
    ) -> ATSImportResult:
        return ATSImportResult(
            source=SOURCE_NAME,
            company=extract_company_slug(normalize_careers_url(careers_url)),
            careers_url=normalize_careers_url(careers_url),
            detected_ats=SOURCE_NAME,
            jobs_found=jobs_found,
            jobs_added=jobs_added,
            jobs_updated=jobs_updated,
            jobs_skipped=jobs_skipped,
            jobs_failed=jobs_failed,
        )
