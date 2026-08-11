from __future__ import annotations

from dataclasses import dataclass
from abc import ABC, abstractmethod


@dataclass(frozen=True)
class ATSImportResult:
    source: str
    company: str
    careers_url: str
    detected_ats: str
    jobs_found: int
    jobs_added: int
    jobs_updated: int
    jobs_skipped: int
    jobs_failed: int


class ATSAdapter(ABC):
    source_name: str

    @abstractmethod
    def normalize_careers_url(self, careers_url: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def extract_company_slug(self, careers_url: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def matches(self, careers_url: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def fetch_jobs(self, careers_url: str) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def build_import_result(
        self,
        careers_url: str,
        jobs_found: int,
        jobs_added: int,
        jobs_updated: int,
        jobs_skipped: int,
        jobs_failed: int,
    ) -> ATSImportResult:
        raise NotImplementedError
