import json
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

START_URL = "https://atlanticrecruiters.com/job-postings/"
BASE_URL = "https://atlanticrecruiters.com"
TEST_LIMIT = 5
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


def collect_text_after_label(soup: BeautifulSoup, labels: list[str]) -> str:
    # Atlantic job pages expose some fields as visible labels in the page body.
    for label in labels:
        label_node = soup.find(string=re.compile(rf"^{re.escape(label)}$", re.I))
        if not label_node:
            continue

        parent_text = label_node.parent.get_text(" ", strip=True)
        if ":" in parent_text:
            candidate = parent_text.split(":", 1)[1].strip()
            if candidate and candidate.lower() != label.lower():
                return candidate

        next_text = label_node.parent.find_next(string=True)
        if next_text:
            candidate = cleaned_text(str(next_text))
            if candidate and candidate.lower() != label.lower():
                return candidate

    return ""


def extract_field_from_text(text: str, label_patterns: list[str]) -> str:
    # Search the cleaned page text for lines like "Location: Boston, MA".
    lines = [cleaned_text(line) for line in text.splitlines()]
    for line in lines:
        if not line:
            continue
        for label_pattern in label_patterns:
            match = re.match(label_pattern, line, re.I)
            if match:
                value = match.group(1).strip()
                return value
    return ""


def extract_field(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.I | re.S)
    if not match:
        return ""
    return cleaned_text(match.group(1))


def extract_field_from_block(text: str, label: str, next_label_candidates: list[str]) -> str:
    # Handle cases where the field is followed by another label on the next line.
    pattern = re.compile(rf"{re.escape(label)}\s*:\s*(.+)", re.I)
    for match in pattern.finditer(text):
        value = cleaned_text(match.group(1))
        if not value:
            continue

        lower_value = value.lower()
        for next_label in next_label_candidates:
            next_lower = next_label.lower() + ":"
            if next_lower in lower_value:
                value = cleaned_text(value.split(next_label, 1)[0])
                break
        return value
    return ""


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
                "department": "",
                "job_number": "",
                "salary": "",
            }

    return {
        "location": "",
        "employment_type": "",
        "department": "",
        "job_number": "",
        "salary": "",
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

    if not details["location"]:
        details["location"] = collect_text_after_label(soup, ["Location", "Job Location"])
    if not details["employment_type"]:
        details["employment_type"] = collect_text_after_label(
            soup,
            ["Employment Type", "Job Type", "Type"],
        )
    details["department"] = collect_text_after_label(
        soup,
        ["Department", "Category", "Division"],
    )

    content_root = soup.find("main") or soup.find("article") or soup.body
    description = cleaned_text(content_root.get_text(" ", strip=True)) if content_root else ""
    description_text = content_root.get_text("\n", strip=True) if content_root else ""
    description_preview = description[:500]
    cleaned_job_text = description

    # Debug the exact text the regex sees before we parse it.
    print("DEBUG CLEANED JOB TEXT (first 1000 chars):")
    print(cleaned_job_text[:1000])
    print("END DEBUG")

    # Parse the same cleaned text that we print above.
    location = extract_field(r"Location:\s*(.*?)(?=\s+Type:)", cleaned_job_text)
    employment_type = extract_field(r"Type:\s*(.*?)(?=\s+Job\s+#)", cleaned_job_text)
    job_number = extract_field(r"Job\s+#\s*(\d+)", cleaned_job_text)
    salary = extract_field(r"Salary:\s*(.*?)(?=\s+Job Overview)", cleaned_job_text)

    if not details["location"]:
        details["location"] = location
    if not details["employment_type"]:
        details["employment_type"] = employment_type
    if not details["department"]:
        details["department"] = collect_text_after_label(
            soup,
            ["Department", "Category", "Division"],
        )
    if not job_number:
        job_number = extract_field(r"Job\s+#\s*(\d+)", description_text)
    if not salary:
        salary = extract_field(r"Salary:\s*(.*?)(?=\s+Job Overview)", description_text)

    return {
        "title": title,
        "url": job_url,
        "location": details["location"],
        "department": details["department"],
        "employment_type": details["employment_type"],
        "job_number": job_number,
        "salary": salary,
        "description_preview": description_preview,
    }


def main() -> None:
    listing_soup = get_soup(START_URL)
    job_urls = find_listing_links(listing_soup)
    job_urls = job_urls[:TEST_LIMIT]

    total_jobs = len(job_urls)
    for index, job_url in enumerate(job_urls, start=1):
        print(f"Scraping job {index} of {total_jobs}")
        details = extract_job_details(job_url)
        print("Job Title:", details["title"])
        print("Job URL:", details["url"])
        print("Location:", details["location"] or "Not found")
        print("Department:", details["department"] or "Not found")
        print("Employment Type:", details["employment_type"] or "Not found")
        print("Job Number:", details["job_number"] or "Not found")
        print("Salary:", details["salary"] or "Not found")
        print("Description Preview:", details["description_preview"])
        print("-" * 60)


if __name__ == "__main__":
    main()
