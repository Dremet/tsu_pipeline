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
)
from .validate import validate_event, player_has_valid_laps


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

def _load_race(data: dict, server: str, conn) -> dict:
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

        conn.execute(
            """
            INSERT INTO base.race_participations
                (id, session_id, steam_id, is_ai, bot_name, vehicle_guid,
                 finish_time, laps_completed, last_checkpoint, position,
                 start_position)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            ),
        )
        if conn.rowcount == 1:
            participations_inserted += 1

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
    server    : server label stored in DB, e.g. 'events', 'heats', 'hotlapping'
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
    if is_hotlapping:
        return _load_hotlap(data, server, conn)
    else:
        return _load_race(data, server, conn)
