from __future__ import annotations

import argparse
import json
import time

from .database import precompute_python_matches
from .logging_config import configure_logging, get_logger


logger = get_logger("precompute")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Precompute Python-only job/candidate matches into the existing match_cache table."
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Recompute all pairs even when the cache is currently valid.",
    )
    parser.add_argument(
        "--all-jobs",
        action="store_true",
        help="Include inactive scraped jobs in the precompute run.",
    )
    return parser


def main() -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args()

    logger.info(
        "Python-only precompute starting | force_refresh=%s | active_jobs_only=%s",
        args.force_refresh,
        not args.all_jobs,
    )
    started_at = time.perf_counter()
    result = precompute_python_matches(
        active_jobs_only=not args.all_jobs,
        force_refresh=args.force_refresh,
    )
    elapsed_seconds = time.perf_counter() - started_at

    summary = {
        **result,
        "wall_clock_seconds": round(elapsed_seconds, 6),
    }
    logger.info("Python-only precompute complete | %s", json.dumps(summary, separators=(",", ":")))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
