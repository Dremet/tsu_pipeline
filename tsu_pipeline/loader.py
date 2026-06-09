"""
load_event(json_path, server, conn) — main entry point.

Parses a TSU event JSON file, validates it, and writes the result into
the base.* tables using the provided psycopg3 connection.  The caller
owns transaction management (conn should be inside an open transaction
or the function can be called inside a `with conn.transaction()` block).

Returns a dict:
    {
        "skipped": bool,
        "skip_reason": str | None,
        "sessions": int,           # 1 or 0
        "participations": int,
        "drivers_new": int,
        "laps": int,               # hotlap only
    }
"""

import json
from pathlib import Path

from .keys import (
    session_id as make_session_id,
    hotlap_event_id as make_hotlap_event_id,
    participation_id as make_participation_id,
    bot_participation_id as make_bot_participation_id,
    lap_telemetry_id as make_lap_telemetry_id,
)
from .validate import validate_event, player_has_valid_laps


# ── details.log helpers ──────────────────────────────────────────────────────

def _find_log_path(json_path: Path) -> Path | None:
    """Return the *_event_details.log sibling for a given *_event.json, or None."""
    if json_path.name.endswith("_event.json"):
        log_name = json_path.name[: -len("_event.json")] + "_event_details.log"
        log_path = json_path.parent / log_name
        if log_path.exists():
            return log_path
    return None


