"""One-time migration from the local SQLite database into PostgreSQL.

Run explicitly:
    python -m backend.migrate_sqlite_to_postgres
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable

from .database import get_connection


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
        for table_name in table_names:
            columns = _table_columns(source_conn, table_name)
            pk_columns = _table_primary_key_columns(source_conn, table_name)
            rows = _fetch_rows(source_conn, table_name, columns)

            _ensure_target_table_exists(target_conn, table_name, columns)
            upsert_sql = _build_upsert_sql(table_name, columns, pk_columns)

            migrated = 0
            target_cursor = target_conn.cursor()
            try:
                for row in rows:
                    target_cursor.execute(upsert_sql, [row[column] for column in columns])
                    migrated += 1
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

            summary[table_name] = {
                "sqlite_rows": len(rows),
                "migrated_rows": migrated,
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
        print(f"{table_name}: migrated={counts['migrated_rows']} sqlite_rows={counts['sqlite_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
