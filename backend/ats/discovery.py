from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import requests

SUPPORTED_HOST_PATTERNS = (
    "jobs.ashbyhq.com",
    "job-boards.greenhouse.io",
    "boards.greenhouse.io",
    "jobs.lever.co",
    "api.lever.co",
)


@dataclass(frozen=True)
class DiscoveredCareersPage:
    original_url: str
    discovered_url: str
    provider: str


class CareersPageParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.discovered_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        attributes = dict(attrs)
        for attribute_name in ("href", "src", "data-src", "data-href"):
            value = attributes.get(attribute_name)
            if value:
                self._maybe_add(value)

        if tag.lower() == "script":
            for key, value in attributes.items():
                if key.lower().startswith("data-") and isinstance(value, str):
                    self._maybe_add(value)

    def _maybe_add(self, value: str) -> None:
        absolute_url = urljoin(self.base_url, value.strip())
        if is_supported_ats_url(absolute_url):
            self.discovered_urls.append(absolute_url)


def _normalize_url(value: str) -> str:
    raw_url = value.strip()
    if raw_url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw_url):
        raw_url = f"https://{raw_url}"
    return raw_url


def is_supported_ats_url(url: str) -> bool:
    parsed = urlparse(_normalize_url(url))
    host = parsed.netloc.lower()
    return any(pattern in host for pattern in SUPPORTED_HOST_PATTERNS)


def discover_supported_ats_url(careers_page_url: str, timeout: int = 30) -> str:
    original_url = _normalize_url(careers_page_url)
    response = requests.get(
        original_url,
        timeout=timeout,
        allow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            )
        },
    )
    response.raise_for_status()

    if is_supported_ats_url(response.url):
        return response.url

    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type.lower() and "<html" not in response.text.lower():
        raise ValueError("No supported ATS detected on this careers page.")

    parser = CareersPageParser(response.url)
    parser.feed(response.text)

    if parser.discovered_urls:
        return _canonicalize_supported_url(parser.discovered_urls[0])

    inline_urls = re.findall(r"https?://[^\s\"'<>]+", response.text)
    for candidate in inline_urls:
        if is_supported_ats_url(candidate):
            return _canonicalize_supported_url(candidate)

    raise ValueError("No supported ATS detected on this careers page.")


def _canonicalize_supported_url(url: str) -> str:
    parsed = urlparse(_normalize_url(url))
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")
    parts = [part for part in path.split("/") if part]

    if "jobs.ashbyhq.com" in host:
        return f"https://jobs.ashbyhq.com/{parts[0]}" if parts else url

    if "jobs.lever.co" in host:
        return f"https://jobs.lever.co/{parts[0]}" if parts else url

    if "api.lever.co" in host:
        if len(parts) >= 3 and parts[0] == "v0" and parts[1] == "postings":
            return f"https://jobs.lever.co/{parts[2]}"
        if parts:
            return f"https://jobs.lever.co/{parts[-1]}"

    if "greenhouse.io" in host:
        query_params = parse_qs(parsed.query)
        board_token = query_params.get("for", [parts[-1] if parts else ""])[0]
        if "boards-api.greenhouse.io" in host:
            return f"https://boards-api.greenhouse.io/v1/boards/{parts[-1]}" if parts else url
        if board_token:
            return f"https://boards-api.greenhouse.io/v1/boards/{board_token}"

    return url
