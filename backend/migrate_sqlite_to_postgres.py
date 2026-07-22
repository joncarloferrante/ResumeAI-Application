"""One-time migration from the local SQLite database into PostgreSQL.

Run explicitly:
    python -m backend.migrate_sqlite_to_postgres
"""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .database import get_connection, init_db


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SQLITE_DB_PATH = PROJECT_ROOT / "database" / "resumeai.db"
EXCLUDED_TABLES = {"sqlite_sequence"}


def _get_sqlite_connection() -> sqlite3.Connection:
    if not SQLITE_DB_PATH.exists():
        raise FileNotFoundError(f"SQLite database not found: {SQLITE_DB_PATH}")
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _list_sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        ORDER BY name
        """
    )
    return [row["name"] for row in cursor.fetchall() if row["name"] not in EXCLUDED_TABLES]


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row["name"] for row in cursor.fetchall()]


def _table_primary_key_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row for row in cursor.fetchall() if row["pk"]]
    columns.sort(key=lambda row: row["pk"])
    return [row["name"] for row in columns]


def _sqlite_unique_keys(conn: sqlite3.Connection, table_name: str) -> list[list[str]]:
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA index_list({table_name})")
    unique_keys: list[list[str]] = []
    for index_row in cursor.fetchall():
        if not index_row["unique"]:
            continue
        index_name = index_row["name"]
        cursor.execute(f"PRAGMA index_info({index_name})")
        columns = [row["name"] for row in cursor.fetchall()]
        if columns:
            unique_keys.append(columns)

    pk_columns = _table_primary_key_columns(conn, table_name)
    if pk_columns:
        unique_keys.append(pk_columns)

    deduped: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for key in unique_keys:
        key_tuple = tuple(key)
        if key_tuple not in seen:
            seen.add(key_tuple)
            deduped.append(key)
    return deduped


def _sqlite_column_map(conn: sqlite3.Connection, table_names: list[str]) -> dict[str, list[str]]:
    return {table_name: _table_columns(conn, table_name) for table_name in table_names}


def _postgres_column_map(pg_conn, table_names: list[str]) -> dict[str, list[str]]:
    cursor = pg_conn.cursor()
    result: dict[str, list[str]] = {}
    for table_name in table_names:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = CURRENT_SCHEMA()
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        rows = cursor.fetchall()
        result[table_name] = [str(row["column_name"]) for row in rows]
    return result


def _postgres_unique_keys(pg_conn, table_name: str) -> list[list[str]]:
    cursor = pg_conn.cursor()
    cursor.execute(
        """
        SELECT tc.constraint_name, kcu.column_name, kcu.ordinal_position
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = CURRENT_SCHEMA()
          AND tc.table_name = %s
          AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
        ORDER BY tc.constraint_name, kcu.ordinal_position
        """,
        (table_name,),
    )
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in cursor.fetchall():
        grouped[str(row["constraint_name"])].append(str(row["column_name"]))
    return [columns for columns in grouped.values() if columns]


def _missing_columns(source_columns: dict[str, list[str]], dest_columns: dict[str, list[str]]) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for table_name, columns in source_columns.items():
        missing_cols = [column for column in columns if column not in dest_columns.get(table_name, [])]
        if missing_cols:
            missing[table_name] = missing_cols
    return missing


def _format_grouped_dict(title: str, data: dict[str, list[str]]) -> None:
    print(title)
    if not data:
        print("  none")
        return
    for table_name, values in data.items():
        print(f"  {table_name}: {', '.join(values)}")


def _row_sort_key(row: dict, pk_columns: list[str], prefer_last_scraped: bool = False) -> tuple:
    last_scraped = str(row.get("last_scraped") or "")
    pk_values = tuple(int(row.get(column) or 0) for column in pk_columns) if pk_columns else (0,)
    if prefer_last_scraped:
        return (last_scraped, *pk_values)
    return (*pk_values,)


def _dedupe_rows(rows: list[dict], key_columns: list[str], pk_columns: list[str], *, prefer_last_scraped: bool = False) -> tuple[list[dict], int]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(column) for column in key_columns)].append(row)

    deduped_rows: list[dict] = []
    skipped = 0
    for group_rows in grouped.values():
        selected = max(group_rows, key=lambda row: _row_sort_key(row, pk_columns, prefer_last_scraped=prefer_last_scraped))
        deduped_rows.append(selected)
        skipped += len(group_rows) - 1

    return deduped_rows, skipped


def _print_duplicate_unique_values(table_name: str, rows: list[dict], unique_keys: list[list[str]]) -> None:
    for key_columns in unique_keys:
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for row in rows:
            groups[tuple(row.get(column) for column in key_columns)].append(row)
        duplicates = {
            table_name: [
                f"{'|'.join(key_columns)}={key} ({len(group_rows)} rows)"
                for key, group_rows in groups.items()
                if len(group_rows) > 1 and all(value is not None for value in key)
            ]
        }
        _format_grouped_dict(f"Duplicate SQLite values for {table_name} unique key {','.join(key_columns)}:", duplicates if duplicates[table_name] else {})


def _insert_sql_for_table(table_name: str, columns: list[str], conflict_target: list[str]) -> str:
    insert_columns = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    conflict_sql = ", ".join(conflict_target)
    update_columns = [column for column in columns if column not in conflict_target]
    if update_columns:
        update_sql = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
        return f"INSERT INTO {table_name} ({insert_columns}) VALUES ({placeholders}) ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}"
    return f"INSERT INTO {table_name} ({insert_columns}) VALUES ({placeholders}) ON CONFLICT ({conflict_sql}) DO NOTHING"


def _select_table_rows(table_name: str, columns: list[str], rows: list[dict]) -> list[dict]:
    if table_name == "scraped_jobs":
        return rows
    return rows


def _print_schema_diff(label: str, missing: dict[str, list[str]]) -> None:
    print(label)
    if not missing:
        print("  none")
        return
    for table_name, columns in missing.items():
        print(f"  {table_name}: {', '.join(columns)}")


def _preflight_schema(source_conn: sqlite3.Connection, target_conn) -> None:
    table_names = _list_sqlite_tables(source_conn)
    source_columns = _sqlite_column_map(source_conn, table_names)
    dest_columns = _postgres_column_map(target_conn, table_names)
    missing_before = _missing_columns(source_columns, dest_columns)
    _print_schema_diff("Missing PostgreSQL columns before init_db():", missing_before)

    init_db()

    dest_columns_after = _postgres_column_map(target_conn, table_names)
    missing_after = _missing_columns(source_columns, dest_columns_after)
    _print_schema_diff("Missing PostgreSQL columns after init_db():", missing_after)

    if missing_after:
        raise RuntimeError("PostgreSQL schema is still missing required columns after init_db().")


def _preflight_unique_constraints(source_conn: sqlite3.Connection, target_conn, table_names: list[str]) -> None:
    print("PostgreSQL unique constraints and indexes:")
    for table_name in table_names:
        unique_keys = _postgres_unique_keys(target_conn, table_name)
        if not unique_keys:
            print(f"  {table_name}: none")
            continue
        print(f"  {table_name}: " + "; ".join(", ".join(key) for key in unique_keys))

    print("SQLite duplicate unique-key values:")
    for table_name in table_names:
        rows = _fetch_rows(source_conn, table_name, _table_columns(source_conn, table_name))
        for key_columns in _sqlite_unique_keys(source_conn, table_name):
            groups: dict[tuple, list[dict]] = defaultdict(list)
            for row in rows:
                groups[tuple(row.get(column) for column in key_columns)].append(row)
            duplicates = [key for key, group_rows in groups.items() if len(group_rows) > 1 and all(value is not None for value in key)]
            if duplicates:
                print(f"  {table_name} ({', '.join(key_columns)}): {duplicates}")


def _unique_conflict_target(table_name: str) -> list[str]:
    return {
        "users": ["email"],
        "candidates": ["id"],
        "scraped_jobs": ["url"],
        "match_cache": ["job_id", "candidate_id"],
        "audit_logs": ["id"],
        "jobs": ["job_id"],
    }.get(table_name, ["id"])


def _fetch_rows(conn: sqlite3.Connection, table_name: str, columns: Iterable[str]) -> list[dict]:
    column_sql = ", ".join(columns)
    cursor = conn.cursor()
    cursor.execute(f"SELECT {column_sql} FROM {table_name}")
    return [dict(row) for row in cursor.fetchall()]


def _ensure_target_table_exists(pg_conn, table_name: str, columns: list[str]) -> None:
    cursor = pg_conn.cursor()
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = CURRENT_SCHEMA()
          AND table_name = %s
        """,
        (table_name,),
    )
    if cursor.fetchone():
        return
    raise RuntimeError(f"Target PostgreSQL table does not exist: {table_name}")


