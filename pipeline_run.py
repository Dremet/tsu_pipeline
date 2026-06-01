#!/usr/bin/env python3
"""
pipeline_run.py — single-folder pipeline step, called by run_pipeline.sh.

Usage:
    uv run python pipeline_run.py <type> <raw_path>

    type:     hotlapping | events | heats
    raw_path: path to the /raw/ subfolder containing *_event.json files

Reads DB_URL from environment (TSU_PROD_POSTGRES_URL or DATABASE_URL).

Exit codes:
    0 — success (including all-skipped, which is OK)
    1 — one or more load errors
"""

import os
import sys
from pathlib import Path

import psycopg

from tsu_pipeline.batch import load_folder
from tsu_pipeline.elo import update_elo


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <type> <raw_path>", file=sys.stderr)
        sys.exit(1)

    server = sys.argv[1]
    raw_path = sys.argv[2]

    db_url = os.environ.get("TSU_PROD_POSTGRES_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: TSU_PROD_POSTGRES_URL or DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    if not Path(raw_path).is_dir():
        print(f"ERROR: raw_path does not exist: {raw_path}", file=sys.stderr)
        sys.exit(1)

    result = load_folder(raw_path, server, db_url)
    print(
        f"[pipeline] {server} {raw_path}: "
        f"loaded={result['loaded']} skipped={result['skipped']} errors={result['errors']}"
    )

    if result["errors"]:
        for f in result.get("error_files", []):
            print(f"  ERROR: {f}", file=sys.stderr)
        sys.exit(1)

    # ELO update for tripleheat — idempotent, bootstrap cutoff (or -infinity) prevents double-counting
    if server == "tripleheat" and result["sessions_new"] > 0:
        with psycopg.connect(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT rs.id FROM base.race_sessions rs
                WHERE rs.server = %s
                  AND rs.utc_start_time > COALESCE(
                      (SELECT MAX(last_race_at) FROM base.elo_bootstrap),
                      '-infinity'::timestamptz
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM base.elo_history eh
                      JOIN base.race_participations rp ON rp.id = eh.participation_id
                      WHERE rp.session_id = rs.id
                  )
                ORDER BY rs.utc_start_time
                """,
                (server,),
            )
            pending = [row[0] for row in cur.fetchall()]
            if pending:
                inserted = update_elo(pending, cur, server=server)
                print(f"[pipeline] ELO: {inserted} new entries for {len(pending)} sessions")
            else:
                print("[pipeline] ELO: no new sessions to process")


if __name__ == "__main__":
    main()
