#!/usr/bin/env python3
"""
Resume Parser v9
================

This script parses PDF, DOCX, and DOC resumes into a CSV spreadsheet.
It keeps the v7 workflow, but adds extra guardrails found from real CSV testing:

- Qwen/LM Studio extracts structured resume facts and work_experience rows.
- Python validates the model output before using it.
- Python calculates Total Experience from real dated role ranges.
- Python merges overlapping ranges so the same months are not double-counted.
- Graduation Year is taken only from explicit graduation/degree-date evidence in the Education section, with a strict no-heading degree-block fallback.
- Skills are extracted with a hybrid method: Qwen + section parsing + known-skill scan.
- Employment Status is based on current professional work rows, not global "Present" text.

Usage:
    python parse_resumes_v9.py --input_folder resumes --output_csv resume_results_v9.csv

Requirements:
    pip install openai pypdf python-docx

Before running with Qwen in LM Studio:
    1. Open LM Studio.
    2. Load your Qwen model, for example qwen2.5-coder-3b-instruct.
    3. Go to Developer and make sure the local server is Running.
    4. Confirm the base URL matches --lmstudio_url, usually http://localhost:1234/v1.

Notes:
    - Regex fallback for experience is disabled by default because global date regexes can
      accidentally count education, certifications, projects, or board roles as work.
    - Use --allow_regex_fallback only for troubleshooting or when you accept manual review.
"""

import argparse
import calendar
import csv
import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    from docx import Document
except ImportError:
    Document = None


# ==========================================
# GLOBAL SETTINGS
# ==========================================

DEFAULT_MODEL = "qwen2.5-coder-3b-instruct"
DEFAULT_BASE_URL = "http://localhost:1234/v1"
from .logging_config import get_logger

parser_logger = get_logger("parser")

# The CSV columns are intentionally explicit and ordered. Keeping this list in one
# place prevents the row dictionaries and CSV writer from getting out of sync.
OUTPUT_COLUMNS = [
    "Candidate",
    "Email",
    "Phone Number",
    "Employment Status",
    "Graduation Year",
    "Total Experience (Years)",
    "Career Span (Years)",
    "Skills",
    "Normalized Skills",
    "Current Position",
    "Current Company",
    "Resume Summary",
    "Experience Source",
    "Parsing Notes",
    "Needs Review",
    "Debug Work Experience",
]

UNKNOWN_EMAIL = "Email Not Found"
UNKNOWN_PHONE = "Phone Not Found"
UNKNOWN_DATE = "Date Not Found"
UNKNOWN_SKILLS = "Skills Not Found"
NO_CURRENT_POSITION = "No current position"

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

CURRENT_MARKERS = {"present", "current", "now", "ongoing", "to present"}

# These role terms are counted in Total Experience. They are not used by themselves
# to mark someone as currently employed; employment status still requires a current
# professional role.
NON_PROFESSIONAL_COUNTABLE_TERMS = [
    "intern",
    "internship",
    "fellow",
    "fellowship",
    "volunteer",
    "community service",
    "extern",
    "externship",
    "teaching assistant",
    "research assistant",
    "student assistant",
    "student worker",
    "graduate assistant",
    "resident assistant",
    "clinical rotation",
    "clinical rotations",
    "practicum",
]

# These are strong signals that a row is not a real job/role and should not be
# counted for experience years. The checks are context-sensitive so universities
# can still be counted when the title is a real student/assistant role.
NON_WORK_EXCLUDE_TERMS = [
    "bachelor",
    "bachelors",
    "bachelor's",
    "master",
    "masters",
    "master's",
    "mba",
    "degree",
    "gpa",
    "coursework",
    "certification",
    "certifications",
    "certificate",
    "license",
    "licenses",
    "licensure",
    "skills",
    "core competencies",
    "core expertise",
    "areas of expertise",
    "summary",
    "profile",
    "objective",
    "contact",
    "linkedin",
    "portfolio",
    "accomplishment",
    "accomplishments",
    "award",
    "awards",
    "publication",
    "publications",
]

BOARD_OR_COMMITTEE_TERMS = [
    "board member",
    "board director",
    "advisory board",
    "committee member",
    "committee chair",
    "steering committee",
]

PROJECT_EXCLUDE_TERMS = [
    "academic project",
    "personal project",
    "class project",
    "capstone project",
    "selected project",
    "projects",
]

PROBABLE_JOB_TITLE_TERMS = [
    "accountant", "administrator", "analyst", "associate", "assistant", "auditor",
    "bookkeeper", "clerk", "consultant", "controller", "coordinator", "developer",
    "director", "engineer", "executive", "finance", "lead", "manager", "nurse",
    "officer", "operator", "partner", "programmer", "representative", "specialist",
    "supervisor", "teacher", "technician", "intern", "fellow", "volunteer",
    "cfo", "ceo", "chief", "president", "treasurer", "guide", "nanny",
    "helpline", "social work", "counselor", "therapist",
]

NAME_REJECT_TERMS = [
    "management", "summary", "profile", "objective", "skills", "experience",
    "education", "certification", "certifications", "license", "licenses",
    "expertise", "competencies", "contact", "phone", "email", "linkedin",
    "portfolio", "resume", "curriculum vitae", "professional", "employment",
    "work history", "career", "project", "projects", "accomplishments",
]

NAME_CREDENTIAL_SUFFIXES = [
    "mba", "cpa", "cfa", "phd", "ph.d", "md", "m.d", "jd", "j.d", "ms", "ma",
    "ba", "bs", "bba", "rn", "shrm-cp", "shrm-scp", "pmp",
]

EXPERIENCE_SECTION_HEADERS = [
    "professional experience",
    "work experience",
    "employment experience",
    "employment history",
    "work history",
    "career history",
    "relevant experience",
    "additional experience",
    "selected experience",
    "experience",
    "early career progression",
    "early career",
]

NON_WORK_SECTION_HEADERS = [
    "board & governance experience",
    "board and governance experience",
    "board experience",
    "governance experience",
    "leadership experience",
    "volunteer experience",
    "volunteer work",
    "community service",
    "education",
    "educational background",
    "academic background",
    "education & credentials",
    "education and credentials",
    "certifications",
    "certification",
    "licenses",
    "licensure",
    "projects",
    "selected projects",
    "skills",
    "technical skills",
    "areas of expertise",
    "core competencies",
    "summary",
    "professional summary",
    "profile",
]

# Section headings used by the line-based section extractor. They are deliberately
# broad because resumes use many different names for the same sections.
SKILL_SECTION_HEADERS = [
    "skills",
    "technical skills",
    "computer skills",
    "core competencies",
    "core expertise",
    "areas of expertise",
    "technical proficiencies",
    "tools",
    "technologies",
    "software",
    "systems",
    "platforms",
    "programming languages",
    "skills & abilities",
    "skills and abilities",
    "professional skills",
    "core strengths",
    "certifications and technical skills",
    "technical skills and certifications",
    "technical skills & certifications",
]

# These one-word headings are useful when they appear as standalone section titles,
# but they can also appear inside work-experience bullets, e.g.
# "Systems: led migration...".  v8 allows same-line content for these headings
# only when the remainder looks like a short skill list.
AMBIGUOUS_SAME_LINE_SECTION_HEADERS = {"tools", "technologies", "software", "systems", "platforms"}

EDUCATION_SECTION_HEADERS = [
    "education",
    "educational background",
    "education background",
    "academic background",
    "academic history",
    "academic credentials",
    "university education",
    "college education",
    "education and certifications",
    "education & certifications",
]

SUMMARY_SECTION_HEADERS = [
    "summary",
    "professional summary",
    "executive summary",
    "executive profile",
    "profile",
    "professional profile",
    "objective",
]

MAJOR_SECTION_STOP_HEADERS = [
    "summary",
    "professional summary",
    "executive summary",
    "executive profile",
    "profile",
    "professional profile",
    "objective",
    "experience",
    "work experience",
    "professional experience",
    "employment history",
    "work history",
    "career history",
    "relevant experience",
    "additional experience",
    "leadership experience",
    "volunteer experience",
    "internship experience",
    "education",
    "educational background",
    "academic background",
    "certifications",
    "certification",
    "licenses",
    "licensure",
    "projects",
    "selected projects",
    "awards",
    "honors",
    "languages",
    "community service",
    "volunteer work",
    "activities",
    "publications",
    "references",
    "skills",
    "technical skills",
    "computer skills",
    "core competencies",
    "core expertise",
    "areas of expertise",
    "technical proficiencies",
    "tools",
    "technologies",
    "software",
    "systems",
    "platforms",
    "programming languages",
    "skills & abilities",
    "skills and abilities",
    "professional skills",
    "core strengths",
    "certifications and technical skills",
    "technical skills and certifications",
    "technical skills & certifications",
]

DEGREE_WORDS = [
    "bachelor", "bachelors", "bachelor's", "b.s", "bs", "ba", "bba",
    "master", "masters", "master's", "m.s", "ms", "mba", "ma",
    "associate", "associates", "associate's", "associate of arts",
    "associate of science", "a.s", "aa",
    "doctor", "doctorate", "phd", "ph.d", "jd", "j.d",
    "degree", "diploma", "major", "minor",
]

SCHOOL_WORDS = [
    "university", "college", "school of", "institute", "academy", "polytechnic",
    "community college", "state university", "business school", "law school",
]

GRADUATION_CONTEXT_WORDS = [
    "graduated", "graduation", "expected", "class of", "conferred", "completed",
]

