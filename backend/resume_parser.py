from datetime import datetime
from pathlib import Path

from .full_resume_parser import parse_resume, create_lmstudio_client, DEFAULT_MODEL, DEFAULT_BASE_URL


def parse_resume_file(path: Path) -> dict:
    return parse_resume_file_with_text(path)


def parse_resume_file_with_text(path: Path, raw_text: str | None = None) -> dict:
    today = datetime.today().date()

    try:
        client = create_lmstudio_client(DEFAULT_BASE_URL)
    except Exception:
        # New uploads should still get deterministic contact/name/work-section parsing
        # when LM Studio or the OpenAI client package is unavailable.
        client = None

    return parse_resume(
        path=path,
        today=today,
        client=client,
        model=DEFAULT_MODEL,
        use_llm=client is not None,
        allow_regex_fallback=False,
        raw_text=raw_text,
    )
