from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from .base import ATSAdapter, ATSImportResult

SOURCE_NAME = "Lever"
LEVER_HOSTS = {"lever.co", "jobs.lever.co"}
REQUEST_TIMEOUT = 30


def cleaned_text(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_careers_url(careers_url: str) -> str:
    raw_url = careers_url.strip()
    if raw_url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw_url):
        raw_url = f"https://{raw_url}"

    parsed = urlparse(raw_url)
    host = parsed.netloc.lower()
    if "lever.co" not in host:
        raise ValueError("This careers platform is not supported yet.")

    path = parsed.path.strip("/")
    parts = [part for part in path.split("/") if part]
    if parts:
        return f"https://jobs.lever.co/{parts[0]}"

    slug = parsed.netloc.split(".")[0]
    if slug and slug != "jobs":
        return f"https://jobs.lever.co/{slug}"

    raise ValueError("Lever careers URL must include an account slug.")


def extract_company_slug(careers_url: str) -> str:
    parsed = urlparse(careers_url.strip())
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parts:
        return parts[-1]
    return parsed.netloc.split(".")[0]


def matches_lever(careers_url: str) -> bool:
    raw_url = careers_url.strip()
    if raw_url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw_url):
        raw_url = f"https://{raw_url}"
    parsed = urlparse(raw_url)
    host = parsed.netloc.lower()
    return "lever.co" in host


def fetch_json(url: str) -> list[dict]:
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
    payload = fetch_json(f"https://api.lever.co/v0/postings/{company_slug}?mode=json")
    jobs: list[dict] = []

    for job in payload:
        job_id = cleaned_text(job.get("id") or job.get("lever_id") or job.get("apply_url") or "")
        if not job_id:
            continue
        categories = job.get("categories") or {}
        teams = job.get("teams") or {}
        location = cleaned_text(categories.get("location")) or cleaned_text(job.get("location"))
        department = cleaned_text(teams.get("department")) or cleaned_text(categories.get("team"))
        job_url = cleaned_text(job.get("hostedUrl") or job.get("hosted_url") or f"https://jobs.lever.co/{company_slug}/{job_id}")

        jobs.append(
            {
                "source": SOURCE_NAME,
                "company": company_name_from_slug(company_slug),
                "source_slug": company_slug,
                "source_job_id": job_id,
                "job_key": f"lever|{job_id}",
                "title": cleaned_text(job.get("text")),
                "url": job_url,
                "apply_url": job_url,
                "location": location,
                "department": department,
                "employment_type": cleaned_text(categories.get("commitment") or job.get("workType")),
                "workplace_type": cleaned_text(categories.get("workplaceType")),
                "salary": "",
                "description": cleaned_text(job.get("description")),
                "responsibilities": "",
                "qualifications": "",
                "benefits": "",
                "additional_notes": "",
                "posted_date": cleaned_text(job.get("createdAt") or job.get("created_at")),
                "active": 1,
                "last_scraped": datetime.now(timezone.utc).isoformat(),
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return jobs


class LeverAdapter(ATSAdapter):
    source_name = SOURCE_NAME

    def normalize_careers_url(self, careers_url: str) -> str:
        return normalize_careers_url(careers_url)

    def extract_company_slug(self, careers_url: str) -> str:
        return extract_company_slug(careers_url)

    def matches(self, careers_url: str) -> bool:
        return matches_lever(careers_url)

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