# A known-skill dictionary helps recall when the Skills section is missing or the
# LLM returns a sparse list. Add/remove skills here based on the resumes you parse.
KNOWN_SKILL_ALIASES: Dict[str, List[str]] = {
    # Office and productivity tools.
    "Microsoft Office": ["microsoft office", "ms office", "office suite"],
    "Microsoft Excel": ["microsoft excel", "ms excel", "excel"],
    "Microsoft Word": ["microsoft word", "ms word"],
    "Microsoft PowerPoint": ["microsoft powerpoint", "ms powerpoint", "powerpoint"],
    "Microsoft Outlook": ["microsoft outlook", "ms outlook", "outlook"],
    "Microsoft Teams": ["microsoft teams", "ms teams"],
    "SharePoint": ["sharepoint", "microsoft sharepoint"],
    "Google Workspace": ["google workspace", "google suite", "g suite"],
    "Google Sheets": ["google sheets"],
    "Google Docs": ["google docs"],

    # Data, analytics, and programming.
    "Power BI": ["power bi", "powerbi"],
    "Tableau": ["tableau"],
    "SQL": ["sql", "structured query language"],
    "Python": ["python"],
    "R": ["r programming", " r "],
    "Java": ["java"],
    "JavaScript": ["javascript", "java script", "js"],
    "HTML": ["html"],
    "CSS": ["css"],
    "C++": ["c++"],
    "C#": ["c#", "c sharp"],
    ".NET": [".net", "dotnet", "dot net"],
    "VBA": ["vba", "visual basic for applications"],
    "Power Query": ["power query"],
    "Power Pivot": ["power pivot"],
    "DAX": ["dax"],
    "Data Analysis": ["data analysis", "data analytics"],
    "Data Visualization": ["data visualization", "data visualisation"],
    "Pivot Tables": ["pivot tables", "pivot table"],
    "VLOOKUP": ["vlookup", "v-lookups", "v lookup"],
    "XLOOKUP": ["xlookup", "x lookup"],

    # Business systems and platforms.
    "QuickBooks": ["quickbooks", "quick books"],
    "SAP": ["sap"],
    "Oracle": ["oracle"],
    "NetSuite": ["netsuite", "net suite"],
    "Workday": ["workday"],
    "ADP": ["adp"],
    "PeopleSoft": ["peoplesoft", "people soft"],
    "Salesforce": ["salesforce", "sales force"],
    "HubSpot": ["hubspot", "hub spot"],
    "Jira": ["jira"],
    "Confluence": ["confluence"],
    "Asana": ["asana"],
    "Trello": ["trello"],
    "Slack": ["slack"],
    "CRM": ["crm", "customer relationship management"],
    "ERP": ["erp", "enterprise resource planning"],

    # Accounting and finance skills.
    "GAAP": ["gaap", "generally accepted accounting principles"],
    "IFRS": ["ifrs", "international financial reporting standards"],
    "Accounts Payable": ["accounts payable", "account payable", "ap processing", "a/p"],
    "Accounts Receivable": ["accounts receivable", "account receivable", "ar processing", "a/r"],
    "Payroll": ["payroll"],
    "General Ledger": ["general ledger", "gl accounting", "g/l"],
    "Month-End Close": ["month-end close", "month end close", "monthly close"],
    "Bank Reconciliation": ["bank reconciliation", "bank reconciliations"],
    "Account Reconciliation": ["account reconciliation", "account reconciliations", "reconciliations"],
    "Financial Reporting": ["financial reporting"],
    "Financial Analysis": ["financial analysis"],
    "Budgeting": ["budgeting", "budgets"],
    "Forecasting": ["forecasting", "forecasts"],
    "Variance Analysis": ["variance analysis"],
    "Tax Preparation": ["tax preparation", "tax prep"],
    "Audit": ["audit", "auditing"],
    "Invoicing": ["invoicing", "invoice processing"],
    "Bookkeeping": ["bookkeeping"],
    "Cost Accounting": ["cost accounting"],

    # Operations, admin, HR, and general business skills.
    "Project Management": ["project management"],
    "Process Improvement": ["process improvement"],
    "Customer Service": ["customer service", "client service", "client services"],
    "Data Entry": ["data entry"],
    "Scheduling": ["scheduling"],
    "Inventory Management": ["inventory management"],
    "Onboarding": ["onboarding", "employee onboarding"],
    "Recruiting": ["recruiting", "recruitment"],
    "Applicant Tracking Systems": ["applicant tracking systems", "ats"],
    "HIPAA": ["hipaa"],
    "Bilingual": ["bilingual"],
    "Spanish": ["spanish"],
}


# ==========================================
# DATA MODEL
# ==========================================

@dataclass
class JobEntry:
    """A single validated job/role row used by Python for experience math."""

    title: str
    company: str
    start_date: date
    end_date: date
    raw_line: str
    is_current: bool
    experience_type: str
    source: str = "qwen"

    @property
    def is_professional(self) -> bool:
        """Professional roles can determine Employment Status when current."""
        return self.experience_type == "professional"

    @property
    def is_countable(self) -> bool:
        """Both professional and non-professional roles count toward Total Experience."""
        return self.experience_type in {"professional", "internship_fellowship_volunteer"}


# ==========================================
# FILE READERS
# ==========================================

# These functions are intentionally simple wrappers around common file readers.
# They return plain text, and all parsing happens later after clean_text().

def read_pdf(path: Path) -> str:
    """Extract text from a PDF using pypdf."""
    if PdfReader is None:
        raise ImportError("pypdf is missing. Install it with: pip install pypdf")

    reader = PdfReader(str(path))
    text_parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            text_parts.append(text)
    return "\n".join(text_parts)


