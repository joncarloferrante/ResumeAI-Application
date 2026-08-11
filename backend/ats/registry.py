from __future__ import annotations

from .ashby import AshbyAdapter


ADAPTERS = [AshbyAdapter()]


def detect_adapter(careers_url: str):
    for adapter in ADAPTERS:
        if adapter.matches(careers_url):
            return adapter
    return None

