from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from .base import ATSAdapter, ATSImportResult

SOURCE_NAME = "Greenhouse"
REQUEST_TIMEOUT = 30
GREENHOUSE_BOARD_HOSTS = {"greenhouse.io", "job-boards.greenhouse.io", "boards-api.greenhouse.io", "boards.greenhouse.io"}


def cleaned_text(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_careers_url(careers_url: str) -> str:
    raw_url = careers_url.strip()
    if raw_url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw_url):
        raw_url = f"https://{raw_url}"

    parsed = urlparse(raw_url)
    host = parsed.netloc.lower()
    if not any(suffix in host for suffix in GREENHOUSE_BOARD_HOSTS):
        raise ValueError("This careers platform is not supported yet.")

    path = parsed.path.strip("/")
    parts = [part for part in path.split("/") if part]
    company_slug = parts[0] if parts else parsed.netloc.split(".")[0]
    if "boards-api.greenhouse.io" in host:
        return raw_url

    if "job-boards.greenhouse.io" in host or host.endswith("greenhouse.io"):
        if parts:
            if parts[0] == "jobs" and len(parts) >= 2:
                company_slug = parts[1]
            elif parts[0] != "jobs":
                company_slug = parts[0]
            if company_slug:
                return f"https://boards-api.greenhouse.io/v1/boards/{company_slug}"

    raise ValueError("Greenhouse careers URL must include a board name.")


def extract_company_slug(careers_url: str) -> str:
    parsed = urlparse(careers_url.strip())
    path = parsed.path.strip("/")
    parts = [part for part in path.split("/") if part]
    host = parsed.netloc.lower()
    if "boards-api.greenhouse.io" in host and parts:
        return parts[-1]
    if ("job-boards.greenhouse.io" in host or host.endswith("greenhouse.io")) and parts:
        if parts[0] == "jobs" and len(parts) >= 2:
            return parts[1]
        return parts[0]
    if parts:
        return parts[0]
    return parsed.netloc.split(".")[0]


def matches_greenhouse(careers_url: str) -> bool:
    raw_url = careers_url.strip()
    if raw_url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw_url):
        raw_url = f"https://{raw_url}"
    parsed = urlparse(raw_url)
    host = parsed.netloc.lower()
    return "greenhouse.io" in host or "boards-api.greenhouse.io" in host or "job-boards.greenhouse.io" in host


def fetch_json(url: str) -> dict:
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
    return response.json()


def company_name_from_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title() or slug


def fetch_jobs(careers_url: str) -> list[dict]:
    normalized_url = normalize_careers_url(careers_url)
    company_slug = extract_company_slug(normalized_url)
    payload = fetch_json(f"{normalized_url}/jobs?content=true")
    jobs: list[dict] = []

    for job in payload.get("jobs", []):
        absolute_url = job.get("absolute_url") or ""
        job_id = str(job.get("id") or absolute_url.rsplit("/", 1)[-1]).strip()
        if not job_id:
            continue

        location = ""
        if isinstance(job.get("location"), dict):
            location = cleaned_text(job["location"].get("name"))
        office = job.get("location")
        if not location and isinstance(office, dict):
            location = cleaned_text(office.get("name"))

        departments = job.get("departments") or []
        department = cleaned_text(departments[0].get("name")) if departments and isinstance(departments[0], dict) else ""
        employment_type = cleaned_text(job.get("employment_type"))
        if not employment_type and job.get("employment_type") is None:
            employment_type = "Full-time" if str(job.get("type") or "").lower() == "full_time" else ""

        jobs.append(
            {
                "source": SOURCE_NAME,
                "company": company_name_from_slug(company_slug),
                "source_slug": company_slug,
                "source_job_id": job_id,
                "job_key": f"greenhouse|{job_id}",
                "title": cleaned_text(job.get("title")),
                "url": absolute_url,
                "apply_url": absolute_url,
                "location": location,
                "department": department,
                "employment_type": employment_type,
                "workplace_type": "",
                "salary": "",
                "description": cleaned_text(job.get("content")),
                "responsibilities": "",
                "qualifications": "",
                "benefits": "",
                "additional_notes": "",
                "posted_date": cleaned_text(job.get("updated_at") or job.get("updated_at_formatted") or job.get("created_at")),
                "active": 1,
                "last_scraped": datetime.now(timezone.utc).isoformat(),
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return jobs


class GreenhouseAdapter(ATSAdapter):
    source_name = SOURCE_NAME

    def normalize_careers_url(self, careers_url: str) -> str:
        return normalize_careers_url(careers_url)

    def extract_company_slug(self, careers_url: str) -> str:
        return extract_company_slug(careers_url)

    def matches(self, careers_url: str) -> bool:
        return matches_greenhouse(careers_url)

    def fetch_jobs(self, careers_url: str) -> list[dict]:
        return fetch_jobs(careers_url)

    def build_import_result(
        self,
        careers_url: str,
        jobs_found: int,
        jobs_added: int,
        jobs_updated: int,
        jobs_skipped: int,
        jobs_failed: int,
    ) -> ATSImportResult:
        normalized_url = normalize_careers_url(careers_url)
        return ATSImportResult(
            source=SOURCE_NAME,
            company=extract_company_slug(normalized_url),
            careers_url=normalized_url,
            detected_ats=SOURCE_NAME,
            jobs_found=jobs_found,
            jobs_added=jobs_added,
            jobs_updated=jobs_updated,
            jobs_skipped=jobs_skipped,
            jobs_failed=jobs_failed,
        )
