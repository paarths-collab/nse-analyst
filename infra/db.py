from __future__ import annotations

import pathlib
from typing import Iterable

import psycopg


def execute_sql_file(database_url: str, sql_path: str) -> None:
    path = pathlib.Path(sql_path)
    if not path.exists():
        raise FileNotFoundError(f"SQL file not found: {sql_path}")

    sql_text = path.read_text(encoding="utf-8")
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql_text)
        conn.commit()


def ensure_schema(database_url: str, migration_files: Iterable[str]) -> None:
    for migration in migration_files:
        execute_sql_file(database_url, migration)


def ping_database(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
