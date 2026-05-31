"""
relabel_casual_heat.py — One-time fix: relabel Casual-Heat sessions in the DB.

Background:
  /home/data/heats/archive/ contains both Tripleheat (Fridays) and Casual-Heat
  (Tuesdays / Wednesdays) files. The cron pipeline loaded all of them with
  server='heats'. This script identifies the Casual-Heat sessions by computing
  their session IDs from the archive files and updates them to server='casual_heat'.

  Friday sessions in the archive overlap with history_triple_heat_hammock and were
  already loaded correctly (ON CONFLICT DO NOTHING = no change needed).
  Tuesday/Wednesday sessions are uniquely Casual-Heat.

Usage:
  uv run python relabel_casual_heat.py          # dry-run: show counts only
  uv run python relabel_casual_heat.py --apply  # write to TSU_PROD_POSTGRES_URL
  uv run python relabel_casual_heat.py --test   # write to TSU_TEST_POSTGRES_URL
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv
import os

load_dotenv()

ARCHIVE_PATH = Path("/home/data/heats/archive")
CASUAL_DAYS = {1, 2, 3}  # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
# Tripleheat ran Tue historically, but archive Tuesdays from Nov 2025+ are Casual-Heat.
# The day-of-week in the FOLDER NAME is used (Berlin local time).
# Fridays (4) are Tripleheat and already correctly labeled — skip them.
# Saturdays/Sundays are edge cases — skip (leave as 'heats').


def _session_id(utc_start_time: str, host: int) -> str:
    return hashlib.md5(f"{utc_start_time}|{host}".encode()).hexdigest()


def _folder_weekday(folder_name: str) -> int:
    """Return 0=Mon … 6=Sun for the YYYYMMDD prefix of a folder name."""
    date_str = folder_name[:8]
    dt = datetime(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
    return dt.weekday()


def collect_casual_session_ids(archive_path: Path) -> list[str]:
    """Compute session IDs for all Casual-Heat files (Tue/Wed folders)."""
    ids = []
    skipped_dirs = 0
    total_dirs = 0

    for folder in sorted(archive_path.iterdir()):
        if not folder.is_dir():
            continue
        total_dirs += 1
        wday = _folder_weekday(folder.name)
        if wday not in CASUAL_DAYS:
            skipped_dirs += 1
            continue

        for json_file in folder.rglob("*_event.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data is None or "raceStats" not in data:
                continue
            if data["raceStats"].get("hotlapping", False):
                continue
            utc_start = data.get("utcStartTime", "")
            host = data.get("host", 0)
            if utc_start and host:
                ids.append(_session_id(utc_start, host))

    print(f"Archive: {total_dirs} dirs total, {skipped_dirs} skipped (non-Tue/Wed)")
    return ids


def main():
    parser = argparse.ArgumentParser(description="Relabel Casual-Heat sessions")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="Write to TSU_PROD_POSTGRES_URL")
    group.add_argument("--test", action="store_true", help="Write to TSU_TEST_POSTGRES_URL")
    args = parser.parse_args()

    if args.apply:
        db_url = os.environ["TSU_PROD_POSTGRES_URL"]
        target = "PRODUCTION"
    elif args.test:
        db_url = os.environ["TSU_TEST_POSTGRES_URL"]
        target = "TEST"
    else:
        db_url = None
        target = "DRY-RUN"

    print(f"Mode: {target}")
    print(f"Archive: {ARCHIVE_PATH}")
    print()

    session_ids = collect_casual_session_ids(ARCHIVE_PATH)
    print(f"Casual-Heat session IDs found in archive: {len(session_ids)}")

    if not session_ids:
        print("Nothing to do.")
        return

    if db_url is None:
        print("\n(Dry-run: use --apply or --test to write changes)")
        return

    with psycopg.connect(db_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            # Count how many of these IDs are currently labeled 'heats'
            cur.execute(
                "SELECT COUNT(*) FROM base.race_sessions WHERE id = ANY(%s) AND server = 'heats'",
                (session_ids,),
            )
            before_count = cur.fetchone()[0]
            print(f"Sessions in DB with server='heats' matching IDs: {before_count}")

            if before_count == 0:
                print("Nothing to update — already relabeled or not loaded.")
                return

            cur.execute(
                "UPDATE base.race_sessions SET server = 'casual_heat' WHERE id = ANY(%s) AND server = 'heats'",
                (session_ids,),
            )
            updated = cur.rowcount
            print(f"Updated {updated} sessions → server='casual_heat'")

        conn.commit()
        print("Committed.")


if __name__ == "__main__":
    main()
