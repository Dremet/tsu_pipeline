"""Attach heat, points and race status to a topdown race that was just loaded.

`loader._load_race` writes the race itself, exactly as it does for every other
server. This adds what only topdown has: the heat the race belonged to, the
points it scored (its qualifying's included), and who actually took part.

Three files feed it, and each answers something the others cannot:

    *_heat.json     which heat and round this was          (the controller)
    *_session.json  the points, qualifying and race apart  (the game)
    <heat_uid>.jsonl  who was racing, who watched, who quit (the controller)

The journal is read from a directory of its own rather than from the results
folder, because the controller and the results hook are separate processes and
the hand-over would otherwise be a race. A race with no journal still loads --
it simply gets no status rows, which is an honest blank rather than a guess.

Nothing here may raise on bad data. `run_pipeline.sh` writes ERROR_OCCURED on a
non-zero exit and then halts the ingest for **all seven servers** until someone
removes the file by hand. One odd heat must not cost the other servers their
results, so inconsistencies are recorded in base.topdown_data_faults and the
load continues.
"""

import json
from pathlib import Path

from . import topdown as rules

STATUS_DIR = "/home/data/topdown/status"


# ── faults ───────────────────────────────────────────────────────────────────

def _fault(conn, kind, detail, heat_uid=None, round_no=None, phase=None,
           session_id=None):
    conn.execute(
        """
        INSERT INTO base.topdown_data_faults
            (heat_uid, round, phase, session_id, kind, detail)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (heat_uid, round_no, phase, session_id, kind, str(detail)[:2000]),
    )


# ── reading the sibling files ────────────────────────────────────────────────

def _sibling(json_path, suffix):
    """The *_heat.json / *_session.json next to a *_event.json."""
    name = json_path.name
    if not name.endswith("_event.json"):
        return None
    path = json_path.parent / (name[: -len("_event.json")] + suffix)
    return path if path.exists() else None


def _read(path):
    """Parse a result file, treating TSU's literal `null` as absent.

    Roughly a fifth of the topdown folders contain a four-byte "null" where the
    stats should be (38 of 197 on 2026-08-25), so this is the normal case, not
    a corner one.
    """
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def find_journal(heat_uid, status_dir=STATUS_DIR):
    path = Path(status_dir) / f"{heat_uid}.jsonl"
    if not path.exists():
        return None
    try:
        return rules.Heat(rules.read_journal(path))
    except OSError:
        return None


def heat_key(stamp, utc_start_time):
    """The heat's unique key, synthesised for races from before it existed.

    Controllers deployed from 2026-08 stamp a heat_uid. Older races have only a
    heat_id, and that counter has been reset -- the July test heats carry ids
    (99, 100) the live counter will hand out again. Pairing the id with the
    race's own date separates them well enough to backfill history.
    """
    if stamp.get("heat_uid"):
        return stamp["heat_uid"], True
    heat_id = stamp.get("heat_id")
    if heat_id is None:
        return None, False
    day = str(utc_start_time)[:10].replace("-", "")
    return f"legacy{day}-{heat_id}", False


# ── writing ──────────────────────────────────────────────────────────────────

def _upsert_heat(conn, heat_uid, stamp, server, journal):
    conn.execute(
        """
        INSERT INTO base.topdown_heats
            (heat_uid, heat_id, server, rounds_total, started_at, ended_at,
             end_reason)
        VALUES (%s, %s, %s, %s, to_timestamp(%s), to_timestamp(%s), %s)
        ON CONFLICT (heat_uid) DO UPDATE SET
            rounds_total = COALESCE(EXCLUDED.rounds_total,
                                    base.topdown_heats.rounds_total),
            ended_at     = COALESCE(EXCLUDED.ended_at,
                                    base.topdown_heats.ended_at),
            end_reason   = COALESCE(EXCLUDED.end_reason,
                                    base.topdown_heats.end_reason)
        """,
        (
            heat_uid,
            stamp.get("heat_id"),
            server,
            stamp.get("rounds_total") or (journal.rounds_total if journal else None),
            journal.started_at if journal else None,
            journal.ended_at if journal else None,
            journal.end_reason if journal else None,
        ),
    )


def _upsert_event(conn, heat_uid, round_no, phase, stamp, session_id):
    conn.execute(
        """
        INSERT INTO base.topdown_events
            (heat_uid, round, phase, session_id, track_guid, vehicle_guid, laps)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (heat_uid, round, phase) DO UPDATE SET
            session_id   = COALESCE(EXCLUDED.session_id,
                                    base.topdown_events.session_id),
            track_guid   = COALESCE(EXCLUDED.track_guid,
                                    base.topdown_events.track_guid),
            vehicle_guid = COALESCE(EXCLUDED.vehicle_guid,
                                    base.topdown_events.vehicle_guid),
            laps         = COALESCE(EXCLUDED.laps, base.topdown_events.laps)
        """,
        (heat_uid, round_no, phase, session_id,
         stamp.get("track_guid") or None, stamp.get("vehicle_guid") or None,
         stamp.get("laps")),
    )


def _known_drivers(conn, steam_ids):
    """Which of these ids base.drivers already knows.

    The points and status tables reference base.drivers, and a player can score
    in a qualifying and leave before the race whose file we are loading -- so
    they are in the session stats but in none of this race's players. Inserting
    them would fail the foreign key and, inside one transaction per file, take
    the whole race down with it.
    """
    if not steam_ids:
        return set()
    conn.execute(
        "SELECT steam_id FROM base.drivers WHERE steam_id = ANY(%s)",
        (list(steam_ids),),
    )
    return {row[0] for row in conn.fetchall()}


def _upsert_points(conn, heat_uid, round_no, points, allowed):
    written = 0
    for steam_id, row in sorted(points.items()):
        if steam_id not in allowed:
            continue
        for phase in (rules.QUALI, rules.RACE):
            if phase not in row:
                continue
            conn.execute(
                """
                INSERT INTO base.topdown_points
                    (heat_uid, round, phase, steam_id, points)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (heat_uid, round, phase, steam_id) DO UPDATE
                    SET points = EXCLUDED.points
                """,
                (heat_uid, round_no, phase, steam_id, row[phase]),
            )
            written += 1
    return written


def _upsert_status(conn, heat_uid, round_no, phase, verdicts, allowed,
                   participation_ids):
    written = 0
    for steam_id, v in sorted(verdicts.items()):
        if steam_id not in allowed:
            continue
        conn.execute(
            """
            INSERT INTO base.topdown_race_status
                (heat_uid, round, phase, steam_id, status, grid_basis,
                 retired, spectated, disconnected_at_end, participation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (heat_uid, round, phase, steam_id) DO UPDATE SET
                status              = EXCLUDED.status,
                grid_basis          = EXCLUDED.grid_basis,
                retired             = EXCLUDED.retired,
                spectated           = EXCLUDED.spectated,
                disconnected_at_end = EXCLUDED.disconnected_at_end,
                participation_id    = COALESCE(
                    EXCLUDED.participation_id,
                    base.topdown_race_status.participation_id)
            """,
            (heat_uid, round_no, phase, steam_id, v["status"], v["grid_basis"],
             v["evidence"]["retired"], v["evidence"]["spectated"],
             v["evidence"]["disconnected_at_end"],
             participation_ids.get(steam_id)),
        )
        written += 1
    return written


# ── entry point ──────────────────────────────────────────────────────────────

def load_topdown_extras(json_path, session_id, server, conn, data,
                        participation_ids=None, status_dir=STATUS_DIR):
    """Write the heat, its points and its status rows for one loaded race.

    `data` is the parsed *_event.json, `participation_ids` maps steam_id to the
    race_participations row already written for this race.

    Returns a summary dict; never raises on bad input.
    """
    json_path = Path(json_path)
    out = {"heat_uid": None, "points": 0, "status": 0, "faults": 0}
    participation_ids = participation_ids or {}

    stamp = _read(_sibling(json_path, "_heat.json"))
    if not stamp:
        _fault(conn, "missing_heat_stamp", json_path.name, session_id=session_id)
        out["faults"] += 1
        return out

    heat_uid, stamped = heat_key(stamp, data.get("utcStartTime"))
    if heat_uid is None:
        _fault(conn, "missing_heat_stamp", "no heat_id in stamp",
               session_id=session_id)
        out["faults"] += 1
        return out
    out["heat_uid"] = heat_uid

    session = _read(_sibling(json_path, "_session.json"))
    journal = find_journal(heat_uid, status_dir) if stamped else None

    _upsert_heat(conn, heat_uid, stamp, server, journal)

    # Which race this is comes from the session file, not the stamp: restart the
    # controller mid-session and its round counter begins again while the
    # session keeps counting (seen 2026-07-31, heat 2, stamp said round 1 with
    # four events already finished).
    round_no = None
    if session:
        finished = int(session.get("m_finishedEventsCount") or 0)
        if finished >= 2:
            round_no = finished // 2
        disagreement = rules.round_disagreement(session, stamp.get("round"))
        if disagreement:
            _fault(conn, "round_disagreement",
                   f"stamp says round {disagreement[0]}, session implies "
                   f"{disagreement[1]}",
                   heat_uid=heat_uid, session_id=session_id)
            out["faults"] += 1
    if round_no is None:
        round_no = stamp.get("round")
    if round_no is None:
        _fault(conn, "missing_round", json_path.name, heat_uid=heat_uid,
               session_id=session_id)
        out["faults"] += 1
        return out

    # The qualifying row exists even though it has no results file of its own:
    # it awards points and it decides the pole.
    _upsert_event(conn, heat_uid, round_no, rules.QUALI, stamp, None)
    _upsert_event(conn, heat_uid, round_no, rules.RACE, stamp, session_id)

    humans = {p["player"]["id"] for p in data.get("players") or []
              if not p["player"].get("ai")}

    if session:
        mismatches = rules.check_session_points(session, strict=False)
        if mismatches:
            _fault(conn, "points_mismatch",
                   ", ".join(f"{sid}: rebuilt {got} vs reported {want}"
                             for sid, (got, want) in sorted(mismatches.items())),
                   heat_uid=heat_uid, round_no=round_no, session_id=session_id)
            out["faults"] += 1

        points = rules.points_from_session(session, round_no)
        # Bots share one steam id between them, so anything the result file did
        # not name as a human is dropped rather than credited.
        allowed = _known_drivers(conn, set(points) & humans)
        out["points"] = _upsert_points(conn, heat_uid, round_no, points, allowed)
    else:
        _fault(conn, "missing_session_stats", json_path.name,
               heat_uid=heat_uid, round_no=round_no, session_id=session_id)
        out["faults"] += 1

    if journal is None:
        # Only worth reporting when there should have been one. A race from
        # before the ledger existed carries no heat_uid and can never have a
        # journal, so flagging those would file a fault for every one of the
        # 159 races already loaded and bury the real ones.
        if stamped:
            _fault(conn, "missing_journal", f"no journal for {heat_uid}",
                   heat_uid=heat_uid, round_no=round_no, session_id=session_id)
            out["faults"] += 1
        return out

    for phase in (rules.QUALI, rules.RACE):
        verdicts = journal.verdicts(round_no, phase)
        if not verdicts:
            continue
        allowed = _known_drivers(conn, set(verdicts))
        out["status"] += _upsert_status(
            conn, heat_uid, round_no, phase, verdicts, allowed,
            participation_ids if phase == rules.RACE else {},
        )
    return out