def _load_details(
    log_path: Path,
    session_id: str,
    player_map: dict[int, str],
    conn,
) -> dict:
    """
    Parse log_path and insert tire compounds + per-lap telemetry into the DB.
    Idempotent (ON CONFLICT DO NOTHING).

    player_map : player_index (from details.log) → participation_id
    Returns    : {"compounds": int, "laps": int} new rows inserted.
    """
    from .details_parser import parse_details_log

    parsed = parse_details_log(log_path)
    if parsed is None:
        return {"compounds": 0, "laps": 0}

    compounds_inserted = 0
    for idx, c in parsed["compounds"].items():
        conn.execute(
            """
            INSERT INTO base.race_tire_compounds
                (session_id, compound_index, compound_name, max_wear, max_performance)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (session_id, compound_index) DO NOTHING
            """,
            (session_id, idx, c["name"], c["max_wear"], c["max_perf"]),
        )
        if conn.rowcount == 1:
            compounds_inserted += 1

    laps_inserted = 0
    for row in parsed["lap_telemetry"]:
        pid = player_map.get(row["player_index"])
        if pid is None:
            continue  # bot or unknown
        compound_name = parsed["compounds"].get(row["compound_index"], {}).get("name", "Unknown")
        tel_id = make_lap_telemetry_id(pid, row["lap_number"])
        conn.execute(
            """
            INSERT INTO base.race_lap_telemetry
                (id, participation_id, session_id, lap_number, compound_name,
                 tire_wear, fuel_remaining, hit_points, stint_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                tel_id,
                pid,
                session_id,
                row["lap_number"],
                compound_name,
                row["tire_wear"],
                row["fuel_remaining"],
                row["hit_points"],
                row["stint_number"],
            ),
        )
        if conn.rowcount == 1:
            laps_inserted += 1

    return {"compounds": compounds_inserted, "laps": laps_inserted}


# ── helpers ──────────────────────────────────────────────────────────────────

def _read_json(json_path: Path) -> dict:
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)


def _extract_lap_data(data: dict, player_index: int) -> list[dict]:
    """
    Returns a list of dicts with keys: lap_number, lap_time, sector_times.

    lap_time and sector_times are in seconds (raw int ticks ÷ 10000).
    Only includes laps where we have full checkpoint data.
    """
    rs = data["raceStats"]
    rr_entries = {e["playerIndex"]: e for e in rs["raceRanking"]["entries"]}
    rr = rr_entries.get(player_index)
    if not rr:
        return []

    laps_completed = rr.get("lapsCompleted", 0)
    if laps_completed <= 0:
        return []

    player_stats = rs.get("playerStats", [])
    if player_index >= len(player_stats):
        return []

    cp_times = player_stats[player_index].get("checkpointTimes", [])
    # Need laps_completed+1 entries: index 0 is the starting crossing,
    # indices 1..laps_completed are the lap-completion crossings.
    if len(cp_times) < laps_completed + 1:
        return []

    sector_cps = rs["checkpoints"]["sectorToCheckpoint"]  # e.g. [0, 8, 18, 27]

    laps = []
    for k in range(1, laps_completed + 1):
        prev_times = cp_times[k - 1]["times"]
        curr_cp0 = cp_times[k]["times"][0]

        if not prev_times:
            continue

        lap_time = (curr_cp0 - prev_times[0]) / 10000.0

        # Sector times: intra-lap sectors from sector_cps, final sector to next cp0
        sector_times = []
        for s in range(1, len(sector_cps)):
            sc_idx = sector_cps[s]
            sc_prev = sector_cps[s - 1]
            if sc_idx < len(prev_times) and sc_prev < len(prev_times):
                sector_times.append((prev_times[sc_idx] - prev_times[sc_prev]) / 10000.0)
        # Last sector: from last sector checkpoint to end of lap (next cp0 crossing)
        last_sc = sector_cps[-1]
        if last_sc < len(prev_times):
            sector_times.append((curr_cp0 - prev_times[last_sc]) / 10000.0)

        laps.append({
            "lap_number": k,
            "lap_time": lap_time,
            "sector_times": sector_times if sector_times else None,
        })

    return laps


def _upsert_track(conn, data: dict) -> None:
    lvl = data["level"]
    conn.execute(
        """
        INSERT INTO base.tracks (guid, name, level_type, maker_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (guid) DO UPDATE
            SET name       = EXCLUDED.name,
                level_type = EXCLUDED.level_type,
                maker_id   = EXCLUDED.maker_id
        """,
        (lvl["guid"], lvl["name"], lvl.get("levelType"), lvl.get("makerId")),
    )


def _upsert_vehicle(conn, vehicle: dict) -> None:
    conn.execute(
        """
        INSERT INTO base.vehicles (guid, name)
        VALUES (%s, %s)
        ON CONFLICT (guid) DO UPDATE SET name = EXCLUDED.name
        """,
        (vehicle["guid"], vehicle["name"]),
    )


def _upsert_driver(conn, player: dict) -> bool:
    """Returns True if this was a new driver."""
    conn.execute(
        """
        INSERT INTO base.drivers (steam_id, name, flag, clan)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (steam_id) DO UPDATE
            SET name       = EXCLUDED.name,
                flag       = EXCLUDED.flag,
                clan       = EXCLUDED.clan,
                updated_at = now()
        RETURNING (xmax = 0) AS was_inserted
        """,
        (
            player["player"]["id"],
            player["player"]["name"],
            player["player"].get("flag"),
            player["player"].get("clan") or None,
        ),
    )
    row = conn.fetchone()
    return row[0] if row else False


# ── race loader ───────────────────────────────────────────────────────────────

def _load_race(data: dict, server: str, conn, json_path: Path | None = None) -> dict:
    _upsert_track(conn, data)

    drivers_new = 0
    for player in data["players"]:
        _upsert_vehicle(conn, player["vehicle"])
        if not player["player"]["ai"]:
            if _upsert_driver(conn, player):
                drivers_new += 1

    utc_start = data["utcStartTime"]
    host = data["host"]
    sid = make_session_id(utc_start, host)

    conn.execute(
        """
        INSERT INTO base.race_sessions
            (id, utc_start_time, host, track_guid, server, finished_state,
             max_laps, participant_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            sid,
            utc_start,
            host,
            data["level"]["guid"],
            server,
            data["finishedState"],
            data["raceStats"].get("maxLaps"),
            len(data["players"]),
        ),
    )
    session_inserted = conn.rowcount == 1

    # Build position ranking: sort by laps_completed desc, finish_time asc,
    # last_checkpoint desc — matching update_elo.py logic
    rr_entries = sorted(
        data["raceStats"]["raceRanking"]["entries"],
        key=lambda e: (-e["lapsCompleted"], e["time"], -e["lastCheckpoint"]),
    )
    position_map = {e["playerIndex"]: i + 1 for i, e in enumerate(rr_entries)}
    rr_by_idx = {e["playerIndex"]: e for e in rr_entries}

    participations_inserted = 0
    player_map: dict[int, str] = {}  # player_index → participation_id (humans only)

    for i, player in enumerate(data["players"]):
        _upsert_vehicle(conn, player["vehicle"])
        rr = rr_by_idx.get(i, {})
        is_ai = bool(player["player"]["ai"])
        steam_id = None if is_ai else player["player"]["id"]
        bot_name = player["player"]["name"] if is_ai else None
        pid = (
            make_bot_participation_id(sid, i)
            if is_ai
            else make_participation_id(sid, steam_id, player["vehicle"]["guid"])
        )

        if not is_ai:
            player_map[i] = pid

        laps_data = [] if is_ai else _extract_lap_data(data, i)
        fastest_lap = min(lap["lap_time"] for lap in laps_data) if laps_data else None

        conn.execute(
            """
            INSERT INTO base.race_participations
                (id, session_id, steam_id, is_ai, bot_name, vehicle_guid,
                 finish_time, laps_completed, last_checkpoint, position,
                 start_position, fastest_lap)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                pid,
                sid,
                steam_id,
                is_ai,
                bot_name,
                player["vehicle"]["guid"],
                rr.get("time", 0) / 10000.0 if rr.get("time") else None,
                rr.get("lapsCompleted"),
                rr.get("lastCheckpoint"),
                position_map.get(i),
                player.get("startPosition"),
                fastest_lap,
            ),
        )
        if conn.rowcount == 1:
            participations_inserted += 1

    # Load tire telemetry from accompanying details.log when available.
    # Currently scoped to server='events'; extend to 'tripleheat' here when ready.
    if json_path is not None and server == "events":
        log_path = _find_log_path(json_path)
        if log_path is not None:
            _load_details(log_path, sid, player_map, conn)

    return {
        "skipped": False,
        "skip_reason": None,
        "sessions": 1 if session_inserted else 0,
        "participations": participations_inserted,
        "drivers_new": drivers_new,
        "laps": 0,
    }


# ── hotlap loader ─────────────────────────────────────────────────────────────

def _load_hotlap(data: dict, server: str, conn) -> dict:
    _upsert_track(conn, data)

    utc_start = data["utcStartTime"]
    host = data["host"]
    eid = make_hotlap_event_id(utc_start, host)

    conn.execute(
        """
        INSERT INTO base.hotlap_events
            (id, utc_start_time, host, track_guid, server)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (eid, utc_start, host, data["level"]["guid"], server),
    )
    event_inserted = conn.rowcount == 1

    drivers_new = 0
    laps_inserted = 0

    for i, player in enumerate(data["players"]):
        if player["player"]["ai"]:
            continue
        if not player_has_valid_laps(i, data):
            continue

        _upsert_vehicle(conn, player["vehicle"])
        if _upsert_driver(conn, player):
            drivers_new += 1

        steam_id = player["player"]["id"]
        vehicle_guid = player["vehicle"]["guid"]

        for lap in _extract_lap_data(data, i):
            conn.execute(
                """
                INSERT INTO base.hotlap_laps
                    (event_id, steam_id, vehicle_guid,
                     lap_number, lap_time, sector_times)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id, steam_id, lap_number) DO NOTHING
                """,
                (
                    eid,
                    steam_id,
                    vehicle_guid,
                    lap["lap_number"],
                    lap["lap_time"],
                    lap["sector_times"],
                ),
            )
            if conn.rowcount == 1:
                laps_inserted += 1

    return {
        "skipped": False,
        "skip_reason": None,
        "sessions": 1 if event_inserted else 0,
        "participations": 0,
        "drivers_new": drivers_new,
        "laps": laps_inserted,
    }


# ── public API ────────────────────────────────────────────────────────────────

def load_event(json_path: str | Path, server: str, conn) -> dict:
    """
    Parse, validate, and load a single TSU event JSON file.

    Parameters
    ----------
    json_path : path to the *_event.json file
    server    : server label stored in DB, e.g. 'events', 'tripleheat', 'hotlapping'
    conn      : open psycopg3 connection (caller manages transaction)

    Returns a result dict (see module docstring).
    """
    data = _read_json(Path(json_path))

    # Guard against empty/null JSON files
    if data is None:
        return {
            "skipped": True,
            "skip_reason": "null JSON content",
            "sessions": 0,
            "participations": 0,
            "drivers_new": 0,
            "laps": 0,
        }

    # Ignore non-race event types (Sumo, Capture, etc. have no raceStats)
    if "raceStats" not in data:
        return {
            "skipped": True,
            "skip_reason": f"unsupported eventType: {data.get('eventType', 'unknown')}",
            "sessions": 0,
            "participations": 0,
            "drivers_new": 0,
            "laps": 0,
        }

    valid, reason = validate_event(data)
    if not valid:
        return {
            "skipped": True,
            "skip_reason": reason,
            "sessions": 0,
            "participations": 0,
            "drivers_new": 0,
            "laps": 0,
        }

    is_hotlapping = data["raceStats"]["hotlapping"]

    # When loading from the dedicated hotlapping server, skip race-mode files.
    # Any server can produce both modes (event server runs practice in hotlap
    # mode; hotlapping server occasionally produces multi-player race sessions).
    # Only hotlap-mode files belong in the hotlapping leaderboard.
    if server == "hotlapping" and not is_hotlapping:
        return {
            "skipped": True,
            "skip_reason": "race-mode file on hotlapping server",
            "sessions": 0,
            "participations": 0,
            "drivers_new": 0,
            "laps": 0,
        }

    if is_hotlapping:
        return _load_hotlap(data, server, conn)
    else:
        return _load_race(data, server, conn, json_path=Path(json_path))
