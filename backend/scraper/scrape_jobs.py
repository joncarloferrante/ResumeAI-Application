import json
import logging
import re
import sqlite3
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from ..database import get_connection
from ..logging_config import get_logger

START_URL = "https://atlanticrecruiters.com/job-postings/"
BASE_URL = "https://atlanticrecruiters.com"
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

scraper_logger = get_logger("scraper")


def get_soup(url: str) -> BeautifulSoup:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        html = page.content()
        browser.close()

    return BeautifulSoup(html, "html.parser")


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
    with get_connection() as conn:
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
                last_scraped TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                manual_edited INTEGER NOT NULL DEFAULT 0,
                manual_edited_at TIMESTAMP,
                manual_edited_by TEXT
            )
        """)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(scraped_jobs)")
        columns = {row[1] for row in cursor.fetchall()}
        for column_name, column_definition in {
            "manual_edited": "INTEGER NOT NULL DEFAULT 0",
            "manual_edited_at": "TIMESTAMP",
            "manual_edited_by": "TEXT",
        }.items():
            if column_name not in columns:
                cursor.execute(f"ALTER TABLE scraped_jobs ADD COLUMN {column_name} {column_definition}")


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
    with get_connection() as conn:
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
                WHERE url = ? AND COALESCE(manual_edited, 0) = 0
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
            if cursor.rowcount == 0:
                cursor.execute("""
                    UPDATE scraped_jobs
                    SET active = 1,
                        last_scraped = CURRENT_TIMESTAMP
                    WHERE url = ?
                """, (job["url"],))
            else:
                scraper_logger.info("Existing jobs updated | url=%s", job["url"])
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
                last_scraped,
                manual_edited,
                manual_edited_at,
                manual_edited_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP, 0, NULL, NULL)
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
        scraper_logger.info("New jobs added | url=%s", job["url"])
        return "inserted"


def mark_missing_jobs_inactive(active_urls: set[str]) -> int:
    with get_connection() as conn:
        cursor = conn.cursor()
        if not active_urls:
            cursor.execute(
                """
                UPDATE scraped_jobs
                SET active = 0,
                    last_scraped = CURRENT_TIMESTAMP
                WHERE active = 1
                """
            )
        else:
            cursor.execute(
                f"""
                UPDATE scraped_jobs
                SET active = 0,
                    last_scraped = CURRENT_TIMESTAMP
                WHERE url NOT IN ({",".join("?" for _ in active_urls)})
                  AND active = 1
                """,
                tuple(active_urls),
            )
        if cursor.rowcount > 0:
            scraper_logger.info("Jobs marked inactive | count=%s", cursor.rowcount)
        return cursor.rowcount


def main() -> None:
    start = time.perf_counter()
    ensure_scraped_jobs_table()
    scraper_logger.info("Scraper started")

    listing_soup = get_soup(START_URL)
    job_urls = find_listing_links(listing_soup)

    jobs_found = len(job_urls)
    scraper_logger.info("Website currently being scraped | url=%s | jobs_discovered=%s", START_URL, jobs_found)
    jobs_inserted = 0
    jobs_updated = 0
    jobs_skipped_errors = 0

    total_jobs = len(job_urls)
    for index, job_url in enumerate(job_urls, start=1):
        scraper_logger.info("Processing job %s of %s", index, total_jobs)
        try:
            details = extract_job_details(job_url)
            save_result = save_scraped_job(details)
            if save_result == "inserted":
                jobs_inserted += 1
            elif save_result == "updated":
                jobs_updated += 1

            scraper_logger.debug("Job scraped | title=%s | url=%s | location=%s | department=%s", details["title"], details["url"], details["location"] or "Not found", details["department"] or "Not found")
        except Exception as exc:
            jobs_skipped_errors += 1
            scraper_logger.exception("Error scraping job | url=%s", job_url)

    jobs_marked_inactive = mark_missing_jobs_inactive(set(job_urls))

    scraper_logger.info("Scrape completed")
    scraper_logger.info("Total runtime %.2f seconds", time.perf_counter() - start)
    scraper_logger.info("jobs found: %s", jobs_found)
    scraper_logger.info("jobs inserted: %s", jobs_inserted)
    scraper_logger.info("jobs updated: %s", jobs_updated)
    scraper_logger.info("jobs skipped/errors: %s", jobs_skipped_errors)
    scraper_logger.info("jobs marked inactive: %s", jobs_marked_inactive)


if __name__ == "__main__":
    main()
