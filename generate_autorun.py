#!/usr/bin/env python3
"""generate_autorun.py

Generate or update the **`autorun.src`** file for the Turbo Sliders Unlimited
hot-lapping dedicated server. The file is broadcast by the game server to show
the current Top-10 lap times plus all drivers who are presently connected.

Key features
------------
* Reads an `event.json` (path given as positional CLI argument).
* Extracts the **Steam IDs of currently connected players**.
* Queries PostgreSQL for the latest hotlapping session group:
  * Always ranks the full group first, **then** filters to `Top-10 OR active` —
    so positions stay consistent.
* Formats lap times as `M:SS.FFFF` and gaps as `+SS.FFFF` (empty for P1).
* Pads every driver name to the **longest name length + 3 spaces**.
* Writes one `/broadcast …` line per result into `autorun.src` (overwriting),
  sets group ownership to **tsu** and permissions to `664`.
* Database connection via `TSU_PROD_POSTGRES_URL` from `.env` or environment.

Usage
~~~~~
```bash
python generate_autorun.py /path/to/event.json \
       --autorun-path /home/hotlapping/server/config/Scripts/autorun.src
```

Dependencies: psycopg[binary], python-dotenv (both in tsu_pipeline pyproject.toml)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Set, Tuple

import psycopg
from dotenv import load_dotenv

# Load .env from this script's directory (tsu_pipeline root)
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# SQL: best lap per driver in the current (latest) hotlap session group
# ---------------------------------------------------------------------------

SQL = """
WITH current_group AS (
    SELECT group_id
    FROM mart.v_hotlap_grouped_sessions
    ORDER BY session_end DESC
    LIMIT 1
),
ranked AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY r.lap_time)    AS pos,
        r.steam_id,
        r.driver_name,
        r.vehicle_name,
        r.lap_time,
        r.lap_time - MIN(r.lap_time) OVER ()       AS diff_to_best
    FROM mart.v_hotlap_group_results r
    JOIN current_group cg ON r.group_id = cg.group_id
    WHERE r.is_best_lap = true
)
SELECT
    pos,
    steam_id,
    driver_name,
    vehicle_name,
    lap_time,
    CASE WHEN pos = 1 THEN NULL ELSE diff_to_best END AS diff_to_best
FROM ranked
WHERE pos <= 10
   OR steam_id = ANY(%(active_ids)s)
ORDER BY pos;
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def format_lap(t_seconds: float) -> str:
    minutes = int(t_seconds // 60)
    seconds = t_seconds - minutes * 60
    return f"{minutes}:{seconds:07.4f}"


def format_diff(diff: float | None) -> str:
    if diff is None:
        return ""
    return f"+{diff:06.4f}"


def extract_active_ids(event_json_path: str) -> Set[int]:
    with open(event_json_path, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    return {p["player"]["id"] for p in data.get("players", [])}


def fetch_results(active_ids: Set[int], db_url: str | None) -> List[Tuple]:
    if not active_ids:
        active_ids = {-1}  # keep ANY() valid
    conn = psycopg.connect(db_url) if db_url else psycopg.connect()
    with conn:
        with conn.cursor() as cur:
            cur.execute(SQL, {"active_ids": list(active_ids)})
            return cur.fetchall()


def build_broadcast_lines(rows: List[Tuple]) -> List[str]:
    # Deduplicate (top-10 entry has priority, active players appended after)
    unique: List[Tuple] = []
    seen: Set[int] = set()
    for row in rows:
        if row[1] not in seen:
            unique.append(row)
            seen.add(row[1])

    max_name_len = max((len(r[2]) for r in unique), default=0)
    pad_width = max_name_len + 3

    lines = ["/broadcast ### Current Hotlapping Results:"]
    for pos, _sid, name, vehicle, lap, diff in unique:
        lap_fmt = format_lap(lap)
        diff_fmt = format_diff(diff)
        space = " " if diff_fmt else ""
        lines.append(
            f"/broadcast {pos}. {name.ljust(pad_width)}{lap_fmt}{space}{diff_fmt} with {vehicle}"
        )
    return lines


def write_autorun(lines: List[str], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    try:
        subprocess.run(["chgrp", "tsu", path], check=True)
        subprocess.run(["chmod", "664", path], check=True)
    except subprocess.CalledProcessError as exc:
        print(f"[WARN] Could not adjust group/permissions: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create autorun.src with current hotlapping Top-10."
    )
    parser.add_argument("event_json", help="Path to event.json (for active player IDs)")
    parser.add_argument(
        "--autorun-path",
        default="/home/hotlapping/server/config/Scripts/autorun.src",
        help="Output path for autorun.src (default: %(default)s)",
    )
    args = parser.parse_args()

    db_url = os.getenv("TSU_PROD_POSTGRES_URL")
    if not db_url:
        print("[ERROR] TSU_PROD_POSTGRES_URL not set", file=sys.stderr)
        sys.exit(1)

    active_ids = extract_active_ids(args.event_json)
    rows = fetch_results(active_ids, db_url)

    if not rows:
        print("[WARN] No hotlap results found — autorun.src not updated", file=sys.stderr)
        sys.exit(0)

    lines = build_broadcast_lines(rows)
    write_autorun(lines, args.autorun_path)
    print(f"[OK] autorun.src updated → {args.autorun_path} ({len(lines)-1} entries)")


if __name__ == "__main__":
    main()