def read_docx(path: Path) -> str:
    """Extract paragraph text from a DOCX using python-docx."""
    if Document is None:
        raise ImportError("python-docx is missing. Install it with: pip install python-docx")

    doc = Document(path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def read_doc(path: Path) -> str:
    """Convert old .doc files to .docx with LibreOffice, then read them."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        result = subprocess.run(
            [
                "libreoffice",
                "--headless",
                "--convert-to",
                "docx",
                "--outdir",
                str(tmpdir_path),
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        converted = tmpdir_path / f"{path.stem}.docx"
        if result.returncode == 0 and converted.exists():
            return read_docx(converted)

    raise RuntimeError(
        f"Could not read old .doc file: {path.name}. Save it as .docx or install LibreOffice."
    )


def read_resume_text(path: Path) -> str:
    """Pick the correct reader based on the file extension."""
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return read_pdf(path)
    if suffix == ".docx":
        return read_docx(path)
    if suffix == ".doc":
        return read_doc(path)

    raise ValueError(f"Unsupported file type: {path.name}")


def clean_text(text: str) -> str:
    """Normalize common PDF/DOC extraction artifacts before parsing."""
    text = text.replace("\u2022", " ")
    text = text.replace("\uf0b7", " ")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u2212", "-")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ==========================================
# BASIC CONTACT / NAME EXTRACTORS
# ==========================================

# These do not need Qwen. Regex is better for email/phone because it is deterministic.

def normalize_candidate_name(name: str) -> str:
    """Clean credentials and punctuation around a candidate name candidate."""
    name = str(name or "").strip()
    name = re.sub(r"\s+", " ", name)
    name = name.strip(" -|,:;")

    credential_pattern = "|".join(re.escape(value) for value in NAME_CREDENTIAL_SUFFIXES)
    name = re.sub(rf"\s*,?\s*\b(?:{credential_pattern})\b\.?$", "", name, flags=re.IGNORECASE)

    if "," in name:
        parts = [part.strip() for part in name.split(",") if part.strip()]
        if len(parts) == 2 and all(re.search(r"[A-Za-z]", part) for part in parts):
            # Convert "Last, First" headers into the usual display order.
            name = f"{parts[1]} {parts[0]}"

    name = re.sub(rf"\s*,?\s*\b(?:{credential_pattern})\b\.?$", "", name, flags=re.IGNORECASE)
    return name.strip(" -|,:;")


def looks_like_person_name(value: str) -> bool:
    """Reject section headings, skill words, contact lines, and non-name fragments."""
    value = normalize_candidate_name(value)
    lower = value.lower()
    words = value.split()

    if not value or len(words) < 2 or len(words) > 5:
        return False
    if any(term in lower for term in NAME_REJECT_TERMS):
        return False
    if "@" in value or re.search(r"\d", value):
        return False
    if re.search(r"[|/\\]", value):
        return False

    alpha_words = [word for word in words if re.search(r"[A-Za-z]", word)]
    if len(alpha_words) < 2:
        return False

    # Names are usually made of letters, apostrophes, periods, and hyphens. This
    # deliberately filters out extracted skill/header fragments such as "Areas of".
    return all(re.match(r"^[A-Za-z][A-Za-z'.-]*$", word) for word in alpha_words)


def extract_candidate_name(text: str, file_path: Path) -> str:
    """Prefer the real person name in the resume header over generic text."""
    for line in text.splitlines()[:30]:
        line = normalize_candidate_name(line)
        if not line:
            continue
        if looks_like_person_name(line):
            return line

    filename_name = file_path.stem.replace("_", " ").replace("-", " ")
    filename_name = re.sub(r"\(\d+\)", "", filename_name)
    filename_name = normalize_candidate_name(filename_name.title())
    if looks_like_person_name(filename_name):
        return filename_name

    return "Candidate Name Unknown"


def choose_candidate_name(qwen_name: str, text: str, file_path: Path, notes: List[str]) -> str:
    """Use the model name only when it survives the same person-name checks."""
    cleaned_qwen_name = normalize_candidate_name(qwen_name)
    if looks_like_person_name(cleaned_qwen_name):
        return cleaned_qwen_name

    if cleaned_qwen_name:
        notes.append(f"Ignored candidate_name that did not look like a person name: {cleaned_qwen_name}")

    return extract_candidate_name(text, file_path)


def extract_email(text: str) -> str:
    """Return the first email address found, or the standard unknown value."""
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return match.group(0) if match else UNKNOWN_EMAIL


def extract_phone(text: str) -> str:
    """Return the first US-style phone number found, or the standard unknown value."""
    phone_pattern = r"(?:\+1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}"
    match = re.search(phone_pattern, text)

    if not match:
        return UNKNOWN_PHONE

    phone = match.group(0).strip()
    phone = re.sub(r"^\+1[\s.-]?", "", phone)
    return phone


# ==========================================
# SECTION EXTRACTION HELPERS
# ==========================================

# Graduation and skills extraction are safer when they are section-aware. These
# helpers isolate a named section and stop at the next major resume heading.

def normalize_heading_text(line: str) -> str:
    """Clean a possible section heading for easier matching."""
    line = re.sub(r"^[\s\-_*|:]+", "", line.strip())
    line = re.sub(r"[\s\-_*|:]+$", "", line)
    line = re.sub(r"\s+", " ", line)
    return line.lower()


def heading_to_pattern(label: str) -> str:
    """Convert a human-readable section label to a regex fragment."""
    escaped = re.escape(label.lower())
    escaped = escaped.replace(r"\ ", r"\s+")
    escaped = escaped.replace(r"\&", r"(?:&|and)")
    return escaped


def same_line_remainder_looks_like_skill_list(remainder: str) -> bool:
    """
    Decide whether text after an ambiguous same-line heading looks like skills.

    This prevents work bullets such as "Systems: led migration work..." from
    being mistaken for a Skills section just because "Systems" is one of the
    requested skill-section labels.
    """
    remainder = str(remainder or "").strip()
    lower = remainder.lower()

    if not remainder:
        return False

    # Long prose after a one-word label is usually a work bullet, not a skills list.
    if len(remainder) > 180 or len(remainder.split()) > 24:
        return False

    prose_signals = [
        "responsible for", "identify and communicate", "senior leadership",
        "stakeholder", "stakeholders", "project planning", "walkthrough",
        "translate", "translated", "supporting", "developed a", "created a",
        "managed", "led ", "lead ", "drive ", "drove ", "participated",
    ]
    if any(signal in lower for signal in prose_signals):
        return False

    # A same-line skills heading generally contains a list separator or one/two
    # known tools/skills.  A short single item is allowed because some resumes use
    # headings like "Software: QuickBooks".
    if any(separator in remainder for separator in [",", ";", "|", "/", "•", "·"]):
        return True

    known_tokens = [
        "excel", "sql", "python", "power bi", "tableau", "quickbooks", "sap",
        "oracle", "netsuite", "workday", "salesforce", "jira", "confluence",
        "servicenow", "linux", "azure", "aws", "github", "git", "powerpoint",
    ]
    return any(token in lower for token in known_tokens) or len(remainder.split()) <= 4


def match_section_heading(line: str, labels: Sequence[str]) -> Tuple[bool, str, str]:
    """
    Check whether a line is a section heading or a heading with same-line content.

    Returns:
        (matched, matched_label, same_line_remainder)

    Example:
        "Skills: Excel, SQL" -> (True, "skills", "Excel, SQL")

    The matcher intentionally does not treat lines like "Experience with SQL" as
    headings, because those can be normal skill bullets. A same-line section must
    use a colon or dash after the heading.
    """
    stripped = line.strip()
    if not stripped:
        return False, "", ""

    # Remove common bullets or decorative prefixes before matching headings.
    clean = re.sub(r"^[\s\-_*|]+", "", stripped)
    clean = re.sub(r"\s+", " ", clean).strip()

    for label in sorted(labels, key=len, reverse=True):
        pattern = heading_to_pattern(label)

        # Exact heading, such as "Education" or "Technical Skills".
        if re.match(rf"(?i)^({pattern})\s*$", clean):
            return True, label, ""

        # Heading with same-line content, such as "Skills: Excel, SQL".
        match = re.match(rf"(?i)^({pattern})\b\s*[:\-]\s*(.*)$", clean)
        if match:
            remainder = match.group(2).strip()
            if (
                label.lower() in AMBIGUOUS_SAME_LINE_SECTION_HEADERS
                and not same_line_remainder_looks_like_skill_list(remainder)
            ):
                continue
            return True, label, remainder

    return False, "", ""


def looks_like_stop_heading(line: str, stop_labels: Sequence[str]) -> bool:
    """Return True when a line appears to be the next major resume section."""
    matched, _label, _remainder = match_section_heading(line, stop_labels)
    return matched


def extract_section(
    text: str,
    start_labels: Sequence[str],
    stop_labels: Sequence[str],
) -> Tuple[str, bool]:
    """
    Extract a section using resume headings.

    The function is line-based because PDF/DOC extraction often breaks formatting.
    It supports same-line content such as "Skills: Excel, SQL".
    """
    lines = text.splitlines()

    for start_index, line in enumerate(lines):
        matched, _label, same_line_remainder = match_section_heading(line, start_labels)
        if not matched:
            continue

        section_lines: List[str] = []
        if same_line_remainder:
            section_lines.append(same_line_remainder)

        for next_line in lines[start_index + 1:]:
            if looks_like_stop_heading(next_line, stop_labels):
                break
            section_lines.append(next_line)

        section_text = "\n".join(section_lines).strip()
        return section_text, bool(section_text)

    return "", False


def compact_section_text(section_text: str) -> str:
    """Flatten a multi-line section so it fits cleanly in one CSV cell."""
    section_text = re.sub(r"[\t\r]+", " ", section_text)
    section_text = re.sub(r"\s*\n\s*", "; ", section_text)
    section_text = re.sub(r"\s+", " ", section_text)
    return section_text.strip(" ;:-")


# ==========================================
# EDUCATION / GRADUATION YEAR EXTRACTION
# ==========================================

# This is the main change for graduation year. It never searches the full resume.
# It first isolates Education, then looks near school or degree context only.

def contains_any_term(text: str, terms: Iterable[str]) -> bool:
    """Return True when any term appears as a real word or phrase.

    This prevents false matches such as the degree abbreviation "as" matching
    "assistant", or "ms" matching part of another word.
    """
    lower = str(text or "").lower()
    for term in terms:
        term = str(term or "").strip().lower()
        if not term:
            continue
        escaped = re.escape(term).replace(r"\ ", r"\s+")
        pattern = rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
        if re.search(pattern, lower):
            return True
    return False


def valid_year(year_text: str, today: date) -> Optional[int]:
    """Validate years so random numbers do not become graduation years."""
    if not re.fullmatch(r"(?:19|20)\d{2}", str(year_text or "")):
        return None

    year = int(year_text)
    # Allow expected graduation dates several years in the future.
    if 1950 <= year <= today.year + 8:
        return year
    return None


def years_in_text(text: str, today: date) -> List[int]:
    """Find valid four-digit years in a small education context window."""
    years: List[int] = []
    for match in re.finditer(r"\b((?:19|20)\d{2})\b", text):
        year = valid_year(match.group(1), today)
        if year is not None:
            years.append(year)
    return years


def end_years_from_date_ranges(text: str, today: date) -> List[int]:
    """
    Pull the ending year from education date ranges.

    Examples:
        "2018 - 2022" -> 2022
        "September 2019 - May 2023" -> 2023
    """
    range_patterns = [
        r"\b((?:19|20)\d{2})\b\s*(?:-|to|through)\s*\b((?:19|20)\d{2})\b",
        r"\b(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\.?\s+((?:19|20)\d{2})\b\s*(?:-|to|through)\s*\b(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\.?\s+((?:19|20)\d{2})\b",
        r"\b((?:19|20)\d{2})\b\s*(?:-|to|through)\s*\b(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\.?\s+((?:19|20)\d{2})\b",
    ]

    found: List[int] = []
    for pattern in range_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            end_year = valid_year(match.group(2), today)
            if end_year is not None:
                found.append(end_year)
    return found


def graduation_hint_years(text: str, today: date) -> List[int]:
    """Find years tied to explicit graduation hints like Expected or Class of."""
    patterns = [
        r"\b(?:expected|anticipated)\s+(?:graduation|completion|degree)?\s*:?,?\s*(?:[A-Za-z]+\s+)?((?:19|20)\d{2})\b",
        r"\b(?:graduated|graduation|conferred|completed|completion)\s*:?,?\s*(?:[A-Za-z]+\s+)?((?:19|20)\d{2})\b",
        r"\bclass\s+of\s+((?:19|20)\d{2})\b",
        r"\b(?:expected|anticipated)\s+(?:[A-Za-z]+\s+)?((?:19|20)\d{2})\b",
    ]

    found: List[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            year = valid_year(match.group(1), today)
            if year is not None:
                found.append(year)
    return found


def degree_context_single_years(text: str, today: date) -> List[int]:
    """
    Return single years only when they are in a degree/completion context.

    v8 allowed any year in a school-looking Education window. That helped recall,
    but caused false positives on resumes where a university/program/training line
    had a year that was not a graduation year. v9 requires either degree wording or
    explicit graduation wording before accepting a single year.
    """
    if not (
        contains_any_term(text, DEGREE_WORDS)
        or contains_any_term(text, GRADUATION_CONTEXT_WORDS)
    ):
        return []

    # Do not treat certificate/license/training dates as graduation years.
    if contains_any_term(text, ["certification", "certificate", "license", "licensure", "course", "coursework", "training"]):
        return []

    return years_in_text(text, today)


def education_context_is_graduation_evidence(text: str) -> bool:
    """
    Decide whether a small education text block is allowed to produce a graduation year.

    Required evidence:
    - explicit graduation wording, OR
    - a degree word such as Bachelor/Master/MBA/BS/BA, OR
    - a date range on the same block as a degree word.

    School words alone are not enough anymore. This is the fix for rows where the
    parser saw a school/program date and incorrectly treated it as a graduation year.
    """
    lower = str(text or "").lower()
    if contains_any_term(lower, GRADUATION_CONTEXT_WORDS):
        return True
    if contains_any_term(lower, DEGREE_WORDS):
        return True
    return False


def block_looks_like_certification_or_work(text: str) -> bool:
    """Reject blocks that are probably certifications, projects, or job rows."""
    lower = str(text or "").lower()

    certification_terms = [
        "certification", "certifications", "certificate", "license", "licenses",
        "licensure", "credential", "credentials", "coursework", "training",
    ]
    if contains_any_term(lower, certification_terms):
        return True

    # A no-heading fallback block containing role terms and no degree is probably a
    # job row, not education. This avoids using Teaching Assistant date ranges at a
    # university as graduation years.
    role_terms = [
        "intern", "analyst", "assistant", "manager", "director", "controller",
        "partner", "auditor", "teacher", "consultant", "specialist", "coordinator",
    ]
    if contains_any_term(lower, role_terms) and not contains_any_term(lower, DEGREE_WORDS):
        return True

    return False


def build_graduation_context_windows(education_text: str) -> List[str]:
    """Build small windows around degree/school/graduation lines inside Education."""
    lines = [line.strip() for line in education_text.splitlines() if line.strip()]
    windows: List[str] = []

    for index, line in enumerate(lines):
        lower_line = line.lower()
        has_context_line = (
            contains_any_term(lower_line, DEGREE_WORDS)
            or contains_any_term(lower_line, GRADUATION_CONTEXT_WORDS)
            or contains_any_term(lower_line, SCHOOL_WORDS)
        )
        if not has_context_line:
            continue

        start = max(0, index - 1)
        end = min(len(lines), index + 2)
        window = " ".join(lines[start:end]).strip()
        if window:
            windows.append(window)

    # If the section is compact, also evaluate the whole section. This helps PDF
    # extracts that put school, degree, and date on one or two wrapped lines.
    if len(" ".join(lines)) <= 600:
        windows.append(" ".join(lines))

    return list(dict.fromkeys(windows))


def find_degree_blocks_without_education_heading(text: str) -> List[str]:
    """
    Strict fallback for resumes that do not have an obvious Education heading.

    It searches for degree/graduation lines anywhere in the resume, but only uses
    tiny windows that contain strong education evidence. This is intended to catch
    cases like Jonathon Glatzer where the degree year exists but the heading was
    missed, without going back to unsafe full-resume year matching.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    blocks: List[str] = []

    for index, line in enumerate(lines):
        lower_line = line.lower()
        if not (
            contains_any_term(lower_line, DEGREE_WORDS)
            or contains_any_term(lower_line, GRADUATION_CONTEXT_WORDS)
        ):
            continue

        # Use a tight window. Including too many lines after the degree can pull in
        # the next Experience section and make a job year look like a graduation year.
        start = max(0, index - 1)
        end = min(len(lines), index + 2)
        block = " ".join(lines[start:end]).strip()
        lower_block = block.lower()

        if block_looks_like_certification_or_work(lower_block):
            continue

        # For no-heading fallback, require a school word OR explicit graduation
        # language. Degree-only headlines without a school are too easy to confuse
        # with summaries.
        if not (
            contains_any_term(lower_block, SCHOOL_WORDS)
            or contains_any_term(lower_block, GRADUATION_CONTEXT_WORDS)
        ):
            continue

        blocks.append(block)

    return list(dict.fromkeys(blocks))


def graduation_years_from_windows(windows: Sequence[str], today: date) -> List[int]:
    """Extract allowed graduation-year candidates from context windows."""
    candidates: List[int] = []

    for window in windows:
        if not window or block_looks_like_certification_or_work(window):
            continue
        if not education_context_is_graduation_evidence(window):
            continue

        # Priority 1: explicit graduation language.
        candidates.extend(graduation_hint_years(window, today))

        # Priority 2: degree/program date range. End year is accepted only because
        # the same small window contains degree/graduation evidence.
        candidates.extend(end_years_from_date_ranges(window, today))

        # Priority 3: a single date on a degree/graduation line, such as
        # "Bachelor of Arts in Economics, May 2024".
        candidates.extend(degree_context_single_years(window, today))

    return candidates


def extract_graduation_year_from_education(text: str, today: date) -> Tuple[str, List[str]]:
    """
    Return a graduation year using explicit education evidence only.

    v9 fixes two failure modes found in your CSV review:
    - False negative: when an Education heading is missed but a degree/year block exists.
    - False positive: when a school/program/training year exists but no graduation year is stated.
    """
    notes: List[str] = []
    education_section, found_section = extract_section(
        text,
        EDUCATION_SECTION_HEADERS,
        [header for header in MAJOR_SECTION_STOP_HEADERS if header not in EDUCATION_SECTION_HEADERS],
    )

    windows: List[str] = []
    if found_section and education_section.strip():
        windows = build_graduation_context_windows(education_section)
    else:
        notes.append("Education section heading not found; checked strict degree-block fallback")
        windows = find_degree_blocks_without_education_heading(text)

    candidate_years = graduation_years_from_windows(windows, today)

    if not candidate_years:
        if found_section:
            notes.append("No explicit graduation year found inside Education section")
        else:
            notes.append("No explicit graduation year found in strict degree-block fallback")
        return UNKNOWN_DATE, notes

    # Use the latest evidence-backed education year. This handles multiple degrees
    # by choosing the most recent completed/expected degree.
    return str(max(candidate_years)), notes


def get_final_graduation_year(qwen_data: dict, text: str, today: date) -> Tuple[str, List[str]]:
    """
    Python education evidence is the source of truth for Graduation Year.

    Qwen is intentionally not allowed to supply a graduation year unless Python
    can independently find explicit degree/graduation evidence in the resume text.
    This prevents hallucinated or inferred years like the Dariush/Nargess cases.
    """
    python_year, notes = extract_graduation_year_from_education(text, today)

    qwen_year_text = str(qwen_data.get("graduation_year", "")).strip()
    qwen_year = valid_year(qwen_year_text, today)

    if python_year != UNKNOWN_DATE:
        if qwen_year is not None and str(qwen_year) != python_year:
            notes.append(f"Qwen graduation_year {qwen_year} disagreed with Python-verified year {python_year}; used Python-verified year")
        return python_year, notes

    if qwen_year is not None:
        notes.append(f"Ignored Qwen graduation_year {qwen_year} because it was not verified by explicit Education/degree evidence")

    return UNKNOWN_DATE, notes


# ==========================================
# SUMMARY EXTRACTION
# ==========================================

def extract_resume_summary(text: str, qwen_data: dict) -> str:
    """Prefer the LLM summary, then fall back to a real Summary/Profile section."""
    qwen_summary = str(qwen_data.get("resume_summary", "")).strip()
    if qwen_summary:
        return re.sub(r"\s+", " ", qwen_summary)

    summary_section, found_section = extract_section(
        text,
        SUMMARY_SECTION_HEADERS,
        [header for header in MAJOR_SECTION_STOP_HEADERS if header not in SUMMARY_SECTION_HEADERS],
    )
    if not found_section:
        return ""

    summary = compact_section_text(summary_section)
    return summary[:900].strip()


# ==========================================
# SKILL EXTRACTION
# ==========================================

# Skills are extracted three ways:
#   1. Qwen normalized_skills
#   2. Raw Skills section parsing
#   3. Known-skill scan over the resume text
# The final list is deduplicated case-insensitively.

def looks_like_wrong_skills_section(skills_section: str) -> bool:
    """
    Reject sections that were probably captured from Experience bullets instead
    of a real Skills section.  This is a safety valve for PDF line breaks and
    ambiguous headings like "Systems:".
    """
    raw = str(skills_section or "")
    compact = compact_section_text(raw)
    lower = compact.lower()

    if not compact:
        return True

    prose_signals = [
        "identify and communicate", "senior leadership", "technology and operational risks",
        "lead project planning", "stakeholder decision", "walkthroughs",
        "system architecture", "business processes", "participated in",
        "prototype tool", "responsible for", "managed a team", "developed a",
        "created a", "translated technical",
    ]

    if any(signal in lower for signal in prose_signals):
        return True

    # A very long bullet-heavy capture is usually not a skills list.
    bullet_count = raw.count("•") + raw.count("●") + raw.count("-")
    if len(compact) > 900 and bullet_count >= 2:
        return True

    # Long prose with many sentence-style verbs is likely a job description.
    prose_verbs = re.findall(
        r"\b(?:identify|communicate|lead|led|support|supporting|drive|drove|translate|"
        r"translated|develop|developed|create|created|manage|managed|facilitate|"
        r"facilitated|participate|participated)\b",
        lower,
    )
    if len(compact) > 500 and len(prose_verbs) >= 4:
        return True

    return False


def extract_skills_section(text: str) -> Tuple[str, bool]:
    """Return the raw Skills section as a one-line CSV value."""
    skills_section, found = extract_section(
        text,
        SKILL_SECTION_HEADERS,
        [header for header in MAJOR_SECTION_STOP_HEADERS if header not in SKILL_SECTION_HEADERS],
    )

    if not found or looks_like_wrong_skills_section(skills_section):
        return UNKNOWN_SKILLS, False

    compact = compact_section_text(skills_section)
    return compact if compact else UNKNOWN_SKILLS, bool(compact)


def normalize_skill_output_to_list(value: Any) -> List[str]:
    """Convert Qwen's normalized_skills value into a Python list."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    if isinstance(value, str):
        # Qwen sometimes returns a comma-separated string instead of a list.
        parts = re.split(r"[,;|\n]+", value)
        return [part.strip() for part in parts if part.strip()]

    return []


def alias_boundary_pattern(alias: str) -> str:
    """Build a safe case-insensitive search pattern for a known skill alias."""
    alias = alias.strip()
    escaped = re.escape(alias)
    escaped = escaped.replace(r"\ ", r"\s+")

    # Do not let "excel" match "excellent". The negative lookarounds are broader
    # than \b so they also work around symbols like +, #, and periods.
    return rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"


def build_alias_lookup() -> Dict[str, str]:
    """Create exact lowercase alias -> canonical skill map."""
    lookup: Dict[str, str] = {}
    for canonical, aliases in KNOWN_SKILL_ALIASES.items():
        lookup[canonical.lower()] = canonical
        for alias in aliases:
            lookup[alias.lower().strip()] = canonical
    return lookup


ALIAS_LOOKUP = build_alias_lookup()


def canonicalize_skill(candidate: str) -> str:
    """Clean and canonicalize a single skill candidate."""
    candidate = str(candidate or "").strip()
    candidate = re.sub(r"^[\s\-_*|:]+", "", candidate)
    candidate = re.sub(r"[\s\-_*|:.]+$", "", candidate)
    candidate = re.sub(r"\s+", " ", candidate)

    # Convert "Software: Excel" -> "Excel" before alias lookup.
    if ":" in candidate:
        left, right = candidate.split(":", 1)
        if len(left.split()) <= 4 and right.strip():
            candidate = right.strip()

    lower = candidate.lower().strip()
    return ALIAS_LOOKUP.get(lower, candidate)


def looks_like_bad_skill_candidate(candidate: str) -> bool:
    """Filter out sentences, headers, dates, and obvious non-skill fragments."""
    candidate = str(candidate or "").strip()
    lower = candidate.lower()

    if not candidate:
        return True
    if candidate in {UNKNOWN_SKILLS, UNKNOWN_DATE, UNKNOWN_EMAIL, UNKNOWN_PHONE}:
        return True
    if "@" in candidate:
        return True
    if re.fullmatch(r"(?:19|20)\d{2}", candidate):
        return True
    if len(candidate) > 80 or len(candidate.split()) > 10:
        return True
    if re.search(r"\b(responsible for|managed to|worked with|provided|created|developed|led daily)\b", lower):
        return True
    if lower in {"skills", "technical skills", "tools", "software", "systems", "platforms"}:
        return True

    return False


def split_skill_section_into_candidates(skills_section: str) -> List[str]:
    """Split raw Skills text into candidate skill phrases."""
    if not skills_section or skills_section == UNKNOWN_SKILLS:
        return []

    # Split common separators while leaving multi-word skills intact.
    text = skills_section.replace("/", " / ")
    raw_parts = re.split(r"[,;|\n]+|\s{2,}", text)

    expanded_parts: List[str] = []
    for part in raw_parts:
        part = part.strip()
        if not part:
            continue

        # Split simple slash-separated lists such as "Excel / SQL / Tableau".
        if " / " in part:
            slash_parts = [p.strip() for p in part.split(" / ") if p.strip()]
            if 1 < len(slash_parts) <= 8:
                expanded_parts.extend(slash_parts)
                continue

        expanded_parts.append(part)

    candidates: List[str] = []
    for part in expanded_parts:
        skill = canonicalize_skill(part)
        if not looks_like_bad_skill_candidate(skill):
            candidates.append(skill)
    return candidates


def scan_known_skills(text: str) -> List[str]:
    """Scan text for known skills and return canonical names in dictionary order."""
    if not text:
        return []

    found: List[str] = []
    for canonical, aliases in KNOWN_SKILL_ALIASES.items():
        # One-letter canonical names, especially "R", are too broad as direct
        # full-text searches. Use their explicit aliases instead.
        search_terms = list(aliases)
        if len(canonical) > 1:
            search_terms.insert(0, canonical)

        for alias in search_terms:
            pattern = alias_boundary_pattern(alias)
            if re.search(pattern, text, flags=re.IGNORECASE):
                found.append(canonical)
                break
    return found


def dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    """Deduplicate values case-insensitively while preserving first-seen order."""
    seen = set()
    result: List[str] = []

    for value in values:
        cleaned = canonicalize_skill(value)
        if looks_like_bad_skill_candidate(cleaned):
            continue

        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            result.append(cleaned)

    return result


def extract_combined_skills(text: str, qwen_data: dict) -> Tuple[str, str, List[str]]:
    """Return raw skills, normalized skills, and skill-related review notes."""
    notes: List[str] = []
    raw_skills, found_skills_section = extract_skills_section(text)

    qwen_skills = normalize_skill_output_to_list(qwen_data.get("normalized_skills", []))
    section_skills = split_skill_section_into_candidates(raw_skills) if found_skills_section else []

    # Scan the Skills section first, then the full resume. The full-resume scan
    # helps when the resume has no clear Skills heading.
    known_from_section = scan_known_skills(raw_skills) if found_skills_section else []
    known_from_full_text = scan_known_skills(text)

    normalized = dedupe_preserve_order(
        list(qwen_skills) + section_skills + known_from_section + known_from_full_text
    )

    if not found_skills_section and known_from_full_text:
        notes.append("No Skills section found, but known skills were found elsewhere in resume")

    if found_skills_section and not normalized:
        notes.append("Skills section found, but normalized skills list was empty")

    normalized_text = ", ".join(normalized) if normalized else UNKNOWN_SKILLS
    return raw_skills, normalized_text, notes


# ==========================================
# DATE PARSING AND EXPERIENCE MATH
# ==========================================

# Qwen extracts rows; Python converts date text into date objects and computes years.
# This avoids asking the LLM to do math.

def parse_date_value(value: str, today: date, is_end: bool = False) -> Tuple[Optional[date], bool]:
    """
    Parse a resume date string.

    Returns:
        (parsed_date, is_current_marker)

    is_end controls year-only handling. A start year becomes Jan 1; an end year
    becomes Dec 31 to avoid undercounting year-only ranges too severely.
    """
    original_value = str(value or "").strip()
    value = original_value.lower().replace(".", "")
    value = value.replace("\u2013", "-").replace("\u2014", "-")
    value = re.sub(r"\s+", " ", value)

    if not value:
        return None, False

    if value in CURRENT_MARKERS:
        return today, True

    if re.search(r"\b(present|current|now|ongoing)\b", value):
        return today, True

    # YYYY-MM or YYYY/MM.
    year_month = re.search(r"\b((?:19|20)\d{2})[/-](\d{1,2})\b", value)
    if year_month:
        year = int(year_month.group(1))
        month = max(1, min(int(year_month.group(2)), 12))
        day = calendar.monthrange(year, month)[1] if is_end else 1
        return date(year, month, day), False

    # MM/YYYY or MM-YYYY.
    numeric = re.search(r"\b(\d{1,2})[/-]((?:19|20)\d{2})\b", value)
    if numeric:
        month = max(1, min(int(numeric.group(1)), 12))
        year = int(numeric.group(2))
        day = calendar.monthrange(year, month)[1] if is_end else 1
        return date(year, month, day), False

    # Month Year.
    month_year = re.search(r"\b([a-z]+)\s+((?:19|20)\d{2})\b", value)
    if month_year:
        month_text = month_year.group(1)
        year = int(month_year.group(2))
        month = MONTHS.get(month_text[:3], 1)
        day = calendar.monthrange(year, month)[1] if is_end else 1
        return date(year, month, day), False

    # Season Year.
    season_year = re.search(r"\b(spring|summer|fall|autumn|winter)\s+((?:19|20)\d{2})\b", value)
    if season_year:
        season = season_year.group(1)
        year = int(season_year.group(2))
        month = {"spring": 3, "summer": 6, "fall": 9, "autumn": 9, "winter": 1}.get(season, 1)
        day = calendar.monthrange(year, month)[1] if is_end else 1
        return date(year, month, day), False

    # Year only.
    year_only = re.search(r"\b((?:19|20)\d{2})\b", value)
    if year_only:
        year = int(year_only.group(1))
        return (date(year, 12, 31) if is_end else date(year, 1, 1)), False

    return None, False


def split_embedded_date_range(start_text: str, end_text: str) -> Tuple[str, str]:
    """
    Fix Qwen rows where the whole range is accidentally placed in start_date.

    Example:
        start_date="2021 - 2023", end_date="" -> "2021", "2023"
    """
    start_text = str(start_text or "").strip()
    end_text = str(end_text or "").strip()

    pattern = re.compile(r"^(.+?)\s*(?:-|to|through)\s*(.+)$", re.IGNORECASE)
    match = pattern.search(start_text)

    if match:
        embedded_start = match.group(1).strip()
        embedded_end = match.group(2).strip()

        # If Qwen says start="2020 - 2022" and end="Present", prefer the
        # embedded concrete end because the role probably ended.
        if end_text and re.search(r"present|current|now|ongoing", end_text, re.IGNORECASE):
            if not re.search(r"present|current|now|ongoing", embedded_end, re.IGNORECASE):
                return embedded_start, embedded_end
            return embedded_start, end_text

        if not end_text:
            return embedded_start, embedded_end

    return start_text, end_text


def merge_date_ranges(ranges: List[Tuple[date, date]]) -> List[Tuple[date, date]]:
    """Merge overlapping date ranges so overlapping jobs are not double-counted."""
    clean_ranges = [(start, end) for start, end in ranges if start and end and end >= start]
    if not clean_ranges:
        return []

    clean_ranges = sorted(clean_ranges, key=lambda item: item[0])
    merged = [clean_ranges[0]]

    for start, end in clean_ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return merged


def years_from_ranges(ranges: List[Tuple[date, date]]) -> float:
    """Convert merged date ranges into years rounded to two decimals."""
    total_days = sum(max((end - start).days, 0) for start, end in ranges)
    return round(total_days / 365.25, 2)


def calculate_total_experience_years(jobs: List[JobEntry]) -> float:
    """Calculate Total Experience from all countable role date ranges."""
    ranges = [(job.start_date, job.end_date) for job in jobs if job.is_countable]
    return years_from_ranges(merge_date_ranges(ranges))


def calculate_career_span_years(jobs: List[JobEntry]) -> float:
    """
    Calculate Career Span from earliest valid role start to latest valid role end.

    This is separate from Total Experience. Career Span includes gaps; Total
    Experience does not count gaps because it uses merged actual role ranges.
    """
    countable_jobs = [job for job in jobs if job.is_countable]
    if not countable_jobs:
        return 0.0

    earliest_start = min(job.start_date for job in countable_jobs)
    latest_end = max(job.end_date for job in countable_jobs)
    return years_from_ranges([(earliest_start, latest_end)])


# ==========================================
# EXPERIENCE ROW CLEANUP AND VALIDATION
# ==========================================

# These functions protect the experience calculation from common LLM mistakes.

def get_bool(value: Any) -> bool:
    """Convert common truthy values to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1", "current", "present", "now"}
    return False


def clean_company_name(company: str) -> str:
    """Remove locations, dates, and explanatory parentheticals from company names."""
    company = str(company or "").strip()
    company = re.sub(r"\s+", " ", company)

    # Remove explanatory parentheticals, e.g. "ABC Corp (remote contract)".
    company = re.sub(r"\s*\([^)]*\)\s*", " ", company).strip()

    # Remove common slash-based locations, e.g. "Company/NY Astoria".
    company = re.sub(r"/\s*(NY|NJ|CA|FL|PA|TX|CT|MA|IL|DC)\b.*$", "", company, flags=re.IGNORECASE)

    # Remove trailing city/state/location strings Qwen sometimes attaches.
    company = re.sub(
        r"\s+[-|,]?\s*(New York|NY|New Jersey|NJ|Miami|FL|Philadelphia|PA|Los Angeles|CA|Chicago|IL|Boston|MA|Washington|DC|Astoria|Brooklyn|Queens|Manhattan|Remote)\b.*$",
        "",
        company,
        flags=re.IGNORECASE,
    )

    # Remove date fragments accidentally attached to company.
    company = re.sub(r"\b(?:19|20)\d{2}\b.*$", "", company).strip()
    return company.strip(" -|,")


def clean_position_name(position: str, company: str = "") -> str:
    """Clean Qwen's job-title output so it contains the title only."""
    value = str(position or "").strip()
    value = re.sub(r"\s+", " ", value)

    # Convert "Analyst at BlackRock New York, NY" -> "Analyst".
    if re.search(r"\s+at\s+", value, re.IGNORECASE):
        left, _right = re.split(r"\s+at\s+", value, maxsplit=1, flags=re.IGNORECASE)
        if left.strip() and len(left.strip().split()) <= 10:
            value = left.strip()

    # Remove trailing company name if Qwen appended it to title.
    if company:
        company_clean = re.escape(clean_company_name(company))
        if company_clean:
            value = re.sub(rf"\s*[-|,]\s*{company_clean}\b.*$", "", value, flags=re.IGNORECASE)
            value = re.sub(rf"\b{company_clean}\b.*$", "", value, flags=re.IGNORECASE).strip()

    # Remove trailing location or date text.
    value = re.sub(
        r"\s+[-|,]?\s*(New York|NY|New Jersey|NJ|Miami|FL|Philadelphia|PA|Los Angeles|CA|Chicago|IL|Boston|MA|Washington|DC|Astoria|Brooklyn|Queens|Manhattan|Remote)\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\b(?:19|20)\d{2}\b.*$", "", value).strip()

    return value.strip(" -|,")


def is_non_professional_countable_role(title: str, company: str, extra_text: str = "") -> bool:
    """Identify internships, fellowships, volunteer work, and student/clinical roles."""
    combined = f"{title} {company} {extra_text}".lower()
    return contains_any_term(combined, NON_PROFESSIONAL_COUNTABLE_TERMS)


def normalize_experience_type(value: str, title: str, company: str) -> str:
    """Normalize Qwen's experience_type field to one of three allowed values."""
    raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
    combined = f"{title} {company} {raw}".lower()

    professional_program_terms = [
        "future leader program",
        "leadership development program",
        "rotational program",
        "management development program",
        "finance partner",
        "assistant controller",
        "business risk and compliance",
    ]
    if any(term in combined for term in professional_program_terms):
        return "professional"

    if raw in {
        "internship", "intern", "fellowship", "fellow", "volunteer", "student",
        "student_role", "teaching_assistant", "research_assistant", "externship",
        "extern", "practicum", "clinical_rotation", "clinical_rotations",
        "internship_fellowship_volunteer",
    }:
        return "internship_fellowship_volunteer"

    if is_non_professional_countable_role(title, company, raw):
        return "internship_fellowship_volunteer"

    if raw in {"exclude", "excluded", "do_not_count", "not_experience", "education", "certification"}:
        return "exclude"

    return "professional"


def looks_like_bad_position(value: str) -> bool:
    """Return True when a current_position value looks like a header/company/bullet."""
    value = str(value or "").strip()
    lower = value.lower()

    if not value:
        return True

    bad_patterns = [
        r"gpa", r"skills?", r"summary", r"education", r"degree", r"university", r"college",
        r"core competencies", r"expertise", r"contact", r"linkedin", r"accomplishments?",
        r"storytelling", r"creative activities", r"claimant communication", r"environment",
        r"\bfirm\b", r"\bcompany\b", r"\binc\.?\b", r"\bllc\b",
        r"consulting firm", r"business management consulting", r"management consulting firm",
        r"\bpractice\b", r"\borganization\b",
    ]
    if any(re.search(pattern, lower) for pattern in bad_patterns):
        return True

    if len(value.split()) > 10:
        return True

    return False


def looks_like_probable_real_job(title: str, company: str) -> bool:
    """Identify clear job rows that Qwen may have mislabeled as exclude."""
    title = str(title or "").strip()
    company = str(company or "").strip()
    combined = f"{title} {company}".lower()

    if not title:
        return False
    if len(title.split()) > 14 or len(company.split()) > 14:
        return False
    if any(term in combined for term in PROBABLE_JOB_TITLE_TERMS):
        return True
    return False


def looks_like_non_work_item(title: str, company: str, experience_type: str) -> bool:
    """Exclude rows that are education/certification/project/header content."""
    title = str(title or "").strip()
    company = str(company or "").strip()
    combined = f"{title} {company}".lower()

    if not title and not company:
        return True

    # Allow student/clinical roles even if the employer is a university or college.
    if experience_type == "internship_fellowship_volunteer" and is_non_professional_countable_role(title, company):
        return False

    if any(term in combined for term in BOARD_OR_COMMITTEE_TERMS):
        return True

    # Degree rows sometimes come back from Qwen as work rows, for example
    # "Civil Engineering - Associates @ Mercer County CC".  Exclude these
    # without blocking real job titles such as "Associate Teacher".
    if re.search(r"\bassociate(?:'s|s)?\b", combined) and (
        contains_any_term(combined, SCHOOL_WORDS)
        or re.search(r"\b(?:civil|mechanical|electrical|computer|chemical|industrial)\s+engineering\b", combined)
        or "community cc" in combined
        or "county cc" in combined
    ):
        return True

    if any(term in combined for term in PROJECT_EXCLUDE_TERMS):
        # Do not exclude real job titles such as Project Manager.
        if "project manager" not in combined and "project coordinator" not in combined:
            return True

    if any(term in combined for term in NON_WORK_EXCLUDE_TERMS):
        return True

    # A school name alone is not work. A real job at a school should have a real title.
    if contains_any_term(combined, SCHOOL_WORDS) and not looks_like_probable_real_job(title, company):
        return True

    if len(title.split()) > 16 or len(company.split()) > 16:
        return True

    return False


# ==========================================
# REGEX EXPERIENCE FALLBACK - OFF BY DEFAULT
# ==========================================

# This fallback exists only because your old script had it. It is intentionally
# conservative and adds review notes when used.

def simplified_heading(line: str) -> str:
    """Normalize headings and ignore decorative date ranges in heading text."""
    without_parenthetical_dates = re.sub(r"\([^)]*(?:19|20)\d{2}[^)]*\)", "", str(line or ""))
    return normalize_heading_text(without_parenthetical_dates)


def line_matches_any_heading(line: str, labels: Sequence[str]) -> bool:
    """Match strict section headings, including headings with parenthetical dates."""
    matched, _label, _remainder = match_section_heading(line, labels)
    if matched:
        return True

    heading = simplified_heading(line)
    return heading in {label.lower() for label in labels}


def get_work_section_lines(text: str) -> List[str]:
    """
    Return lines from likely work-history sections only.

    This keeps local date parsing from counting education, board service, skills,
    credentials, and other non-employment dates as professional experience.
    """
    lines: List[str] = []
    in_work_section = False

    for line in text.splitlines():
        if line_matches_any_heading(line, NON_WORK_SECTION_HEADERS):
            in_work_section = False
            continue

        if line_matches_any_heading(line, EXPERIENCE_SECTION_HEADERS):
            in_work_section = True
            continue

        if in_work_section:
            lines.append(line)

    return lines


def clean_resume_line(line: str) -> str:
    """Trim bullets and whitespace while preserving title/company punctuation."""
    line = str(line or "").strip()
    line = re.sub(r"^[\s\-*\u2022\u00b7]+", "", line)
    return re.sub(r"\s+", " ", line).strip()


def looks_like_location_text(value: str) -> bool:
    """Return True when a pipe/dash segment looks like a city/state/location."""
    value = str(value or "").strip()
    return bool(
        re.search(
            r"\b(remote|hybrid|onsite|[A-Z][a-z]+,\s*[A-Z]{2}|NY|NJ|CA|FL|PA|TX|CT|MA|IL|DC)\b",
            value,
        )
    )


def title_from_line(line: str) -> str:
    """Extract a concise job title from a nearby line, usually before a colon."""
    line = clean_resume_line(line)
    if not line:
        return ""

    title_candidate = line.split(":", 1)[0].strip()
    title_candidate = re.sub(r"\([^)]*(?:19|20)\d{2}[^)]*\)", "", title_candidate).strip()
    title_candidate = clean_position_name(title_candidate)

    if looks_like_bad_position(title_candidate):
        return ""
    if looks_like_probable_real_job(title_candidate, ""):
        return title_candidate

    return ""


def previous_nonempty_line(lines: Sequence[str], index: int) -> str:
    """Find the nearest meaningful line above a date range."""
    for previous_index in range(index - 1, -1, -1):
        line = clean_resume_line(lines[previous_index])
        if line:
            return line
    return ""


def nearby_title_after(lines: Sequence[str], index: int, max_lines: int = 3) -> str:
    """Find a title shortly after a company/date line, skipping descriptions."""
    for next_index in range(index + 1, min(len(lines), index + max_lines + 1)):
        title = title_from_line(lines[next_index])
        if title:
            return title
    return ""


def split_descriptor_into_company_title(descriptor: str, next_line: str = "") -> Tuple[str, str]:
    """
    Infer employer and title from the text next to a date range.

    Common resume forms handled here:
    - "Company | Location November 2023 - Present" plus title on the next line.
    - "Company - Social Work Intern" followed by the date range on the next line.
    - "Company | Director of Treasury (2004-2006)" on one line.
    """
    descriptor = clean_resume_line(descriptor)
    descriptor = re.sub(r"\([^)]*(?:19|20)\d{2}[^)]*\)", "", descriptor).strip(" -|,")
    next_title = title_from_line(next_line)

    if not descriptor:
        return "", next_title

    if "|" in descriptor:
        parts = [part.strip() for part in descriptor.split("|") if part.strip()]
        company = clean_company_name(parts[0]) if parts else ""
        title = ""
        if len(parts) > 1 and not looks_like_location_text(parts[1]):
            title = title_from_line(parts[1])
        return company, title or next_title

    dash_parts = re.split(r"\s+-\s+", descriptor, maxsplit=1)
    if len(dash_parts) == 2:
        left, right = [part.strip() for part in dash_parts]
        if title_from_line(right):
            return clean_company_name(left), title_from_line(right)
        if title_from_line(left):
            return clean_company_name(right), title_from_line(left)

    if title_from_line(descriptor):
        return "", title_from_line(descriptor)

    return clean_company_name(descriptor), next_title


def find_date_ranges_with_offsets(line: str, today: date) -> List[Tuple[date, date, bool, str, int, int]]:
    """Find date ranges in one resume line and keep offsets for title/company parsing."""
    date_token = (
        r"(?:"
        r"(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|"
        r"Sep|Sept|September|Oct|October|Nov|November|Dec|December)\.?\s+\d{4}"
        r"|\d{1,2}[/-]\d{4}"
        r"|\b(?:19|20)\d{2}\b"
        r")"
    )
    end_token = rf"(?:{date_token}|Present|Current|Now|Ongoing)"
    range_pattern = re.compile(rf"({date_token})\s*(?:-|to|through)\s*({end_token})", re.IGNORECASE)

    ranges: List[Tuple[date, date, bool, str, int, int]] = []
    for match in range_pattern.finditer(line):
        start, _start_current = parse_date_value(match.group(1), today, is_end=False)
        end, is_current = parse_date_value(match.group(2), today, is_end=True)
        if start and end and end >= start:
            ranges.append((start, end, is_current, match.group(0), match.start(), match.end()))

    return ranges


def extract_job_entries_from_work_sections(text: str, today: date) -> Tuple[List[JobEntry], List[str]]:
    """Create local JobEntry rows from date ranges inside work-history sections."""
    jobs: List[JobEntry] = []
    notes: List[str] = []
    lines = [clean_resume_line(line) for line in get_work_section_lines(text)]

    for index, line in enumerate(lines):
        if not line or line_matches_any_heading(line, NON_WORK_SECTION_HEADERS):
            continue

        for start, end, is_current, raw_range, start_index, _end_index in find_date_ranges_with_offsets(line, today):
            descriptor = line[:start_index].strip(" -|,(")
            previous_line = previous_nonempty_line(lines, index)
            next_line = nearby_title_after(lines, index)

            company, title = split_descriptor_into_company_title(descriptor, next_line)
            if not title:
                previous_company, previous_title = split_descriptor_into_company_title(previous_line, next_line)
                company = company or previous_company
                title = previous_title or title_from_line(previous_line)

            title = clean_position_name(title, company)
            company = clean_company_name(company)
            exp_type = "internship_fellowship_volunteer" if is_non_professional_countable_role(title, company, line) else "professional"

            if not title and not company:
                notes.append(f"Work-section fallback skipped range with no title/company: {raw_range}")
                continue

            if looks_like_non_work_item(title, company, exp_type):
                notes.append(f"Work-section fallback skipped non-work-looking item: {title} @ {company} | {raw_range}")
                continue

            jobs.append(
                JobEntry(
                    title=title or "Position Unknown",
                    company=company or "Company Unknown",
                    start_date=start,
                    end_date=end,
                    raw_line=raw_range,
                    is_current=is_current,
                    experience_type=exp_type,
                    source="work section fallback",
                )
            )

    return jobs, notes


def merge_job_lists(primary_jobs: List[JobEntry], fallback_jobs: List[JobEntry]) -> List[JobEntry]:
    """Add fallback rows only when they are not already represented by Qwen rows."""
    merged = list(primary_jobs)
    seen = {
        (
            job.title.lower(),
            job.company.lower(),
            job.start_date,
            job.end_date,
        )
        for job in merged
    }

    for job in fallback_jobs:
        signature = (job.title.lower(), job.company.lower(), job.start_date, job.end_date)
        if signature not in seen:
            merged.append(job)
            seen.add(signature)

    return merged

def should_exclude_context_for_regex(context: str) -> bool:
    """Reject regex date ranges that appear near non-work sections."""
    lower = context.lower()
    exclude_terms = NON_WORK_EXCLUDE_TERMS + BOARD_OR_COMMITTEE_TERMS + [
        "education", "academic", "course", "courses", "certified", "credential",
    ]
    return any(term in lower for term in exclude_terms)


def find_date_ranges(text: str, today: date) -> List[Tuple[date, date, bool, str]]:
    """Find date ranges in text for the optional regex fallback path."""
    date_token = (
        r"(?:"
        r"(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|"
        r"Sep|Sept|September|Oct|October|Nov|November|Dec|December)\.?\s+\d{4}"
        r"|\d{1,2}[/-]\d{4}"
        r"|\b(?:19|20)\d{2}\b"
        r")"
    )
    end_token = rf"(?:{date_token}|Present|Current|Now|Ongoing)"
    range_pattern = re.compile(rf"({date_token})\s*(?:-|to|through)\s*({end_token})", re.IGNORECASE)

    ranges = []
    for match in range_pattern.finditer(text):
        start, _start_current = parse_date_value(match.group(1), today, is_end=False)
        end, is_current = parse_date_value(match.group(2), today, is_end=True)
        if start and end and end >= start:
            ranges.append((start, end, is_current, match.group(0)))

    return ranges


def get_context_for_range(text: str, raw_range: str) -> str:
    """Get nearby text around a regex date range to guess title/company."""
    index = text.lower().find(raw_range.lower())
    if index == -1:
        return raw_range

    start = max(0, index - 220)
    end = min(len(text), index + len(raw_range) + 220)
    return text[start:end]


def guess_job_title(context: str) -> str:
    """Best-effort title guess for regex fallback rows."""
    lines = [line.strip() for line in context.splitlines() if line.strip()]
    bad_words = r"gpa|university|college|school|education|degree|bachelor|master|skills|summary|profile|email|phone|linkedin|certification"
    title_words = r"manager|specialist|analyst|director|controller|accountant|assistant|coordinator|administrator|engineer|consultant|nurse|teacher|operator|bookkeeper|auditor|associate|supervisor|clerk|representative|officer|lead|head|chief|cfo|ceo|president|vice president|vp|intern|fellow"

    for line in lines:
        clean_line = re.sub(r"\b(?:19|20)\d{2}\b.*", "", line).strip(" -|,")
        clean_line = re.sub(r"\s+", " ", clean_line)
        if not clean_line:
            continue
        if re.search(bad_words, clean_line, re.IGNORECASE):
            continue
        if "@" in clean_line or re.search(r"\d{3}[-.\s]?\d{3}[-.\s]?\d{4}", clean_line):
            continue
        if len(clean_line.split()) > 10:
            continue
        if re.search(title_words, clean_line, re.IGNORECASE):
            return clean_line

    return "Position Unknown"


def guess_company(context: str) -> str:
    """Best-effort company guess for regex fallback rows."""
    lines = [line.strip() for line in context.splitlines() if line.strip()]

    for line in lines:
        if line.startswith(("*", "-")):
            continue
        if re.search(r"education|skills|summary|gpa|certification", line, re.IGNORECASE):
            continue
        if len(line.split()) <= 10 and not re.search(r"@|\d{3}", line):
            return clean_company_name(line)

    return "Company Unknown"


def extract_job_entries_regex_fallback(text: str, today: date) -> Tuple[List[JobEntry], List[str]]:
    """Optional fallback to create JobEntry rows from global regex date ranges."""
    jobs: List[JobEntry] = []
    notes: List[str] = []
    seen = set()

    for start, end, is_current, raw_range in find_date_ranges(text, today):
        if raw_range.lower() in seen:
            continue
        seen.add(raw_range.lower())

        context = get_context_for_range(text, raw_range)
        if should_exclude_context_for_regex(context):
            notes.append(f"Regex fallback skipped non-work-looking range: {raw_range}")
            continue

        title = guess_job_title(context)
        company = guess_company(context)
        exp_type = "internship_fellowship_volunteer" if is_non_professional_countable_role(title, company, context) else "professional"

        if looks_like_non_work_item(title, company, exp_type):
            notes.append(f"Regex fallback skipped item that did not look like work: {title} @ {company} | {raw_range}")
            continue

        jobs.append(
            JobEntry(
                title=title,
                company=company,
                start_date=start,
                end_date=end,
                raw_line=raw_range,
                is_current=is_current,
                experience_type=exp_type,
                source="regex fallback",
            )
        )

    return jobs, notes


# ==========================================
# QWEN / LM STUDIO EXTRACTION
# ==========================================

# Qwen is used for structure, not math. The model extracts rows; Python validates
# and calculates. This makes results easier to debug and more consistent.

def extract_json_from_text(content: str) -> dict:
    """Extract and parse the first JSON object from a model response."""
    content = str(content or "").strip()
    content = content.replace("```json", "").replace("```", "").strip()

    start = content.find("{")
    end = content.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise json.JSONDecodeError("No JSON object found", content, 0)

    json_text = content[start:end + 1]

    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        # Remove trailing commas before trying once more. This handles a common
        # local-LLM formatting error without hiding larger JSON problems.
        cleaned = re.sub(r",\s*([}\]])", r"\1", json_text)
        return json.loads(cleaned)


def qwen_extract_resume(text: str, client: Any, model: str) -> dict:
    """Call LM Studio/Qwen and ask for strict JSON resume fields."""
    prompt = f"""
You are a recruiting resume parser. Return ONLY valid JSON. Do not include markdown, explanations, comments, or trailing text.

JSON schema:
{{
  "candidate_name": "",
  "current_position": "",
  "current_company": "",
  "currently_employed": false,
  "graduation_year": "",
  "resume_summary": "",
  "normalized_skills": [],
  "work_experience": [
    {{
      "company": "",
      "title": "",
      "start_date": "",
      "end_date": "",
      "is_current": false,
      "experience_type": "professional"
    }}
  ]
}}

Rules for all fields:
- Return only the JSON object.
- If a value is unknown, use an empty string or an empty list as appropriate.
- Do not calculate experience years. Python will calculate years.

Rules for work_experience:
- Include only real roles/positions held by the candidate.
- Put the job title in title and the employer/company in company. Do not swap them.
- A phrase like "business management consulting firm" is an employer description, not a job title.
- Include professional jobs, internships, fellowships, volunteer roles, externships, teaching assistant roles, research assistant roles, student assistant roles, clinical rotations, and practicums.
- Do NOT include education, school attendance, degree dates, GPA, certification/license dates, coursework, resume dates, article dates, project dates, board roles, advisory board roles, committee memberships, accomplishments, contact sections, skills sections, section headers, summaries, or headlines as jobs.
- For experience_type, use exactly one of: "professional", "internship_fellowship_volunteer", "exclude".
- Use "professional" for normal paid work.
- Use "internship_fellowship_volunteer" for internships, fellowships, volunteer roles, externships, student assistant roles, research assistant roles, teaching assistant roles, clinical rotations, and practicums.
- Use "exclude" for anything that is not a real job/role.
- start_date must be the older date when the role started.
- end_date must be the newer date when the role ended.
- If a role is current, set end_date to "Present" and is_current to true.
- If a role has a concrete end date or end year, set is_current to false.
- Preserve dates as written when possible, such as "March 2025", "06/2023", "2021", or "Present".

Rules for current position and employment:
- current_position and current_company must come only from the most recent current professional job.
- A current professional job should have Present, Current, Now, Ongoing, or a clearly current missing end date.
- Do not use a volunteer role, board role, advisory role, project, certification, school, headline, skill, or summary as current employment.
- current_position should be the job title only, for example "Technology Audit Analyst", not "Technology Audit Analyst at BlackRock New York, NY".
- current_company should be the employer name only, not city, state, dates, or title.
- If there is no current professional job, use empty strings and currently_employed false.

Rules for graduation_year:
- Use only explicit Education-section evidence.
- Return a year only when the year is tied to graduation/completion wording, Class of wording, expected/anticipated graduation wording, or a degree date/date range.
- If a degree has a date range, use the end year of that degree.
- A school/program/academy/training year by itself is NOT enough. Do not infer a graduation year from attendance, program year, training year, certification year, or job dates.
- Do not use job dates, certification dates, license dates, board dates, project dates, resume/header dates, summary dates, or work_experience dates as graduation_year.
- If graduation year cannot be determined from explicit Education-section degree/graduation evidence, use an empty string.

Rules for normalized_skills:
- Return a list of clean skill names.
- Include software, tools, systems, technical skills, business skills, accounting/finance skills, and other clear skills.
- Do not include full sentences.

Resume text:
{text[:14000]}
"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )

    content = response.choices[0].message.content.strip()
    parsed = extract_json_from_text(content)

    if not isinstance(parsed, dict):
        raise ValueError("Qwen did not return a JSON object.")

    if not isinstance(parsed.get("work_experience", []), list):
        parsed["work_experience"] = []

    if not isinstance(parsed.get("normalized_skills", []), list):
        parsed["normalized_skills"] = normalize_skill_output_to_list(parsed.get("normalized_skills", []))

    return parsed


def create_lmstudio_client(base_url: str) -> Any:
    """Create an OpenAI-compatible client pointed at LM Studio."""
    if OpenAI is None:
        raise ImportError("openai is missing. Install it with: pip install openai")

    return OpenAI(base_url=base_url, api_key="lm-studio")


def qwen_work_experience_to_jobs(qwen_data: dict, today: date) -> Tuple[List[JobEntry], List[str]]:
    """Validate Qwen work_experience rows and convert them to JobEntry objects."""
    jobs: List[JobEntry] = []
    notes: List[str] = []
    raw_jobs = qwen_data.get("work_experience", [])

    if not isinstance(raw_jobs, list):
        return jobs, ["Qwen work_experience was not a list."]

    qwen_current_position = clean_position_name(str(qwen_data.get("current_position", "")).strip())
    qwen_current_company = clean_company_name(str(qwen_data.get("current_company", "")).strip())
    qwen_currently_employed = get_bool(qwen_data.get("currently_employed", False))

    current_marker_pattern = re.compile(r"\b(present|current|now|ongoing)\b", re.IGNORECASE)

    for index, item in enumerate(raw_jobs, start=1):
        if not isinstance(item, dict):
            notes.append(f"Skipped work_experience item {index}: not an object")
            continue

        company = clean_company_name(str(item.get("company", "")).strip())
        title = clean_position_name(str(item.get("title", "")).strip(), company)
        start_text = str(item.get("start_date", "")).strip()
        end_text = str(item.get("end_date", "")).strip()
        item_is_current = get_bool(item.get("is_current", False))

        start_text, end_text = split_embedded_date_range(start_text, end_text)
        raw_line = f"{start_text} - {end_text}".strip(" -")

        exp_type = normalize_experience_type(str(item.get("experience_type", "professional")), title, company)

        if exp_type == "exclude":
            if looks_like_probable_real_job(title, company):
                exp_type = "internship_fellowship_volunteer" if is_non_professional_countable_role(title, company) else "professional"
                notes.append(f"Reclassified Qwen-excluded probable job item {index}: {title} @ {company} | {raw_line}")
            else:
                notes.append(f"Excluded item {index}: {title} @ {company} | {raw_line}")
                continue

        if looks_like_non_work_item(title, company, exp_type):
            notes.append(f"Excluded non-work-looking item {index}: {title} @ {company} | {raw_line}")
            continue

        if not title and not company:
            notes.append(f"Skipped item {index}: missing title and company")
            continue

        start_date, _start_current_marker = parse_date_value(start_text, today, is_end=False)
        end_date, end_current_marker = parse_date_value(end_text, today, is_end=True)
        end_text_has_current_marker = bool(current_marker_pattern.search(end_text))

        # Current status comes from the row dates, not a global resume search.
        current_by_dates = bool(end_current_marker or end_text_has_current_marker)

        # Missing end date can be current only when Qwen marks the row current and
        # the row is consistent with Qwen's current title/company signal.
        current_by_missing_end = False
        if not end_text and item_is_current:
            same_title = bool(title and qwen_current_position and (
                qwen_current_position.lower() in title.lower() or title.lower() in qwen_current_position.lower()
            ))
            same_company = bool(company and qwen_current_company and (
                qwen_current_company.lower() in company.lower() or company.lower() in qwen_current_company.lower()
            ))
            current_by_missing_end = same_title or same_company or qwen_currently_employed
            if current_by_missing_end:
                notes.append(f"Item {index} treated as current because end date was missing and Qwen marked the row current")

        looks_current = current_by_dates or current_by_missing_end

        if start_date is None:
            notes.append(f"Skipped item {index}: missing/unreadable start date for {title or 'Position Unknown'} @ {company or 'Company Unknown'}")
            continue

        if end_date is None and looks_current:
            end_date = today

        if end_date is None:
            notes.append(f"Skipped item {index}: missing/unreadable end date for {title or 'Position Unknown'} @ {company or 'Company Unknown'}")
            continue

        if end_date < start_date:
            reversed_span_years = (start_date - end_date).days / 365.25
            if 0 <= reversed_span_years <= 60:
                notes.append(f"Swapped start/end dates for item {index}: {title} @ {company} | {raw_line}")
                start_date, end_date = end_date, start_date
                looks_current = False
            else:
                notes.append(f"Skipped item {index}: end date before start date for {title} @ {company}")
                continue

        jobs.append(
            JobEntry(
                title=title or "Position Unknown",
                company=company or "Company Unknown",
                start_date=start_date,
                end_date=end_date,
                raw_line=raw_line,
                is_current=bool(looks_current),
                experience_type=exp_type,
                source="qwen",
            )
        )

    return jobs, notes


# ==========================================
# EMPLOYMENT STATUS AND DEBUG OUTPUT
# ==========================================

# Employment status uses only validated JobEntry rows. A global "Present" anywhere
# in the resume is not enough.

def current_professional_jobs(jobs: List[JobEntry]) -> List[JobEntry]:
    """Return current jobs that are professional, not volunteer/internship roles."""
    return [job for job in jobs if job.is_current and job.is_professional]


def get_employment_fields(jobs: List[JobEntry], qwen_data: dict) -> Tuple[str, str, str, List[str]]:
    """Determine Employment Status, Current Position, and Current Company."""
    notes: List[str] = []
    current_jobs = current_professional_jobs(jobs)

    # Clean Qwen current fields early so they can be used as a fallback when the
    # validated current job row has a bad title such as "Business Management Consulting Firm".
    qwen_currently_employed = get_bool(qwen_data.get("currently_employed", False))
    qwen_company = clean_company_name(str(qwen_data.get("current_company", "")).strip())
    qwen_position = clean_position_name(str(qwen_data.get("current_position", "")).strip(), qwen_company)

    if current_jobs:
        latest = max(current_jobs, key=lambda job: job.start_date)
        company = clean_company_name(latest.company) or qwen_company
        position = clean_position_name(latest.title, company)

        if not position or looks_like_bad_position(position):
            if qwen_position and not looks_like_bad_position(qwen_position):
                position = qwen_position
                notes.append("Current position came from Qwen fallback because current work_experience title looked like a company/header")
            else:
                position = "Current Position Unknown"
                notes.append("Current professional job found, but current position needs review")

        return "Employed", position, company, notes

    # Do not mark as Employed from Qwen alone. Use Possibly Employed so it can be reviewed.

    professional_jobs = [job for job in jobs if job.is_professional]

    if qwen_currently_employed:
        notes.append("Qwen indicated currently_employed, but no validated current professional work row was found")
        position = qwen_position if qwen_position and not looks_like_bad_position(qwen_position) else ""
        company = qwen_company
        if not position and professional_jobs:
            latest = max(professional_jobs, key=lambda job: job.end_date)
            position = clean_position_name(latest.title, latest.company)
            company = company or clean_company_name(latest.company)
            notes.append("Used most recent professional role because Qwen current position was missing or invalid")
        return "Possibly Employed", position or NO_CURRENT_POSITION, company, notes

    if professional_jobs:
        latest = max(professional_jobs, key=lambda job: job.end_date)
        notes.append("No current professional role found; using most recent professional role for dashboard role/company fields")
        return "Unemployed", clean_position_name(latest.title, latest.company), clean_company_name(latest.company), notes

    ignored_current_nonprofessional = [job for job in jobs if job.is_current and not job.is_professional]
    if ignored_current_nonprofessional:
        notes.append("Current non-professional role found but ignored for Employment Status")

    return "Unemployed", NO_CURRENT_POSITION, "", notes


def build_debug_work_experience(jobs: List[JobEntry]) -> str:
    """Create a human-readable audit trail of counted work rows."""
    if not jobs:
        return ""

    parts = []
    for job in jobs:
        current = "current" if job.is_current else "ended"
        parts.append(
            f"{job.title} @ {job.company} | {job.raw_line} | {job.experience_type} | {current} | {job.source}"
        )

    return " || ".join(parts)


# ==========================================
# PARSE ONE RESUME
# ==========================================

# parse_resume() coordinates all pieces:
#   1. Read and clean resume text.
#   2. Ask Qwen for structured fields when enabled.
#   3. Convert Qwen rows into validated JobEntry rows.
#   4. Optionally use regex fallback if Qwen rows are not usable and fallback is allowed.
#   5. Calculate experience, graduation year, skills, employment status, and CSV output.

def parse_resume(
    path: Path,
    today: date,
    client: Optional[Any],
    model: str,
    use_llm: bool,
    allow_regex_fallback: bool,
) -> dict:
    """Parse one resume file and return one CSV row dictionary."""
    raw_text = read_resume_text(path)
    text = clean_text(raw_text)

    qwen_data: Dict[str, Any] = {}
    qwen_notes: List[str] = []
    review_notes: List[str] = []
    experience_source = "none"

    jobs: List[JobEntry] = []
    qwen_returned_work_items = False

    if use_llm and client is not None:
        try:
            qwen_data = qwen_extract_resume(text, client, model)
            raw_items = qwen_data.get("work_experience", [])
            qwen_returned_work_items = isinstance(raw_items, list) and len(raw_items) > 0
            jobs, qwen_notes = qwen_work_experience_to_jobs(qwen_data, today)
        except Exception as exc:
            qwen_notes.append(f"Qwen extraction failed: {exc}")
            review_notes.append("Qwen extraction failed")

    section_jobs, section_notes = extract_job_entries_from_work_sections(text, today)
    if section_notes:
        qwen_notes.extend(section_notes)

    if section_jobs:
        if jobs:
            before_count = len(jobs)
            jobs = merge_job_lists(jobs, section_jobs)
            if len(jobs) > before_count:
                qwen_notes.append("Added work-section fallback rows that were missing from Qwen work_experience")
        else:
            jobs = section_jobs
            qwen_notes.append("Used work-section fallback rows because Qwen returned no usable dated work_experience")

    if jobs:
        has_qwen_jobs = any(job.source == "qwen" for job in jobs)
        has_section_jobs = any(job.source == "work section fallback" for job in jobs)
        if has_qwen_jobs and has_section_jobs:
            experience_source = "qwen work_experience plus work-section fallback"
        elif has_qwen_jobs:
            experience_source = "qwen work_experience" if not qwen_notes else "qwen work_experience with skipped/reviewed rows"
        else:
            experience_source = "work-section fallback"
    elif allow_regex_fallback:
        jobs, regex_notes = extract_job_entries_regex_fallback(text, today)
        qwen_notes.extend(regex_notes)
        experience_source = "regex fallback allowed"
        review_notes.append("Regex fallback used; verify experience years manually")
    else:
        if use_llm and qwen_returned_work_items:
            experience_source = "qwen no usable dated work_experience"
            qwen_notes.append("Qwen returned work_experience rows, but none had usable dates for year calculation. Regex fallback was not used to avoid inflated experience years.")
            review_notes.append("Review work experience dates manually")
        elif use_llm:
            experience_source = "qwen no work_experience"
            qwen_notes.append("Qwen returned no usable work_experience rows. Regex fallback was not used to avoid inflated experience years.")
            review_notes.append("Review work experience manually")
        else:
            experience_source = "no_llm no experience calculation"
            qwen_notes.append("--no_llm was selected and regex fallback was not allowed")
            review_notes.append("Run with LM Studio or add --allow_regex_fallback")

    total_experience_years = calculate_total_experience_years(jobs)
    career_span_years = calculate_career_span_years(jobs)

    employment_status, current_position, current_company, employment_notes = get_employment_fields(jobs, qwen_data)
    review_notes.extend(employment_notes)

    graduation_year, graduation_notes = get_final_graduation_year(qwen_data, text, today)
    qwen_notes.extend(graduation_notes)

    raw_skills, normalized_skills, skill_notes = extract_combined_skills(text, qwen_data)
    review_notes.extend(skill_notes)

    if any(
        signal in note
        for note in qwen_notes
        for signal in ["Skipped", "skipped", "Swapped", "Reclassified", "Excluded", "unreadable", "missing"]
    ):
        review_notes.append("Some work_experience rows were skipped, date-fixed, excluded, or reclassified")

    candidate_name = choose_candidate_name(str(qwen_data.get("candidate_name", "")).strip(), text, path, qwen_notes)
    resume_summary = extract_resume_summary(text, qwen_data)
    needs_review = "No" if not review_notes else "; ".join(dict.fromkeys(review_notes))

    return {
        "Candidate": candidate_name,
        "Email": extract_email(text),
        "Phone Number": extract_phone(text),
        "Employment Status": employment_status,
        "Graduation Year": graduation_year,
        "Total Experience (Years)": total_experience_years,
        "Career Span (Years)": career_span_years,
        "Skills": raw_skills,
        "Normalized Skills": normalized_skills,
        "Current Position": current_position,
        "Current Company": current_company,
        "Resume Summary": resume_summary,
        "Experience Source": experience_source,
        "Parsing Notes": " | ".join(dict.fromkeys(qwen_notes)),
        "Needs Review": needs_review,
        "Debug Work Experience": build_debug_work_experience(jobs),
    }


# ==========================================
# FILE DISCOVERY, CSV WRITING, CLI
# ==========================================

# These functions keep the command-line workflow close to the previous script.

def find_resume_files(input_folder: Path) -> List[Path]:
    """Find supported resume files in the input folder."""
    supported = {".pdf", ".docx", ".doc"}
    return sorted(path for path in input_folder.iterdir() if path.suffix.lower() in supported)


def write_csv(rows: List[dict], output_csv: Path) -> None:
    """Write all parsed rows to the output CSV using the fixed column order."""
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    """Define command-line arguments."""
    parser = argparse.ArgumentParser(description="Parse resumes into a CSV spreadsheet.")
    parser.add_argument(
        "--input_folder",
        required=True,
        help="Folder containing PDF, DOCX, or DOC resumes.",
    )
    parser.add_argument(
        "--output_csv",
        default="resume_results_v9.csv",
        help="CSV file to create. Default: resume_results_v9.csv",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"LM Studio model id. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--lmstudio_url",
        default=DEFAULT_BASE_URL,
        help=f"LM Studio base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--no_llm",
        action="store_true",
        help="Run without Qwen/LM Studio. Experience years will be 0 unless --allow_regex_fallback is also used.",
    )
    parser.add_argument(
        "--allow_regex_fallback",
        action="store_true",
        help="Allow old regex experience parsing. Use only for troubleshooting because it can count non-job dates.",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point used when the script is run from the command line."""
    args = parse_args()
    input_folder = Path(args.input_folder)
    output_csv = Path(args.output_csv)
    today = datetime.today().date()

    if not input_folder.exists() or not input_folder.is_dir():
        raise NotADirectoryError(f"Input folder does not exist: {input_folder}")

    resume_files = find_resume_files(input_folder)
    if not resume_files:
        raise FileNotFoundError(f"No PDF, DOCX, or DOC files found in: {input_folder}")

    client = None
    use_llm = not args.no_llm
    if use_llm:
        try:
            client = create_lmstudio_client(args.lmstudio_url)
        except Exception as exc:
            parser_logger.warning("LM Studio client could not be created. Continuing without Qwen. Error: %s", exc)
            use_llm = False

    rows = []
    errors = []

    for resume_file in resume_files:
        try:
            row = parse_resume(
                resume_file,
                today,
                client,
                args.model,
                use_llm,
                args.allow_regex_fallback,
            )
            rows.append(row)
            parser_logger.info("Parsed: %s", resume_file.name)
        except Exception as exc:
            errors.append((resume_file.name, str(exc)))
            parser_logger.exception("ERROR parsing %s", resume_file.name)

    write_csv(rows, output_csv)
    parser_logger.info("Done. Wrote %s rows to %s", len(rows), output_csv)

    if errors:
        parser_logger.warning("Files with errors:")
        for filename, error in errors:
            parser_logger.warning("- %s: %s", filename, error)


if __name__ == "__main__":
    main()
