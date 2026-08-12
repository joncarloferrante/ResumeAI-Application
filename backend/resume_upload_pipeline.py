from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .full_resume_parser import read_pdf, read_docx


MIN_TEXT_CHARS = 220
MIN_WORD_COUNT = 35
MIN_ALNUM_RATIO = 0.55
MIN_VALIDATION_SCORE = 3


@dataclass
class ExtractionResult:
    text: str
    page_count: int | None = None
    is_scanned: bool = False
    ocr_attempted: bool = False
    ocr_available: bool = False
    ocr_reason: str | None = None


@dataclass
class ValidationResult:
    is_resume: bool
    score: int
    signals: list[str]
    reason: str | None = None


def read_resume_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return read_pdf(path)
    if suffix == ".docx":
        return read_docx(path)
    raise ValueError(f"Unsupported file type: {path.name}")


def extract_pdf_page_count(path: Path) -> int | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(str(path))
        return len(reader.pages)
    except Exception:
        return None


def is_meaningful_text(text: str, page_count: int | None = None) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    alnum_count = sum(1 for char in normalized if char.isalnum())
    word_count = len(re.findall(r"\b[\w.+-]{2,}\b", normalized))
    alnum_ratio = (alnum_count / max(len(normalized), 1)) if normalized else 0.0

    if len(normalized) < MIN_TEXT_CHARS:
        return False
    if word_count < MIN_WORD_COUNT:
        return False
    if alnum_ratio < MIN_ALNUM_RATIO:
        return False
    if page_count and page_count > 1 and len(normalized) < (page_count * 90):
        return False
    return True


def get_ocr_service():
    return None


def extract_text_with_ocr(path: Path) -> ExtractionResult:
    text = read_resume_text(path)
    page_count = extract_pdf_page_count(path) if path.suffix.lower() == ".pdf" else None
    if is_meaningful_text(text, page_count):
        return ExtractionResult(text=text, page_count=page_count)

    ocr_service = get_ocr_service()
    if ocr_service is None:
        return ExtractionResult(
            text=text,
            page_count=page_count,
            is_scanned=path.suffix.lower() == ".pdf",
            ocr_attempted=True,
            ocr_available=False,
            ocr_reason="ocr_not_configured",
        )

    ocr_text = ocr_service.extract_text(path)
    return ExtractionResult(
        text=ocr_text or "",
        page_count=page_count,
        is_scanned=path.suffix.lower() == ".pdf",
        ocr_attempted=True,
        ocr_available=True,
    )


def validate_resume_content(text: str) -> ValidationResult:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return ValidationResult(False, 0, [], "empty_text")

    lower = cleaned.lower()
    signals = []
    score = 0

    if re.search(r"\b(?:experience|employment|work history|professional experience)\b", lower):
        signals.append("experience")
        score += 1
    if re.search(r"\b(?:skills|technical skills|core competencies)\b", lower):
        signals.append("skills")
        score += 1
    if re.search(r"\b(?:education|bachelor|master|degree|university|college)\b", lower):
        signals.append("education")
        score += 1
    if re.search(r"\b(?:@|\b\d{3}[-.)]\d{3}[-.]\d{4}\b|\blinkedin\b|\bemail\b)\b", lower):
        signals.append("contact")
        score += 1
    if re.search(r"\b(?:resume|curriculum vitae|cv|profile|summary|objective)\b", lower):
        signals.append("resume_heading")
        score += 1
    if re.search(r"\b(?:19|20)\d{2}\b", lower):
        signals.append("dates")
        score += 1
    if re.search(r"\b(?:manager|analyst|director|engineer|specialist|coordinator|consultant|accountant|recruiter)\b", lower):
        signals.append("job_titles")
        score += 1

    if score >= MIN_VALIDATION_SCORE:
        return ValidationResult(True, score, signals)
    return ValidationResult(False, score, signals, "not_resume_like")
