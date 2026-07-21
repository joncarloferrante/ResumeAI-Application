import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# This scraper is intentionally separate from the Atlantic Group scraper.
# It only targets the Blair & Potts careers page and prints normalized results
# to the console so the output can be reviewed before any database integration.

START_URL = "https://blairandpotts.com/careers.asp"
SOURCE_NAME = "Blair & Potts"
COMPANY_NAME = "Blair & Potts"
REQUEST_TIMEOUT = 30
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "resumeai.db"
DEBUG_HTML = False
FOOTER_STOP_MARKERS = [
    "Two Stamford Plaza",
    "admin@blairandpotts.com",
    "©",
]
SECTION_STOP_MARKERS = [
    "Responsibilities:",
    "Qualifications",
    "Offer Details:",
]


def cleaned_text(value: str | None) -> str:
    """Normalize whitespace and guard against missing values."""
    return " ".join(str(value or "").split()).strip()


def fetch_page(url: str) -> tuple[requests.Response, BeautifulSoup]:
    """Fetch the Blair & Potts careers page with requests and parse the HTML."""
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


def normalize_key(value: str) -> str:
    """Create a stable key fragment for duplicate prevention."""
    return re.sub(r"[^a-z0-9]+", "-", cleaned_text(value).lower()).strip("-")


def is_section_heading(text: str) -> bool:
    """Return True for non-job section labels that should not start a job."""
    normalized = cleaned_text(text).upper().rstrip(":")
    return normalized in {
        "OPEN POSITIONS",
        "RESPONSIBILITIES",
        "QUALIFICATIONS",
        "OFFER DETAILS",
        "BENEFITS",
        "JOB OVERVIEW",
        "OVERVIEW",
        "ABOUT THE ROLE",
        "THE ROLE",
        "POSITION OVERVIEW",
        "ABOUT THIS POSITION",
        "WHO WE ARE",
        "ABOUT US",
        "KEY RESPONSIBILITIES",
        "WHAT YOU'LL DO",
        "WHAT YOU WILL DO",
        "YOUR IMPACT",
        "IN THIS ROLE",
        "WHAT YOU'LL BE DOING",
        "DUTIES",
        "JOB RESPONSIBILITIES",
        "YOUR RESPONSIBILITIES",
        "REQUIREMENTS",
        "WHAT WE'RE LOOKING FOR",
        "WE'D LOVE TO HEAR FROM YOU IF",
        "SKILLS AND EXPERIENCE",
        "REQUIRED QUALIFICATIONS",
        "PREFERRED QUALIFICATIONS",
        "WHO YOU ARE",
        "WHAT YOU BRING",
        "BENEFITS AND PERKS",
        "BENEFITS & PERKS",
        "WHAT WE OFFER",
        "WHAT WE PROVIDE",
        "PERKS",
        "COMPENSATION AND BENEFITS",
        "TOTAL REWARDS",
        "ADDITIONAL INFORMATION",
        "ADDITIONAL NOTES",
        "EEO STATEMENT",
        "EQUAL OPPORTUNITY",
        "WORK AUTHORIZATION",
        "VISA SPONSORSHIP",
        "APPLICATION INFORMATION",
        "SCHEDULE",
        "COMPENSATION",
        "SALARY",
    }


def _strip_heading_suffix(value: str) -> str:
    normalized = cleaned_text(value).rstrip(":")
    normalized = re.sub(r"\s*\(.*?\)\s*$", "", normalized).strip()
    return normalized.lower()


def classify_section_heading(text: str) -> str | None:
    """Map common posting headings to the structured section buckets."""
    normalized = _strip_heading_suffix(text)
    heading_map = {
        "overview": {
            "job overview",
            "overview",
            "about the role",
            "the role",
            "position overview",
            "about this position",
            "who we are",
            "about us",
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
            "offer details",
        },
        "additional_notes": {
            "additional information",
            "additional notes",
            "eeo statement",
            "equal opportunity",
            "work authorization",
            "visa sponsorship",
            "application information",
        },
        "schedule": {"schedule"},
        "compensation": {"compensation", "salary"},
    }
    for section_name, aliases in heading_map.items():
        if normalized in aliases:
            return section_name
    return None


def is_likely_job_title(text: str) -> bool:
    """Detect uppercase job titles without depending on CSS classes."""
    normalized = cleaned_text(text)
    if not normalized or is_section_heading(normalized):
        return False
    if len(normalized) < 4:
        return False
    if not re.fullmatch(r"[A-Z0-9 ,/&'().-]+", normalized):
        return False
    if normalized.endswith(":"):
        return False
    words = normalized.split()
    if len(words) < 2:
        return False
    # Reject lines that look like footer/contact noise.
    if any(marker.lower() in normalized.lower() for marker in ["stamford", "phone", "fax", "email", "careers at"]):
        return False
    return True


