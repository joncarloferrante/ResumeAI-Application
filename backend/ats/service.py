from __future__ import annotations

from datetime import datetime, timezone

from .registry import detect_adapter
from ..database import (
    get_job_sources,
    get_job_source_by_id,
    save_job_source,
    update_job_source_sync_result,
    get_connection,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_jobs_for_source(source: dict, jobs: list[dict]) -> tuple[int, int, int]:
    from .ashby import upsert_normalized_jobs

    return upsert_normalized_jobs(source, jobs)


def _mark_missing_jobs_inactive(source: dict, seen_job_keys: set[str]) -> int:
    if not seen_job_keys:
        return 0

    with get_connection() as conn:
        cursor = conn.cursor()
        placeholders = ", ".join("?" for _ in seen_job_keys)
        cursor.execute(
            f"""
            UPDATE scraped_jobs
            SET active = 0,
                last_scraped = ?,
                last_seen_at = ?
            WHERE source = ?
              AND COALESCE(source_slug, '') = ?
              AND job_key IS NOT NULL
              AND job_key NOT IN ({placeholders})
              AND COALESCE(active, 1) = 1
            """,
            (
                _utc_now(),
                _utc_now(),
                source.get("source_type", ""),
                source.get("source_slug", ""),
                *seen_job_keys,
            ),
        )
        return cursor.rowcount


def register_or_update_job_source(careers_url: str) -> dict:
    adapter = detect_adapter(careers_url)
    if not adapter:
        raise ValueError("This careers platform is not supported yet.")

    normalized_url = adapter.normalize_careers_url(careers_url)
    company_slug = adapter.extract_company_slug(normalized_url)
    source = save_job_source(
        {
            "company_name": company_slug,
            "source_type": adapter.source_name,
            "careers_url": normalized_url,
            "source_slug": company_slug,
            "enabled": 1,
        }
    )
    return source


def sync_job_source(source_id: int) -> dict:
    source = get_job_source_by_id(source_id)
    if not source:
        raise ValueError("Source not found.")

    if not source.get("enabled", 1):
        raise ValueError("Source is disabled.")

    adapter = detect_adapter(source["careers_url"])
    if not adapter:
        raise ValueError("This careers platform is not supported yet.")

    now = _utc_now()
    try:
        normalized_url = adapter.normalize_careers_url(source["careers_url"])
        jobs = adapter.fetch_jobs(normalized_url)
        source["careers_url"] = normalized_url
        inserted, updated, skipped = upsert_jobs_for_source(source, jobs)
        seen_job_keys = {job.get("job_key") for job in jobs if job.get("job_key")}
        inactivated = _mark_missing_jobs_inactive(source, seen_job_keys)
        update_job_source_sync_result(
            source_id,
            {
                "last_sync_at": now,
                "last_successful_sync_at": now,
                "last_sync_status": "success",
                "last_sync_error": "",
                "last_sync_job_count": len(jobs),
            },
        )
        return {
            "source_id": source_id,
            "jobs_found": len(jobs),
            "jobs_added": inserted,
            "jobs_updated": updated,
            "jobs_skipped": skipped,
            "jobs_inactivated": inactivated,
            "jobs_failed": 0,
            "status": "success",
        }
    except Exception as exc:
        update_job_source_sync_result(
            source_id,
            {
                "last_sync_at": now,
                "last_sync_status": "failed",
                "last_sync_error": str(exc),
            },
        )
        raise


def sync_enabled_job_sources() -> dict:
    sources = [source for source in get_job_sources() if int(source.get("enabled", 0)) == 1]
    summary = {
        "sources_attempted": 0,
        "sources_succeeded": 0,
        "sources_failed": 0,
        "jobs_added": 0,
        "jobs_updated": 0,
        "jobs_skipped": 0,
        "jobs_inactivated": 0,
        "results": [],
    }

    for source in sources:
        summary["sources_attempted"] += 1
        try:
            result = sync_job_source(int(source["id"]))
            summary["sources_succeeded"] += 1
            summary["jobs_added"] += int(result.get("jobs_added", 0))
            summary["jobs_updated"] += int(result.get("jobs_updated", 0))
            summary["jobs_skipped"] += int(result.get("jobs_skipped", 0))
            summary["jobs_inactivated"] += int(result.get("jobs_inactivated", 0))
            summary["results"].append({"source_id": source["id"], "status": "success", **result})
        except Exception as exc:
            summary["sources_failed"] += 1
            summary["results"].append({
                "source_id": source["id"],
                "status": "failed",
                "error": str(exc),
            })

    return summary
