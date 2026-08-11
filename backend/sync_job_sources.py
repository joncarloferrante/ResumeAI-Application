from __future__ import annotations

import sys
from pathlib import Path

from .ats.service import sync_enabled_job_sources
from .database import init_db
from .logging_config import configure_logging, get_logger


def main() -> int:
    configure_logging()
    logger = get_logger("cron")

    try:
        init_db()
        summary = sync_enabled_job_sources()
    except Exception as exc:
        logger.exception("Automated ATS sync failed before processing sources: %s", exc)
        print(f"fatal_error: {exc}")
        return 1

    print("ATS sync summary")
    print(f"sources_attempted: {summary['sources_attempted']}")
    print(f"sources_succeeded: {summary['sources_succeeded']}")
    print(f"sources_failed: {summary['sources_failed']}")
    print(f"jobs_added: {summary['jobs_added']}")
    print(f"jobs_updated: {summary['jobs_updated']}")
    print(f"jobs_skipped: {summary['jobs_skipped']}")
    print(f"jobs_inactivated: {summary['jobs_inactivated']}")

    for result in summary["results"]:
        if result["status"] == "success":
            logger.info(
                "ATS source synced | source_id=%s | jobs_added=%s | jobs_updated=%s | jobs_skipped=%s | jobs_inactivated=%s",
                result["source_id"],
                result.get("jobs_added", 0),
                result.get("jobs_updated", 0),
                result.get("jobs_skipped", 0),
                result.get("jobs_inactivated", 0),
            )
        else:
            logger.error("ATS source sync failed | source_id=%s | error=%s", result["source_id"], result.get("error", "unknown"))
            print(f"source_failed: {result['source_id']} | {result.get('error', 'unknown')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