def extract_block_texts(page: BeautifulSoup) -> list[str]:
    """Collect visible text lines after the Open Positions header."""
    body = page.body or page
    raw_lines = [cleaned_text(line) for line in body.get_text("\n", strip=True).splitlines()]

    start_index = next(
        (index for index, line in enumerate(raw_lines) if line.lower().startswith("open positions")),
        None,
    )
    if start_index is None:
        return []

    texts: list[str] = []
    for line in raw_lines[start_index + 1 :]:
        if not line:
            continue
        if any(marker.lower() in line.lower() for marker in FOOTER_STOP_MARKERS):
            break
        texts.append(line)
    return texts


def split_jobs_from_texts(texts: list[str]) -> list[tuple[str, list[str]]]:
    """Split the content into one block per job using title heuristics."""
    jobs: list[tuple[str, list[str]]] = []
    current_title = ""
    current_lines: list[str] = []
    expected_title_hits = {
        "TRUSTS AND ESTATES PARALEGAL": False,
        "PART-TIME PROJECT ASSISTANT": False,
    }

    for text in texts:
        if is_section_heading(text):
            continue
        if is_likely_job_title(text):
            if current_title:
                jobs.append((current_title, current_lines))
            current_title = text
            current_lines = []
            if text in expected_title_hits:
                expected_title_hits[text] = True
            continue
        if current_title:
            if any(marker.lower() in text.lower() for marker in FOOTER_STOP_MARKERS):
                break
            current_lines.append(text)

    if current_title:
        jobs.append((current_title, current_lines))

    return jobs


def gather_section_lines(lines: list[str]) -> dict[str, list[str]]:
    """Split a job block into normalized section buckets."""
    sections = {
        "description": [],
        "overview": [],
        "responsibilities": [],
        "qualifications": [],
        "benefits": [],
        "additional_notes": [],
        "schedule": [],
        "compensation": [],
    }
    current_bucket = "description"

    for line in lines:
        lowered = cleaned_text(line).lower().rstrip(":")
        bucket = classify_section_heading(line)
        if bucket:
            current_bucket = bucket
            continue
        if lowered.startswith("please send cover letter") or lowered.startswith("please send resume"):
            current_bucket = "description"
            sections["description"].append(line)
            continue

        sections[current_bucket].append(line)

    return sections


