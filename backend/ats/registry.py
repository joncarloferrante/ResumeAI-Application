from __future__ import annotations

from .ashby import AshbyAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter


ADAPTERS = [AshbyAdapter(), GreenhouseAdapter(), LeverAdapter()]


def detect_adapter(careers_url: str):
    for adapter in ADAPTERS:
        if adapter.matches(careers_url):
            return adapter
    return None