def _build_upsert_sql(table_name: str, columns: list[str], pk_columns: list[str]) -> str:
    insert_columns = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))

    if pk_columns:
        update_columns = [column for column in columns if column not in pk_columns]
        if update_columns:
            update_sql = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
            conflict_target = ", ".join(pk_columns)
            return (
                f"INSERT INTO {table_name} ({insert_columns}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_target}) DO UPDATE SET {update_sql}"
            )
        conflict_target = ", ".join(pk_columns)
        return f"INSERT INTO {table_name} ({insert_columns}) VALUES ({placeholders}) ON CONFLICT ({conflict_target}) DO NOTHING"

    return f"INSERT INTO {table_name} ({insert_columns}) VALUES ({placeholders})"


def _set_sequence_value(pg_conn, table_name: str) -> None:
    cursor = pg_conn.cursor()
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = CURRENT_SCHEMA()
          AND table_name = %s
          AND column_default LIKE 'nextval%%'
        ORDER BY ordinal_position
        """,
        (table_name,),
    )
    identity_rows = cursor.fetchall()
    if not identity_rows:
        return

    seq_column = identity_rows[0]["column_name"]
    cursor.execute(f"SELECT COALESCE(MAX({seq_column}), 0) AS max_value FROM {table_name}")
    max_value_row = cursor.fetchone()
    max_value = max_value_row["max_value"] if isinstance(max_value_row, dict) else max_value_row[0]
    cursor.execute(
        """
        SELECT pg_get_serial_sequence(%s, %s)
        """,
        (table_name, seq_column),
    )
    seq_name_row = cursor.fetchone()
    if not seq_name_row:
        return

    seq_name = seq_name_row.get("pg_get_serial_sequence") if isinstance(seq_name_row, dict) else seq_name_row[0]
    if not seq_name:
        return

    cursor.execute("SELECT setval(%s, %s, %s)", (seq_name, int(max_value), True))


def migrate() -> dict[str, dict[str, int]]:
    source_conn = _get_sqlite_connection()
    target_conn = get_connection()
    table_names = _list_sqlite_tables(source_conn)
    summary: dict[str, dict[str, int]] = {}

    try:
        _preflight_schema(source_conn, target_conn)
        _preflight_unique_constraints(source_conn, target_conn, table_names)
        for table_name in table_names:
            columns = _table_columns(source_conn, table_name)
            pk_columns = _table_primary_key_columns(source_conn, table_name)
            rows = _fetch_rows(source_conn, table_name, columns)
            unique_keys = _sqlite_unique_keys(source_conn, table_name)
            conflict_target = _unique_conflict_target(table_name)

            duplicate_count = 0
            for key_columns in unique_keys:
                groups: dict[tuple, list[dict]] = defaultdict(list)
                for row in rows:
                    groups[tuple(row.get(column) for column in key_columns)].append(row)
                duplicate_count += sum(len(group_rows) - 1 for group_rows in groups.values() if len(group_rows) > 1)
            _print_duplicate_unique_values(table_name, rows, unique_keys)

            deduped_rows = rows
            if table_name == "scraped_jobs":
                deduped_rows, skipped_by_url = _dedupe_rows(rows, ["url"], pk_columns, prefer_last_scraped=True)
                duplicate_count = max(duplicate_count, skipped_by_url)
            elif conflict_target:
                deduped_rows, skipped = _dedupe_rows(rows, conflict_target, pk_columns)
                duplicate_count = max(duplicate_count, skipped)

            _ensure_target_table_exists(target_conn, table_name, columns)
            upsert_sql = _insert_sql_for_table(table_name, columns, conflict_target)

            inserted = 0
            updated = 0
            target_cursor = target_conn.cursor()
            try:
                for row in deduped_rows:
                    if len(conflict_target) == 1:
                        select_sql = f"SELECT 1 FROM {table_name} WHERE {conflict_target[0]} = %s"
                    else:
                        select_sql = f"SELECT 1 FROM {table_name} WHERE ({', '.join(conflict_target)}) = ({', '.join(['%s'] * len(conflict_target))})"
                    target_cursor.execute(select_sql, [row[column] for column in conflict_target])
                    existed_before = target_cursor.fetchone() is not None
                    target_cursor.execute(upsert_sql, [row[column] for column in columns])
                    if existed_before:
                        updated += 1
                    else:
                        inserted += 1
                target_conn.commit()
            except Exception:
                target_conn.rollback()
                raise

            if pk_columns and len(pk_columns) == 1:
                try:
                    _set_sequence_value(target_conn, table_name)
                    target_conn.commit()
                except Exception:
                    target_conn.rollback()

            cursor = target_conn.cursor()
            cursor.execute(f"SELECT COUNT(*) AS count FROM {table_name}")
            final_row = cursor.fetchone()
            final_count = final_row["count"] if isinstance(final_row, dict) else final_row[0]
            summary[table_name] = {
                "source_row_count": len(rows),
                "duplicate_rows_skipped": duplicate_count,
                "inserted_rows": inserted,
                "updated_rows": updated,
                "final_postgres_row_count": int(final_count or 0),
            }
    finally:
        source_conn.close()
        target_conn.close()

    return summary


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL must be set before running the migration.")

    summary = migrate()
    print("Migration summary:")
    for table_name, counts in summary.items():
        print(
            f"{table_name}: source={counts['source_row_count']} skipped={counts['duplicate_rows_skipped']} "
            f"inserted={counts['inserted_rows']} updated={counts['updated_rows']} final_pg={counts['final_postgres_row_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
