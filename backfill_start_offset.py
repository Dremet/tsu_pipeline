"""
backfill_start_offset.py

Backfills race_start_offset_s for existing base.race_sessions rows
by reading the original event JSON files.

The offset is taken from checkpointTimes[0]["times"][0] / 10000.0 of any
human player — this is the raw game-clock second at which the race started.
mart.v_race_results subtracts this from finish_time to give the net race
duration.

Usage:
    uv run python backfill_start_offset.py               # dry-run (shows counts)
    uv run python backfill_start_offset.py --apply       # writes to DB
    uv run python backfill_start_offset.py --prod        # uses TSU_PROD_POSTGRES_URL
    uv run python backfill_start_offset.py --prod --apply
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import psycopg

load_dotenv()

# Add package to path so we can import keys
sys.path.insert(0, str(Path(__file__).parent))
from tsu_pipeline.keys import session_id as make_session_id


DATA_ROOTS = [
    Path("/home/data/events/archive"),
    Path("/home/data/history_triple_heat_hammock"),
    Path("/home/data/tripleheat"),
    Path("/home/data/heats"),
]


def get_start_offset(data: dict) -> float | None:
    """Extract race_start_offset_s from a parsed event JSON dict."""
    player_stats_list = data.get("raceStats", {}).get("playerStats", [])
    for i, player in enumerate(data.get("players", [])):
        if player.get("player", {}).get("ai"):
            continue
        if i < len(player_stats_list):
            ct = player_stats_list[i].get("checkpointTimes", [])
            if ct and ct[0].get("times"):
                return ct[0]["times"][0] / 10000.0
    return None


def run(db_url: str, dry_run: bool) -> None:
    updated = 0
    skipped_no_data = 0
    skipped_not_in_db = 0
    skipped_already_set = 0
    errors = 0

    with psycopg.connect(db_url) as conn:
        for root in DATA_ROOTS:
            if not root.exists():
                continue
            for json_path in sorted(root.rglob("*_event.json")):
                try:
                    raw = json_path.read_text(encoding="utf-8", errors="replace")
                    data = json.loads(raw)
                except Exception:
                    errors += 1
                    continue

                if data is None or "raceStats" not in data:
                    continue

                try:
                    sid = make_session_id(data["utcStartTime"], data["host"])
                except Exception:
                    errors += 1
                    continue

                offset = get_start_offset(data)
                if offset is None:
                    skipped_no_data += 1
                    continue

                row = conn.execute(
                    "SELECT race_start_offset_s FROM base.race_sessions WHERE id = %s",
                    (sid,),
                ).fetchone()

                if row is None:
                    skipped_not_in_db += 1
                    continue
                if row[0] is not None:
                    skipped_already_set += 1
                    continue

                if dry_run:
                    print(f"  Would set {sid[:12]}… offset={offset:.4f}s")
                else:
                    conn.execute(
                        """
                        UPDATE base.race_sessions
                           SET race_start_offset_s = %s
                         WHERE id = %s AND race_start_offset_s IS NULL
                        """,
                        (offset, sid),
                    )
                updated += 1

        if not dry_run:
            conn.commit()

    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"\n[{mode}]")
    print(f"  Updated:          {updated}")
    print(f"  Already set:      {skipped_already_set}")
    print(f"  No checkpoint data: {skipped_no_data}")
    print(f"  Session not in DB:  {skipped_not_in_db}")
    print(f"  Errors:           {errors}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    parser.add_argument("--prod",  action="store_true", help="Use TSU_PROD_POSTGRES_URL")
    args = parser.parse_args()

    url_key = "TSU_PROD_POSTGRES_URL" if args.prod else "TSU_TEST_POSTGRES_URL"
    db_url = os.environ.get(url_key)
    if not db_url:
        print(f"ERROR: {url_key} not set in environment.", file=sys.stderr)
        sys.exit(1)

    print(f"DB: {url_key}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}\n")
    run(db_url, dry_run=not args.apply)


if __name__ == "__main__":
    main()