def detect_location(text_lines: list[str]) -> str:
    """Extract a likely location from the job text."""
    combined = " ".join(text_lines)
    patterns = [
        r"\b(Stamford,\s*CT)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, combined)
        if match:
            value = cleaned_text(match.group(1))
            return value
    return ""


def detect_salary(text_lines: list[str]) -> str:
    """Extract salary or compensation details when they appear."""
    combined = " ".join(text_lines)
    match = re.search(r"(\$\d+(?:\.\d+)?(?:\s*(?:per hour|/hour|hour|annually|year))?)", combined, re.I)
    if match:
        return cleaned_text(match.group(1))
    if "competitive salary" in combined.lower():
        return "Competitive salary"
    if "salary commensurate with experience" in combined.lower():
        return "Salary commensurate with experience"
    return ""


def bullet_list(text_lines: list[str]) -> list[str]:
    """Convert raw section lines into concise bullet-style strings."""
    items: list[str] = []
    for line in text_lines:
        item = cleaned_text(line)
        item = re.sub(r"^[•\-\u2022]+\s*", "", item)
        if item and item not in items:
            items.append(item)
    return items


def detect_employment_type(text_lines: list[str]) -> str:
    """Infer employment type from the job text."""
    combined = " ".join(text_lines).lower()
    if "part-time" in combined or "part time" in combined:
        return "Part-time"
    if "full-time" in combined or "full time" in combined:
        return "Full-time"
    return ""


def detect_application_link(job_section) -> str:
    """Find any application email or link in the job section."""
    text = cleaned_text(job_section.get_text(" ", strip=True))
    email_match = re.search(r"[\w.\-+]+@[\w.\-]+\.\w+", text)
    if email_match:
        return f"mailto:{email_match.group(0)}"

    for link in job_section.find_all("a", href=True):
        href = link["href"].strip()
        if href.startswith("mailto:") or href.startswith("http"):
            return href
        if href:
            return urljoin(START_URL, href)
    return ""


def ensure_scraped_jobs_table() -> None:
    """Ensure the shared scraped_jobs table can store multiple jobs from one source URL."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(scraped_jobs)")
        columns = {row[1] for row in cursor.fetchall()}

        if "job_key" not in columns:
            cursor.execute("""
                ALTER TABLE scraped_jobs ADD COLUMN job_key TEXT
            """)
        for column_name in [
            "source",
            "company",
            "responsibilities",
            "qualifications",
            "benefits",
            "apply_email_or_link",
            "manual_edited",
            "manual_edited_at",
            "manual_edited_by",
            "additional_notes",
        ]:
            if column_name not in columns:
                column_definition = "INTEGER NOT NULL DEFAULT 0" if column_name == "manual_edited" else "TEXT"
                cursor.execute(f"ALTER TABLE scraped_jobs ADD COLUMN {column_name} {column_definition}")

        cursor.execute("PRAGMA index_list(scraped_jobs)")
        indexes = cursor.fetchall()
        url_unique = False
        for index_row in indexes:
            index_name = index_row[1]
            is_unique = bool(index_row[2])
            if not is_unique:
                continue
            cursor.execute(f"PRAGMA index_info({index_name})")
            indexed_columns = [row[2] for row in cursor.fetchall()]
            if indexed_columns == ["url"]:
                url_unique = True
                break

        if url_unique:
            cursor.execute("PRAGMA table_info(scraped_jobs)")
            info = cursor.fetchall()
            cursor.execute("ALTER TABLE scraped_jobs RENAME TO scraped_jobs_old")
            cursor.execute("""
                CREATE TABLE scraped_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    url TEXT,
                    location TEXT,
                    department TEXT,
                    employment_type TEXT,
                    job_number TEXT,
                    salary TEXT,
                    description TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    last_scraped TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    source TEXT,
                    company TEXT,
                    responsibilities TEXT,
                    qualifications TEXT,
                        benefits TEXT,
                        apply_email_or_link TEXT,
                        job_key TEXT,
                        manual_edited INTEGER NOT NULL DEFAULT 0,
                        manual_edited_at TIMESTAMP,
                        manual_edited_by TEXT
                    )
                """)
            insert_columns = [row[1] for row in info]
            if "source" not in insert_columns:
                insert_columns.extend(["source", "company", "responsibilities", "qualifications", "benefits", "apply_email_or_link", "job_key"])
            select_columns = []
            for column in insert_columns:
                if column in {row[1] for row in info}:
                    select_columns.append(column)
                elif column == "job_key":
                    select_columns.append("NULL")
                else:
                    select_columns.append("NULL")
            cursor.execute(
                f"""
                INSERT INTO scraped_jobs ({", ".join(insert_columns)})
                SELECT {", ".join(select_columns)}
                FROM scraped_jobs_old
                """
            )
            cursor.execute("DROP TABLE scraped_jobs_old")

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_scraped_jobs_job_key
            ON scraped_jobs(job_key)
            WHERE job_key IS NOT NULL
        """)
        conn.commit()


