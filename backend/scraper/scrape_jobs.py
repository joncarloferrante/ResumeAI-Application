import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

START_URL = "https://atlanticrecruiters.com/job-postings/"
BASE_URL = "https://atlanticrecruiters.com"
TEST_LIMIT = 5
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "resumeai.db"
IGNORED_LINK_TEXTS = {
    "home",
    "jobs",
    "job listings",
    "contact",
    "view opportunities",
    "apply",
    "clear search results",
    "divisions",
    "all divisions",
    "all locations",
}


def get_soup(url: str) -> BeautifulSoup:
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return parsed._replace(path=path or "/").geturl()


def cleaned_text(text: str) -> str:
    return " ".join(text.split()).strip()


def text_matches_link(text: str) -> bool:
    return cleaned_text(text).lower() in IGNORED_LINK_TEXTS


def extract_field(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.I | re.S)
    if not match:
        return ""
    return cleaned_text(match.group(1))


def ensure_scraped_jobs_table() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scraped_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                url TEXT NOT NULL UNIQUE,
                location TEXT,
                department TEXT,
                employment_type TEXT,
                job_number TEXT,
                salary TEXT,
                description TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                last_scraped TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


def find_listing_links(soup: BeautifulSoup) -> list[str]:
    # The page contains navigation links plus real job links. We only keep the latter.
    job_urls: list[str] = []
    seen_urls: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        text = anchor.get_text(" ", strip=True)
        full_url = normalize_url(urljoin(BASE_URL, href))
        path = urlparse(full_url).path

        # Real postings are individual /jobs/... links, not nav items or filters.
        if not path.startswith("/jobs/"):
            continue
        if path == "/jobs/" or path.startswith("/jobs/division/"):
            continue
        if text_matches_link(text):
            continue

        if full_url in seen_urls:
            continue

        seen_urls.add(full_url)
        job_urls.append(full_url)

    return job_urls


def parse_json_ld_jobposting(soup: BeautifulSoup) -> dict:
    # Prefer structured data when present, then fall back to visible page text.
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or script.get_text(strip=True) or "").strip()
        if not raw:
            continue

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("@type") != "JobPosting":
                continue

            location = ""
            job_location = entry.get("jobLocation")
            if isinstance(job_location, dict):
                address = job_location.get("address")
                if isinstance(address, dict):
                    parts = [
                        address.get("addressLocality"),
                        address.get("addressRegion"),
                    ]
                    location = ", ".join(part for part in parts if part) or location
                elif isinstance(job_location.get("name"), str):
                    location = job_location["name"].strip()
            elif isinstance(job_location, list) and job_location:
                first = job_location[0]
                if isinstance(first, dict):
                    address = first.get("address")
                    if isinstance(address, dict):
                        parts = [
                            address.get("addressLocality"),
                            address.get("addressRegion"),
                        ]
                        location = ", ".join(part for part in parts if part) or location

            employment_type = entry.get("employmentType") or ""
            if isinstance(employment_type, list):
                employment_type = ", ".join(str(item) for item in employment_type if item)
            elif employment_type is None:
                employment_type = ""

            return {
                "location": location,
                "employment_type": employment_type,
            }

    return {
        "location": "",
        "employment_type": "",
    }


def extract_job_details(job_url: str) -> dict[str, str]:
    soup = get_soup(job_url)

    title = ""
    heading = soup.find("h1")
    if heading:
        title = cleaned_text(heading.get_text(" ", strip=True))
    if not title:
        og_title = soup.find("meta", attrs={"property": "og:title"})
        title = cleaned_text(og_title.get("content", "")) if og_title else ""

    details = parse_json_ld_jobposting(soup)
    content_root = soup.find("main") or soup.find("article") or soup.body
    cleaned_job_text = cleaned_text(content_root.get_text(" ", strip=True)) if content_root else ""
    description = cleaned_job_text

    # Debug the exact text the regex sees before we parse it.
    print("DEBUG CLEANED JOB TEXT (first 1000 chars):")
    print(cleaned_job_text[:1000])
    print("END DEBUG")

    # Parse the same cleaned text that is printed above.
    location = extract_field(r"Location:\s*(.*?)(?=\s+Type:)", cleaned_job_text)
    employment_type = extract_field(r"Type:\s*(.*?)(?=\s+Job\s+#)", cleaned_job_text)
    job_number = extract_field(r"Job\s+#\s*(\d+)", cleaned_job_text)
    salary = extract_field(r"Salary:\s*(.*?)(?=\s+Job Overview)", cleaned_job_text)

    if not details["location"]:
        details["location"] = location
    if not details["employment_type"]:
        details["employment_type"] = employment_type

    department = extract_field(r"Department:\s*(.*?)(?=\s+(?:Location|Type|Job\s+#|Salary|Job Overview))", cleaned_job_text)

    return {
        "title": title,
        "url": job_url,
        "location": details["location"],
        "department": department,
        "employment_type": details["employment_type"],
        "job_number": job_number,
        "salary": salary,
        "description": description,
    }


def save_scraped_job(job: dict[str, str]) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id
            FROM scraped_jobs
            WHERE url = ?
        """, (job["url"],))
        existing = cursor.fetchone()

        if existing:
            cursor.execute("""
                UPDATE scraped_jobs
                SET title = ?,
                    location = ?,
                    department = ?,
                    employment_type = ?,
                    job_number = ?,
                    salary = ?,
                    description = ?,
                    active = 1,
                    last_scraped = CURRENT_TIMESTAMP
                WHERE url = ?
            """, (
                job["title"],
                job["location"],
                job["department"],
                job["employment_type"],
                job["job_number"],
                job["salary"],
                job["description"],
                job["url"],
            ))
            return "updated"

        cursor.execute("""
            INSERT INTO scraped_jobs (
                title,
                url,
                location,
                department,
                employment_type,
                job_number,
                salary,
                description,
                active,
                last_scraped
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        """, (
            job["title"],
            job["url"],
            job["location"],
            job["department"],
            job["employment_type"],
            job["job_number"],
            job["salary"],
            job["description"],
        ))
        return "inserted"


def main() -> None:
    ensure_scraped_jobs_table()

    listing_soup = get_soup(START_URL)
    job_urls = find_listing_links(listing_soup)[:TEST_LIMIT]

    jobs_found = len(job_urls)
    jobs_inserted = 0
    jobs_updated = 0
    jobs_skipped_errors = 0

    total_jobs = len(job_urls)
    for index, job_url in enumerate(job_urls, start=1):
        print(f"Scraping job {index} of {total_jobs}")
        try:
            details = extract_job_details(job_url)
            save_result = save_scraped_job(details)
            if save_result == "inserted":
                jobs_inserted += 1
            elif save_result == "updated":
                jobs_updated += 1

            print("Job Title:", details["title"])
            print("Job URL:", details["url"])
            print("Location:", details["location"] or "Not found")
            print("Department:", details["department"] or "Not found")
            print("Employment Type:", details["employment_type"] or "Not found")
            print("Job Number:", details["job_number"] or "Not found")
            print("Salary:", details["salary"] or "Not found")
            print("Description Preview:", details["description"][:500])
            print("-" * 60)
        except Exception as exc:
            jobs_skipped_errors += 1
            print(f"Error scraping {job_url}: {exc}")

    print("Summary:")
    print(f"jobs found: {jobs_found}")
    print(f"jobs inserted: {jobs_inserted}")
    print(f"jobs updated: {jobs_updated}")
    print(f"jobs skipped/errors: {jobs_skipped_errors}")


if __name__ == "__main__":
    main()
