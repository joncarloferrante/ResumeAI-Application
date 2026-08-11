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
    def matches(self, careers_url: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def import_jobs(self, careers_url: str) -> ATSImportResult:
        raise NotImplementedError