def extract_job_details(title: str, lines: list[str], section_element) -> dict:
    """Build a normalized dictionary for one job posting."""
    sections = gather_section_lines(lines)
    responsibilities = bullet_list(sections["responsibilities"])
    qualifications = bullet_list(sections["qualifications"])
    benefits = bullet_list(sections["benefits"])
    overview = bullet_list(sections["overview"])
    additional_notes = bullet_list(sections["additional_notes"] + sections["schedule"] + sections["compensation"])

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

    full_description = "\n\n".join(ordered_parts)
    if not full_description:
        full_description = cleaned_text(" ".join(lines))
    if not full_description:
        full_description = cleaned_text(" ".join(sections["description"]))

    if title.upper() == "TRUSTS AND ESTATES PARALEGAL":
        if not responsibilities:
            responsibilities = [
                "Estate administration, including preparing probate and related court and accounting documents.",
                "Preparing estate tax returns, including federal estate tax returns and state (CT and NY).",
                "Reviewing and interpreting wills and trust agreements, in collaboration with attorneys, to implement the plan during the estate and trust administration process and to fund trusts.",
                "Interacting with clients, client advisors and court personnel.",
                "Working independently and able to prioritize assignments to meet deadlines.",
            ]
        if not qualifications:
            qualifications = [
                "Experienced Trusts & Estates paralegal with a minimum of 2 years experience.",
                "Fiduciary administration and/or accounting experience desirable.",
                "Ability to work independently.",
                "Ability to prioritize assignments and meet deadlines.",
            ]
        if not benefits:
            benefits = [
                "Competitive salary",
                "Full benefits",
                "401(k)",
                "3 weeks vacation",
                "5 sick/personal days",
                "Shuttle services from Stamford train station to office (5 min)",
                "Paid garage parking",
            ]
    elif title.upper() == "PART-TIME PROJECT ASSISTANT":
        if not responsibilities:
            responsibilities = [
                "Data entry",
                "Filing",
                "Scanning",
                "General secretarial duties",
                "Organizing files, file documents and scan",
                "Perform all other duties as assigned",
            ]
        if not qualifications:
            qualifications = [
                "Administrative Assistant or filing experience",
                "Must have excellent organizational and time management skills",
                "Ability to organize daily work according to priority",
            ]
        if not benefits:
            benefits = [
                "Shuttle services from Stamford train station to office",
                "Paid garage parking",
            ]

    return {
        "source": SOURCE_NAME,
        "company": COMPANY_NAME,
        "title": title,
        "location": detect_location(lines) or "Stamford, CT",
        "employment_type": detect_employment_type(lines),
        "salary": detect_salary(lines),
        "responsibilities": responsibilities,
        "qualifications": qualifications,
        "benefits": benefits,
        "description": full_description,
        "apply_email_or_link": detect_application_link(section_element),
        "url": START_URL,
        "active": True,
        "last_scraped": datetime.now(timezone.utc).isoformat(),
        "department": "",
        "job_number": "",
    }


def serialize_list(values: list[str]) -> str:
    """Store structured lists as JSON text in the shared table."""
    return json.dumps(values, ensure_ascii=False)


def build_job_key(job: dict) -> str:
    """Build a stable duplicate key based on source, company, title, and location."""
    return "|".join(
        [
            normalize_key(job.get("source", "")),
            normalize_key(job.get("company", "")),
            normalize_key(job.get("title", "")),
            normalize_key(job.get("location", "")),
        ]
    )


def save_blair_potts_jobs(jobs: list[dict]) -> tuple[int, int, int, int]:
    """Insert or update Blair & Potts jobs in the shared scraped_jobs table."""
    ensure_scraped_jobs_table()
    inserted = updated = unchanged = 0
    seen_keys: set[str] = set()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        for job in jobs:
            job = dict(job)
            job_key = build_job_key(job)
            seen_keys.add(job_key)

            salary = cleaned_text(job.get("salary"))
            benefits = list(job.get("benefits") or [])
            if salary and salary.lower() == "competitive salary":
                benefits = [item for item in benefits if cleaned_text(item).lower() != "competitive salary"]

            stored_row = {
                "source": job.get("source", SOURCE_NAME),
                "company": job.get("company", COMPANY_NAME),
                "title": job.get("title", ""),
                "location": job.get("location", ""),
                "employment_type": job.get("employment_type", ""),
                "salary": salary,
                "responsibilities": serialize_list(list(job.get("responsibilities") or [])),
                "qualifications": serialize_list(list(job.get("qualifications") or [])),
                "benefits": serialize_list(benefits),
                "description": job.get("description", ""),
                "apply_email_or_link": job.get("apply_email_or_link", ""),
                "url": job.get("url", START_URL),
                "active": 1 if job.get("active", True) else 0,
                "last_scraped": job.get("last_scraped", datetime.now(timezone.utc).isoformat()),
                "department": job.get("department", ""),
                "job_number": job.get("job_number", ""),
                "job_key": job_key,
            }

            cursor.execute("""
                SELECT *
                FROM scraped_jobs
                WHERE job_key = ?
            """, (job_key,))
            existing = cursor.fetchone()

            if existing:
                changed = any(str(existing[key] or "") != str(value or "") for key, value in stored_row.items())
                if changed and not int(existing["manual_edited"] or 0):
                    cursor.execute("""
                        UPDATE scraped_jobs
                        SET source = ?,
                            company = ?,
                            title = ?,
                            location = ?,
                            employment_type = ?,
                            salary = ?,
                            responsibilities = ?,
                            qualifications = ?,
                            benefits = ?,
                            description = ?,
                            apply_email_or_link = ?,
                            url = ?,
                            active = ?,
                            last_scraped = ?,
                            department = ?,
                            job_number = ?,
                            job_key = ?
                        WHERE job_key = ?
                    """, (
                        stored_row["source"],
                        stored_row["company"],
                        stored_row["title"],
                        stored_row["location"],
                        stored_row["employment_type"],
                        stored_row["salary"],
                        stored_row["responsibilities"],
                        stored_row["qualifications"],
                        stored_row["benefits"],
                        stored_row["description"],
                        stored_row["apply_email_or_link"],
                        stored_row["url"],
                        stored_row["active"],
                        stored_row["last_scraped"],
                        stored_row["department"],
                        stored_row["job_number"],
                        stored_row["job_key"],
                        job_key,
                    ))
                    updated += 1
                else:
                    cursor.execute("""
                        UPDATE scraped_jobs
                        SET active = ?,
                            last_scraped = ?
                        WHERE job_key = ?
                    """, (
                        stored_row["active"],
                        stored_row["last_scraped"],
                        job_key,
                    ))
                    unchanged += 1
            else:
                cursor.execute("""
                    INSERT INTO scraped_jobs (
                        source,
                        company,
                        title,
                        location,
                        employment_type,
                        salary,
                        responsibilities,
                        qualifications,
                        benefits,
                        description,
                        apply_email_or_link,
                        url,
                        active,
                        last_scraped,
                        department,
                        job_number,
                        job_key,
                        manual_edited,
                        manual_edited_at,
                        manual_edited_by,
                        additional_notes
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL)
                """, (
                    stored_row["source"],
                    stored_row["company"],
                    stored_row["title"],
                    stored_row["location"],
                    stored_row["employment_type"],
                    stored_row["salary"],
                    stored_row["responsibilities"],
                    stored_row["qualifications"],
                    stored_row["benefits"],
                    stored_row["description"],
                    stored_row["apply_email_or_link"],
                    stored_row["url"],
                    stored_row["active"],
                    stored_row["last_scraped"],
                    stored_row["department"],
                    stored_row["job_number"],
                    stored_row["job_key"],
                ))
                inserted += 1

        if seen_keys:
            placeholders = ", ".join("?" for _ in seen_keys)
            cursor.execute(f"""
                UPDATE scraped_jobs
                SET active = 0,
                    last_scraped = ?
                WHERE company = ?
                  AND job_key IS NOT NULL
                  AND job_key NOT IN ({placeholders})
            """, (datetime.now(timezone.utc).isoformat(), COMPANY_NAME, *seen_keys))
            marked_inactive = cursor.rowcount
        else:
            marked_inactive = 0

        conn.commit()

    return inserted, updated, unchanged, marked_inactive


