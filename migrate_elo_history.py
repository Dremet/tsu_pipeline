#!/usr/bin/env python3
"""
migrate_elo_history.py — Import legacy ELO history from racing-DB into test-DB.

Reads:  OLD_RACING_POSTGRES_URL  →  tsu.elo_heat + tsu.drivers  (read-only)
Writes: TSU_TEST_POSTGRES_URL    →  base.drivers + base.elo_bootstrap

Usage:
    uv run migrate_elo_history.py           # dry-run (print only, no writes)
    uv run migrate_elo_history.py --apply   # write to test DB

This script is safe to run multiple times (idempotent upserts).
It does NOT write to the production TSU_HOTLAPPING_POSTGRES_URL.
"""

import argparse
import os
import sys
from datetime import timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

RACING_URL = os.environ.get("OLD_RACING_POSTGRES_URL")
TEST_URL = os.environ.get("TSU_TEST_POSTGRES_URL")
BOOTSTRAP_SQL = (Path(__file__).parent / "migrations" / "002_elo_bootstrap.sql").read_text()


# ── read from racing DB ───────────────────────────────────────────────────────

def fetch_racing_data() -> list[dict]:
    """
    Returns list of dicts with the LATEST ELO per driver from tsu.elo_heat.
    Joins tsu.drivers to get steam_id, name, flag, clan.
    """
    with psycopg.connect(RACING_URL) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                d.steam_id,
                d.name,
                d.clan,
                d.flag,
                latest.value        AS elo_value,
                latest.number_races,
                latest.last_timestamp AS last_race_at
            FROM tsu.drivers d
            JOIN LATERAL (
                SELECT value, number_races, last_timestamp
                FROM tsu.elo_heat
                WHERE driver_id = d.id
                ORDER BY created_at DESC
                LIMIT 1
            ) latest ON true
            ORDER BY latest.value DESC
            """
        )
        return [
            {
                "steam_id": row[0],
                "name": row[1],
                "clan": row[2] or None,
                "flag": row[3] or None,
                "elo_value": row[4],
                "number_races": row[5],
                # racing-DB stores last_timestamp as timestamp WITHOUT timezone,
                # but the value is UTC (matches utcStartTime in JSON files).
                # Mark it explicitly so PostgreSQL doesn't misinterpret it as
                # local time (Europe/Berlin) when inserting into TIMESTAMPTZ.
                "last_race_at": row[6].replace(tzinfo=timezone.utc) if row[6] else None,
            }
            for row in cur.fetchall()
        ]


def fetch_racing_summary() -> dict:
    """Return high-level stats about the racing DB ELO data."""
    with psycopg.connect(RACING_URL) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                COUNT(*) AS total_entries,
                COUNT(DISTINCT driver_id) AS unique_drivers,
                MIN(created_at) AS oldest_entry,
                MAX(created_at) AS newest_entry,
                COUNT(DISTINCT DATE_TRUNC('week', created_at)) AS race_weeks
            FROM tsu.elo_heat
            """
        )
        row = cur.fetchone()
        return {
            "total_entries": row[0],
            "unique_drivers": row[1],
            "oldest_entry": row[2],
            "newest_entry": row[3],
            "race_weeks": row[4],
        }


# ── write to test DB ──────────────────────────────────────────────────────────

def apply_migration(records: list[dict], *, dry_run: bool) -> None:
    if dry_run:
        print("[DRY-RUN] Would write to TSU_TEST_POSTGRES_URL — no DB changes.")
        return

    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        # Ensure bootstrap table exists
        conn.execute(BOOTSTRAP_SQL)

    with psycopg.connect(TEST_URL) as conn:
        cur = conn.cursor()
        drivers_upserted = 0
        bootstrap_upserted = 0

        for r in records:
            # Upsert driver (name/flag/clan may have changed)
            cur.execute(
                """
                INSERT INTO base.drivers (steam_id, name, flag, clan)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (steam_id) DO UPDATE
                    SET name       = EXCLUDED.name,
                        flag       = EXCLUDED.flag,
                        clan       = EXCLUDED.clan,
                        updated_at = now()
                """,
                (r["steam_id"], r["name"], r["flag"], r["clan"]),
            )
            drivers_upserted += 1

            # Upsert bootstrap ELO
            cur.execute(
                """
                INSERT INTO base.elo_bootstrap
                    (steam_id, elo_value, number_races, last_race_at, source)
                VALUES (%s, %s, %s, %s, 'racing_db_migration')
                ON CONFLICT (steam_id) DO UPDATE
                    SET elo_value    = EXCLUDED.elo_value,
                        number_races = EXCLUDED.number_races,
                        last_race_at = EXCLUDED.last_race_at,
                        imported_at  = now()
                """,
                (r["steam_id"], r["elo_value"], r["number_races"], r["last_race_at"]),
            )
            bootstrap_upserted += 1

        print(f"  Drivers upserted:    {drivers_upserted}")
        print(f"  Bootstrap upserted:  {bootstrap_upserted}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to TSU_TEST_POSTGRES_URL (default: dry-run only)",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    if not RACING_URL:
        print("ERROR: OLD_RACING_POSTGRES_URL not set in .env", file=sys.stderr)
        sys.exit(1)
    if not TEST_URL:
        print("ERROR: TSU_TEST_POSTGRES_URL not set in .env", file=sys.stderr)
        sys.exit(1)

    print("=== racing-DB summary ===")
    summary = fetch_racing_summary()
    for k, v in summary.items():
        print(f"  {k:20s}: {v}")

    print()
    print("=== Fetching latest ELO per driver ===")
    records = fetch_racing_data()
    print(f"  Drivers with ELO: {len(records)}")
    print()

    print("=== Top 10 by ELO ===")
    print(f"  {'Name':15s} {'steam_id':18s} {'ELO':>10s} {'races':>6s} {'last_race':12s}")
    print("  " + "-" * 70)
    for r in records[:10]:
        last = str(r["last_race_at"])[:10] if r["last_race_at"] else "-"
        print(f"  {r['name']:15s} {r['steam_id']:<18d} {r['elo_value']:>10.1f} "
              f"{r['number_races']:>6d} {last:12s}")

    print()
    print("=== ELO distribution ===")
    buckets = [
        (">1400", sum(1 for r in records if r["elo_value"] > 1400)),
        ("1200-1400", sum(1 for r in records if 1200 <= r["elo_value"] <= 1400)),
        ("1000-1200", sum(1 for r in records if 1000 <= r["elo_value"] < 1200)),
        ("800-1000", sum(1 for r in records if 800 <= r["elo_value"] < 1000)),
        ("<800", sum(1 for r in records if r["elo_value"] < 800)),
    ]
    for label, count in buckets:
        bar = "█" * count
        print(f"  {label:10s}: {count:3d}  {bar}")

    print()
    if dry_run:
        print("[DRY-RUN] Would INSERT/UPSERT the above into TSU_TEST_POSTGRES_URL.")
        print("          Run with --apply to execute.")
    else:
        print("=== Applying to TSU_TEST_POSTGRES_URL ===")
        apply_migration(records, dry_run=False)
        print("Done.")


if __name__ == "__main__":
    main()
