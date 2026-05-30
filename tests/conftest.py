"""
Shared fixtures for tsu_pipeline tests.

Each test runs with a dedicated connection whose transaction is always
rolled back on teardown (no state leaks between tests).

The session-scoped `prepare_db` fixture creates the schema once and
truncates all data tables, so each test session starts from an empty slate.
"""

import os
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")

DB_URL = os.environ["TSU_TEST_POSTGRES_URL"]
_MIGRATIONS = [
    Path(__file__).parents[1] / "migrations" / "001_base_schema.sql",
    Path(__file__).parents[1] / "migrations" / "002_elo_bootstrap.sql",
    Path(__file__).parents[1] / "migrations" / "003_mart_views.sql",
]

_DATA_TABLES = [
    "base.elo_history",
    "base.elo_bootstrap",
    "base.hotlap_laps",
    "base.hotlap_events",
    "base.race_participations",
    "base.race_sessions",
    "base.vehicles",
    "base.tracks",
    "base.drivers",
]


@pytest.fixture(scope="session", autouse=True)
def prepare_db():
    """Apply all migrations (idempotent) and truncate data tables once per session."""
    with psycopg.connect(DB_URL, autocommit=True) as c:
        for migration in _MIGRATIONS:
            c.execute(migration.read_text())
        c.execute(f"TRUNCATE {', '.join(_DATA_TABLES)} CASCADE")


@pytest.fixture
def conn():
    """
    Per-test DB cursor inside a transaction that is always rolled back.
    Tests can read and write freely; nothing persists after the test.
    """
    connection = psycopg.connect(DB_URL)
    cursor = connection.cursor()
    try:
        yield cursor
    finally:
        connection.rollback()
        cursor.close()
        connection.close()
