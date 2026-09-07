#!/usr/bin/env python3
"""Dump the topdown lap records so the game server can announce them.

The topdown server posts the record for the upcoming track/car pairing into the
lobby before every qualifying. It has no database access, and event init must
never block on a query, so the record list is written here — right after new
topdown races were loaded — next to the web config the server already reads.

Only human laps count: a bot record is nothing a player can go and beat.
Keyed by GUID, because that is what the heat plan carries and what survives a
track being renamed.

Usage:
    python dump_topdown_records.py [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

DEFAULT_OUT = "/srv/tsura/server_config/topdown_records.json"

QUERY = """
    SELECT DISTINCT ON (track_guid, vehicle_guid)
           track_guid,
           vehicle_guid,
           track_name,
           vehicle_name,
           fastest_lap,
           driver_name,
           steam_id,
           utc_start_time
      FROM mart.v_race_results
     WHERE server = 'topdown'
       AND is_ai = false
       AND fastest_lap IS NOT NULL
       AND fastest_lap > 0
  ORDER BY track_guid, vehicle_guid, fastest_lap;
"""


def fetch_records(db_url: str) -> dict:
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(QUERY)
        rows = cur.fetchall()

    records = {}
    for (track_guid, vehicle_guid, track_name, vehicle_name,
         fastest_lap, driver_name, steam_id, started) in rows:
        records[f"{track_guid}|{vehicle_guid}"] = {
            "track": track_name,
            "vehicle": vehicle_name,
            "lap": round(float(fastest_lap), 4),
            "driver": driver_name,
            "steam_id": str(steam_id) if steam_id is not None else None,
            "driven_at": started.isoformat() if started else None,
        }
    return records


def write_atomic(path: str, payload: dict) -> None:
    """Replace `path` in one step — the server may read it at any moment."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".topdown_records.",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        # The game server user reaches the file through the tsu group, the same
        # way it reaches the panel configs in this directory.
        subprocess.run(["chgrp", "tsu", tmp], check=False)
        os.chmod(tmp, 0o664)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help=f"target file (default: {DEFAULT_OUT})")
    args = parser.parse_args()

    db_url = os.getenv("TSU_PROD_POSTGRES_URL")
    if not db_url:
        print("[ERROR] TSU_PROD_POSTGRES_URL not set", file=sys.stderr)
        return 1

    records = fetch_records(db_url)
    write_atomic(args.out, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    })
    print(f"[OK] {len(records)} topdown records -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