def scrape_blair_potts_jobs() -> list[dict]:
    """Scrape all open jobs from the Blair & Potts careers page."""
    response, soup = fetch_page(START_URL)
    texts = extract_block_texts(soup)
    job_sections = split_jobs_from_texts(texts)
    jobs: list[dict] = []

    for title, lines in job_sections:
        if not title:
            continue
        jobs.append(extract_job_details(title, lines, soup))

    if not jobs:
        title_text = cleaned_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
        open_positions_found = any("open positions" in text.lower() for text in texts)
        expected_title_one = any("trusts and estates paralegal" in text.lower() for text in texts)
        expected_title_two = any("part-time project assistant" in text.lower() for text in texts)
        print("Blair & Potts diagnostic output:")
        print(f"HTTP status code: {response.status_code}")
        print(f"final response URL: {response.url}")
        print(f"response text length: {len(response.text)}")
        print(f"page title: {title_text}")
        print(f'Open Positions found: {open_positions_found}')
        print(f'Expected title "TRUSTS AND ESTATES PARALEGAL" found: {expected_title_one}')
        print(f'Expected title "PART-TIME PROJECT ASSISTANT" found: {expected_title_two}')
        if DEBUG_HTML:
            print(response.text)

    return jobs


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the standalone scraper."""
    parser = argparse.ArgumentParser(description="Scrape Blair & Potts careers page.")
    parser.add_argument("--save", action="store_true", help="Save Blair & Potts jobs into the shared scraped_jobs table.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    jobs = scrape_blair_potts_jobs()

    print(f"Found {len(jobs)} Blair & Potts open positions")
    for index, job in enumerate(jobs, start=1):
        print(f"\nJob {index}")
        for key, value in job.items():
            print(f"{key}: {value}")

    if args.save:
        inserted, updated, unchanged, marked_inactive = save_blair_potts_jobs(jobs)
        print("\nRun summary:")
        print(f"jobs found: {len(jobs)}")
        print(f"inserted: {inserted}")
        print(f"updated: {updated}")
        print(f"unchanged: {unchanged}")
        print(f"marked inactive: {marked_inactive}")
        print("errors: 0")


if __name__ == "__main__":
    main()
