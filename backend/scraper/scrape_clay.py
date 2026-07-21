import argparse
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

START_URL = "https://www.clay.com/jobs"
JOB_BOARD_URL = "https://jobs.ashbyhq.com/claylabs?embed=js"
ASHBY_BASE_URL = "https://jobs.ashbyhq.com/claylabs"
SOURCE_NAME = "clay"
COMPANY_NAME = "Clay"
REQUEST_TIMEOUT = 30
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "resumeai.db"
DEBUG_HTML = False


def cleaned_text(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


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


def get_job_board_payload() -> dict:
    response, _ = fetch_page(JOB_BOARD_URL)
    return extract_app_data(response.text)


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", cleaned_text(value).lower()).strip("-")


def build_job_key(job: dict) -> str:
    return "|".join(
        [
            normalize_key(job.get("source", "")),
            normalize_key(job.get("source_job_id", "")),
        ]
    )


def normalize_heading_text(value: str | None) -> str:
    text = cleaned_text(value)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return text


def is_ignored_description_text(value: str) -> bool:
    normalized = normalize_heading_text(value).lower()
    return any(
        phrase in normalized
        for phrase in [
            "about clay",
            "our mission is to help organizations turn any growth idea into reality",
            "we see growth as a creative practice",
            "in 2025",
            "in 2026",
            "glassdoor",
            "investor",
            "community includes",
            "community information",
            "funding",
            "application",
            "hear from our employees directly",
            "about the role",
        ]
    )


def _strip_heading_suffix(value: str) -> str:
    normalized = normalize_heading_text(value).rstrip(":").lower()
    normalized = re.sub(r"\s*\(.*?\)\s*$", "", normalized).strip()
    return normalized


def is_responsibilities_heading(value: str) -> bool:
    normalized = _strip_heading_suffix(value)
    return normalized in {
        "responsibilities",
        "your responsibilities",
        "the role",
        "what you'll do",
        "what you will do",
    }


def is_qualifications_heading(value: str) -> bool:
    normalized = _strip_heading_suffix(value)
    return normalized in {
        "qualifications",
        "requirements",
        "preferred qualifications",
        "who you are",
        "bonus points",
        "what you'll bring",
        "what you will bring",
        "we'd love to hear from you if",
    }


def is_benefits_heading(value: str) -> bool:
    normalized = _strip_heading_suffix(value)
    return normalized in {
        "what we provide",
        "what we provide benefits perks",
        "benefits perks",
        "benefits",
        "what we offer",
        "what we offer benefits perks",
        "perks",
        "compensation and benefits",
        "total rewards",
    }


def is_section_heading(value: str) -> bool:
    return is_responsibilities_heading(value) or is_qualifications_heading(value) or is_benefits_heading(value)


def split_content_items(value: str) -> list[str]:
    text = cleaned_text(value)
    if not text:
        return []
    parts = re.split(r"(?:(?:^|\s)(?:[-???]|\d+[.)]))\s+", text)
    if len(parts) > 1:
        items = [cleaned_text(part) for part in parts if cleaned_text(part)]
        return items
    return [text]


def is_valid_salary(value: str) -> bool:
    text = cleaned_text(value)
    if not text:
        return False
    lowered = text.lower()
    if any(term in lowered for term in ["series", "fund", "revenue", "valuation"]):
        return False
    if re.search(r"\b(?:m|b)\b", lowered):
        return False
    if re.search(r"\$\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
        return True
    if re.search(r"\$\s*\d+(?:\.\d+)?\s*(?:per hour|/hour|annually|per year|year)\b", lowered):
        return True
    if re.search(r"\$\s*\d{2,3}k(?:\s*[-?]\s*\$?\d{2,3}k)?", lowered):
        return True
    if re.search(r"\$\s*\d+(?:\.\d+)?\s*[-?]\s*\$?\s*\d+(?:\.\d+)?", lowered):
        return True
    return False


def parse_description_html(description_html: str) -> tuple[list[str], list[str], list[str], str]:
    soup = BeautifulSoup(description_html or "", "html.parser")
    text = cleaned_text(soup.get_text(" ", strip=True))

    sections = {"overview": [], "responsibilities": [], "qualifications": [], "benefits": [], "additional_notes": [], "schedule": [], "compensation": []}
    current = None
    capture_started = False
    content_root = soup.body or soup

    heading_map = {
        "overview": {
            "about the role",
            "the role",
            "position overview",
            "about this position",
            "who we are",
            "about us",
            "job overview",
        },
        "responsibilities": {
            "responsibilities",
            "key responsibilities",
            "what you'll do",
            "what you will do",
            "your impact",
            "in this role",
            "what you'll be doing",
            "duties",
            "job responsibilities",
            "your responsibilities",
        },
        "qualifications": {
            "qualifications",
            "requirements",
            "what we're looking for",
            "we'd love to hear from you if",
            "skills and experience",
            "required qualifications",
            "preferred qualifications",
            "who you are",
            "what you bring",
            "bonus points",
        },
        "benefits": {
            "benefits",
            "benefits and perks",
            "benefits & perks",
            "what we offer",
            "what we provide",
            "perks",
            "compensation and benefits",
            "total rewards",
        },
        "additional_notes": {
            "additional information",
            "additional notes",
            "equal opportunity",
            "eeo statement",
            "work authorization",
            "visa sponsorship",
            "application information",
        },
        "schedule": {
            "schedule",
        },
        "compensation": {
            "compensation",
            "salary",
            "compensation and benefits",
        },
    }

    def classify_heading(value: str) -> str | None:
        normalized = _strip_heading_suffix(value)
        for section_name, aliases in heading_map.items():
            if normalized in aliases:
                return section_name
        return None

    for element in content_root.find_all(["h1", "h2", "h3", "h4", "p", "li"], recursive=True):
        line = cleaned_text(element.get_text(" ", strip=True))
        if not line:
            continue

        if is_ignored_description_text(line):
            continue

        section_name = classify_heading(line)
        if section_name:
            current = section_name
            capture_started = True
            continue

        if element.name in {"h1", "h2", "h3", "h4"} and capture_started:
            current = None if current in {"overview", "schedule", "compensation", "additional_notes"} else current
            continue

        if not capture_started or current is None:
            continue

        for item in split_content_items(line):
            cleaned_item = cleaned_text(item)
            if cleaned_item and cleaned_item not in sections[current]:
                sections[current].append(cleaned_item)

    def dedupe(items: list[str]) -> list[str]:
        seen = set()
        cleaned = []
        for item in items:
            value = cleaned_text(item)
            if value and value.lower() not in seen:
                seen.add(value.lower())
                cleaned.append(value)
        return cleaned

    overview = dedupe(sections["overview"])
    responsibilities = dedupe(sections["responsibilities"])
    qualifications = dedupe(sections["qualifications"])
    benefits = dedupe(sections["benefits"])
    additional_notes = dedupe(sections["additional_notes"] + sections["schedule"] + sections["compensation"])

    ordered_parts: list[str] = []
    if overview:
        ordered_parts.extend(overview)
    for heading, values in [
        ("Responsibilities", responsibilities),
        ("Qualifications", qualifications),
        ("Benefits", benefits),
        ("Additional Notes", additional_notes),
    ]:
        if values:
            ordered_parts.append(heading + ":")
            ordered_parts.extend(values)

    description_text = "\n\n".join(ordered_parts) if ordered_parts else text

    return responsibilities, qualifications, benefits, description_text


def detect_salary(job: dict, description_text: str) -> str:
    for field_name in ("scrapeableCompensationSalarySummary", "compensationTierSummary"):
        value = cleaned_text(job.get(field_name))
        if value and is_valid_salary(value):
            return value

    match = re.search(
        r"(\$\s*\d+(?:,\d{3})*(?:\.\d+)?(?:\s*[-?]\s*\$?\s*\d+(?:,\d{3})*(?:\.\d+)?)?(?:\s*(?:per hour|/hour|annually|per year|year))?)",
        description_text,
        re.I,
    )
    if not match:
        return ""
    value = cleaned_text(match.group(1))
    return value if is_valid_salary(value) else ""


def detect_location(job: dict) -> str:
    for field in ("locationExternalName", "locationName"):
        value = cleaned_text(job.get(field))
        if value:
            return value
    return ""


def detect_department(job: dict) -> str:
    for field in ("departmentExternalName", "departmentName", "teamExternalName", "teamName"):
        value = cleaned_text(job.get(field))
        if value and not is_employment_type_label(value):
            return value
    return ""


def is_employment_type_label(value: str) -> bool:
    normalized = cleaned_text(value).lower()
    return any(
        token in normalized
        for token in [
            "full-time",
            "full time",
            "part-time",
            "part time",
            "contract",
            "temporary",
            "temp",
            "perm",
            "contingency",
            "intern",
            "hybrid",
            "remote",
            "onsite",
            "on-site",
        ]
    )


def detect_employment_type(job: dict) -> str:
    value = cleaned_text(job.get("employmentType"))
    mapping = {
        "FullTime": "Full-time",
        "PartTime": "Part-time",
        "Contract": "Contract",
        "Temporary": "Temporary",
        "Intern": "Intern",
    }
    return mapping.get(value, value)


def detect_workplace_type(job: dict) -> str:
    value = cleaned_text(job.get("workplaceType"))
    mapping = {
        "Remote": "remote",
        "Hybrid": "hybrid",
        "OnSite": "on-site",
        "Onsite": "on-site",
    }
    return mapping.get(value, value.lower())


def get_apply_url(job_id: str) -> str:
    return f"{ASHBY_BASE_URL}/{job_id}/application"


def scrape_clay_jobs() -> list[dict]:
    payload = get_job_board_payload()
    job_board = payload.get("jobBoard") or {}
    job_postings = job_board.get("jobPostings") or []
    jobs: list[dict] = []

    for job in job_postings:
        job_id = cleaned_text(job.get("id"))
        if not job_id or not job.get("isListed", True):
            continue

        detail_response, detail_soup = fetch_page(f"{ASHBY_BASE_URL}/{job_id}")
        detail_app_data = extract_app_data(detail_response.text)
        posting = detail_app_data.get("posting") or {}
        description_html = posting.get("descriptionHtml") or ""
        responsibilities, qualifications, benefits, description_text = parse_description_html(description_html)

        full_description = description_text or cleaned_text(detail_soup.get_text(" ", strip=True))
        salary = detect_salary(posting or job, full_description)
        workplace_type = detect_workplace_type(job)
        employment_type = detect_employment_type(job)
        location = detect_location(job)
        department = detect_department(job)
        source_url = f"{ASHBY_BASE_URL}/{job_id}"

        jobs.append(
            {
                "title": cleaned_text(job.get("title")),
                "company": COMPANY_NAME,
                "source": SOURCE_NAME,
                "source_job_id": job_id,
                "job_number": job_id,
                "url": source_url,
                "apply_url": get_apply_url(job_id),
                "location": location,
                "department": department,
                "employment_type": employment_type,
                "workplace_type": workplace_type,
                "salary": salary,
                "description": full_description,
                "responsibilities": responsibilities,
                "qualifications": qualifications,
                "benefits": benefits,
                "posted_date": cleaned_text(job.get("publishedDate")),
                "active": True,
                "last_scraped": datetime.now(timezone.utc).isoformat(),
                "job_key": build_job_key({"source": SOURCE_NAME, "source_job_id": job_id}),
            }
        )

    return jobs


def ensure_scraped_jobs_table() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(scraped_jobs)")
        columns = {row[1] for row in cursor.fetchall()}

        for column_name in ["source_job_id", "apply_url", "workplace_type", "posted_date", "job_key", "manual_edited", "manual_edited_at", "manual_edited_by", "additional_notes"]:
            if column_name not in columns:
                column_definition = "INTEGER NOT NULL DEFAULT 0" if column_name == "manual_edited" else "TEXT"
                cursor.execute(f"ALTER TABLE scraped_jobs ADD COLUMN {column_name} {column_definition}")

        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_scraped_jobs_job_key ON scraped_jobs(job_key) WHERE job_key IS NOT NULL")
        conn.commit()


def serialize_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def save_clay_jobs(jobs: list[dict]) -> tuple[int, int, int, int]:
    ensure_scraped_jobs_table()
    inserted = updated = unchanged = 0
    seen_keys: set[str] = set()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        for job in jobs:
            job_key = job["job_key"]
            seen_keys.add(job_key)
            stored_row = {
                "source": SOURCE_NAME,
                "company": COMPANY_NAME,
                "source_job_id": job.get("source_job_id", ""),
                "title": job.get("title", ""),
                "url": job.get("url", ""),
                "apply_url": job.get("apply_url", ""),
                "location": job.get("location", ""),
                "department": job.get("department", ""),
                "employment_type": job.get("employment_type", ""),
                "job_number": job.get("job_number", "") or job.get("source_job_id", ""),
                "workplace_type": job.get("workplace_type", ""),
                "salary": job.get("salary", ""),
                "description": job.get("description", ""),
                "responsibilities": serialize_list(job.get("responsibilities") or []),
                "qualifications": serialize_list(job.get("qualifications") or []),
                "benefits": serialize_list(job.get("benefits") or []),
                "posted_date": job.get("posted_date", ""),
                "active": 1,
                "last_scraped": job.get("last_scraped", datetime.now(timezone.utc).isoformat()),
                "job_key": job_key,
            }

            cursor.execute("SELECT * FROM scraped_jobs WHERE job_key = ?", (job_key,))
            existing = cursor.fetchone()
            if existing:
                changed = any(str(existing[key] or "") != str(value or "") for key, value in stored_row.items() if key in existing.keys())
                if changed and not int(existing["manual_edited"] or 0):
                    cursor.execute(
                        """
                        UPDATE scraped_jobs
                        SET source = ?, company = ?, source_job_id = ?, title = ?, url = ?, apply_url = ?,
                            location = ?, department = ?, employment_type = ?, job_number = ?, workplace_type = ?, salary = ?,
                            description = ?, responsibilities = ?, qualifications = ?, benefits = ?, posted_date = ?,
                            active = ?, last_scraped = ?, job_key = ?
                        WHERE job_key = ?
                        """,
                        (
                            stored_row["source"],
                            stored_row["company"],
                            stored_row["source_job_id"],
                            stored_row["title"],
                            stored_row["url"],
                            stored_row["apply_url"],
                            stored_row["location"],
                            stored_row["department"],
                            stored_row["employment_type"],
                            stored_row["job_number"],
                            stored_row["workplace_type"],
                            stored_row["salary"],
                            stored_row["description"],
                            stored_row["responsibilities"],
                            stored_row["qualifications"],
                            stored_row["benefits"],
                            stored_row["posted_date"],
                            stored_row["active"],
                            stored_row["last_scraped"],
                            stored_row["job_key"],
                            job_key,
                        ),
                    )
                    updated += 1
                else:
                    cursor.execute(
                        """
                        UPDATE scraped_jobs
                        SET active = ?, last_scraped = ?
                        WHERE job_key = ?
                        """,
                        (stored_row["active"], stored_row["last_scraped"], job_key),
                    )
                    unchanged += 1
            else:
                cursor.execute(
                    """
                    INSERT INTO scraped_jobs (
                        source, company, source_job_id, title, url, apply_url, location, department,
                        employment_type, job_number, workplace_type, salary, description, responsibilities, qualifications, benefits,
                        posted_date, active, last_scraped, job_key, manual_edited, manual_edited_at, manual_edited_by, additional_notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL)
                    """,
                    (
                        stored_row["source"],
                        stored_row["company"],
                        stored_row["source_job_id"],
                        stored_row["title"],
                        stored_row["url"],
                        stored_row["apply_url"],
                        stored_row["location"],
                        stored_row["department"],
                        stored_row["employment_type"],
                        stored_row["job_number"],
                        stored_row["workplace_type"],
                        stored_row["salary"],
                        stored_row["description"],
                        stored_row["responsibilities"],
                        stored_row["qualifications"],
                        stored_row["benefits"],
                        stored_row["posted_date"],
                        stored_row["active"],
                        stored_row["last_scraped"],
                        stored_row["job_key"],
                    ),
                )
                inserted += 1

        marked_inactive = 0
        if seen_keys:
            placeholders = ", ".join("?" for _ in seen_keys)
            cursor.execute(
                f"""
                UPDATE scraped_jobs
                SET active = 0,
                    last_scraped = ?
                WHERE source = ?
                  AND job_key IS NOT NULL
                  AND job_key NOT IN ({placeholders})
                """,
                (datetime.now(timezone.utc).isoformat(), SOURCE_NAME, *seen_keys),
            )
            marked_inactive = cursor.rowcount

        conn.commit()

    return inserted, updated, unchanged, marked_inactive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Clay jobs from Ashby.")
    parser.add_argument("--save", action="store_true", help="Save Clay jobs into the shared scraped_jobs table.")
    return parser.parse_args()


def main() -> None:
    start = time.perf_counter()
    print("Clay scrape started")
    jobs = scrape_clay_jobs()
    print(f"Jobs fetched: {len(jobs)}")
    for index, job in enumerate(jobs, start=1):
        print(f"\nJob {index}")
        print(json.dumps(job, indent=2, ensure_ascii=False))

    if args := parse_args():
        if args.save:
            inserted, updated, unchanged, marked_inactive = save_clay_jobs(jobs)
            print(f"Jobs inserted: {inserted}")
            print(f"Jobs updated: {updated}")
            print(f"Jobs marked inactive: {marked_inactive}")
            print("Scrape complete")
            print(f"Total runtime: {time.perf_counter() - start:.2f} seconds")


if __name__ == "__main__":
    main()
