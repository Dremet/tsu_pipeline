"""
Tests for tsu_pipeline.loader — end-to-end with real DB (rolled back per test).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest

from tsu_pipeline.loader import load_event
from tsu_pipeline.elo import update_elo

FIXTURES = Path(__file__).parent / "fixtures"

REAL_EVENT = Path(
    "/home/data/events/archive/20250911_205039/raw/"
    "20250911_205039_NewHampshireMotorSpeedwayv1_event.json"
)
HEAT_RACE_1 = FIXTURES / "heat_race_1.json"
HEAT_RACE_2 = FIXTURES / "heat_race_2.json"
REAL_HOTLAP = Path(
    "/home/data/hotlapping/archive/20251223_170021/raw/"
    "20251223_170021_OffTheRoad_event.json"
)
REAL_HEAT = Path(
    "/home/data/heats/archive/20260513_211653/raw/"
    "20260513_211653_JäädytettyIndeksi-ClubLayout_event.json"
)
BOT_RACE_1 = FIXTURES / "race_with_bots.json"
BOT_RACE_2 = FIXTURES / "race_with_bots_2.json"


# ── helpers ──────────────────────────────────────────────────────────────────

def _count(conn, table: str, where: str = "", params=()) -> int:
    conn.execute(f"SELECT COUNT(*) FROM {table} {where}", params)
    return conn.fetchone()[0]


# ── real event (race) ─────────────────────────────────────────────────────────

def test_load_real_event(conn):
    result = load_event(REAL_EVENT, "events", conn)
    assert not result["skipped"]
    assert result["sessions"] == 1
    assert result["participations"] > 0
    assert result["drivers_new"] > 0
    assert _count(conn, "base.race_sessions") == 1
    assert _count(conn, "base.race_participations") > 0
    # No bots in real event → all participations have steam_id
    conn.execute("SELECT COUNT(*) FROM base.race_participations WHERE is_ai = true")
    assert conn.fetchone()[0] == 0


def test_load_real_event_idempotent(conn):
    """Loading the same event twice must not produce duplicates."""
    r1 = load_event(REAL_EVENT, "events", conn)
    r2 = load_event(REAL_EVENT, "events", conn)
    assert r1["sessions"] == 1
    assert r2["sessions"] == 0  # ON CONFLICT DO NOTHING → nothing inserted
    assert _count(conn, "base.race_sessions") == 1
    assert r2["participations"] == 0


# ── real heat (Stopped_GivePoints) ────────────────────────────────────────────

def test_load_real_heat(conn):
    result = load_event(REAL_HEAT, "tripleheat", conn)
    assert not result["skipped"]
    assert result["sessions"] == 1


# ── real hotlap ───────────────────────────────────────────────────────────────

def test_load_real_hotlap(conn):
    result = load_event(REAL_HOTLAP, "hotlapping", conn)
    assert not result["skipped"]
    assert result["sessions"] == 1
    assert result["laps"] > 0
    # All laps belong to human drivers
    conn.execute(
        "SELECT COUNT(*) FROM base.hotlap_laps hl "
        "JOIN base.drivers d ON d.steam_id = hl.steam_id"
    )
    assert conn.fetchone()[0] == result["laps"]


def test_load_real_hotlap_idempotent(conn):
    r1 = load_event(REAL_HOTLAP, "hotlapping", conn)
    r2 = load_event(REAL_HOTLAP, "hotlapping", conn)
    assert r1["laps"] > 0
    assert r2["laps"] == 0
    assert _count(conn, "base.hotlap_laps") == r1["laps"]


def test_sentinel_hotlap_skipped(conn):
    result = load_event(FIXTURES / "sentinel_hotlap.json", "hotlapping", conn)
    assert result["skipped"]
    assert _count(conn, "base.hotlap_events") == 0


# ── bot identity isolation ────────────────────────────────────────────────────

def test_two_races_with_same_bot_name(conn):
    """
    Two races each containing a bot named 'Peter' must produce two independent
    participation rows with no shared identity and no FK into base.drivers.
    'Peter' must NOT appear in mart.v_race_results (which filters out bots).
    """
    r1 = load_event(BOT_RACE_1, "events", conn)
    r2 = load_event(BOT_RACE_2, "events", conn)

    assert not r1["skipped"]
    assert not r2["skipped"]

    # Two bot rows exist, completely independent
    conn.execute(
        "SELECT id, session_id, bot_name, steam_id, is_ai "
        "FROM base.race_participations WHERE is_ai = true ORDER BY session_id"
    )
    bots = conn.fetchall()
    assert len(bots) == 2, f"Expected 2 bot rows, got {len(bots)}"

    # Each is tied to its own session
    bot_sessions = {row[1] for row in bots}
    assert len(bot_sessions) == 2, "Bots should belong to different sessions"

    # Both have NULL steam_id → can never be joined to base.drivers
    assert all(row[3] is None for row in bots), "Bot steam_id must be NULL"

    # Bot name stored
    assert all(row[2] == "Peter" for row in bots)

    # 'Peter' must not appear in mart.v_race_results
    conn.execute(
        "SELECT COUNT(*) FROM mart.v_race_results WHERE driver_name = 'Peter'"
    )
    assert conn.fetchone()[0] == 0, "Bots must be absent from mart.v_race_results"


def test_human_drivers_in_bot_races_have_no_cross_contamination(conn):
    """Human drivers from bot races must be in base.drivers; bots must not."""
    load_event(BOT_RACE_1, "events", conn)
    load_event(BOT_RACE_2, "events", conn)

    conn.execute("SELECT steam_id FROM base.drivers")
    driver_steam_ids = {row[0] for row in conn.fetchall()}

    # Human steam IDs from the fixture files
    human_ids = {76561198000001001, 76561198000001002}
    assert human_ids.issubset(driver_steam_ids)

    # No sentinel / zero steam_id should have entered drivers table
    assert 0 not in driver_steam_ids


# ── ELO integration ───────────────────────────────────────────────────────────

def test_elo_not_for_bot_only_race(conn):
    """Solo human (+ bot) server='tripleheat' race → 0 opponents → no ELO entries."""
    load_event(BOT_RACE_1, "tripleheat", conn)
    conn.execute("SELECT id FROM base.race_sessions")
    session_ids = [row[0] for row in conn.fetchall()]
    inserted = update_elo(session_ids, conn)
    assert inserted == 0, "Solo human (+ bot) race must produce no ELO entries"


def test_elo_events_server_never_gets_elo(conn):
    """Sessions with server='events' must never receive ELO (new rule #6)."""
    load_event(REAL_EVENT, "events", conn)
    conn.execute("SELECT id FROM base.race_sessions")
    session_ids = [row[0] for row in conn.fetchall()]
    # Default server='tripleheat' filter → events sessions produce no ELO
    inserted = update_elo(session_ids, conn)
    assert inserted == 0, "Liga-Event sessions must never get ELO"


def test_elo_two_human_heats_race(conn):
    """Two humans in a server='tripleheat' race must each get an ELO entry."""
    load_event(HEAT_RACE_1, "tripleheat", conn)
    conn.execute("SELECT id FROM base.race_sessions WHERE server = 'tripleheat'")
    session_ids = [row[0] for row in conn.fetchall()]
    inserted = update_elo(session_ids, conn)
    assert inserted == 2, "Both human drivers must receive ELO entries"


def test_elo_chronological_multi_race(conn):
    """
    ELO must build chronologically: the second race uses updated ELO from the first.

    Race 1 (08:00): DriverA P1, DriverB P2 → A gains, B loses from 1000 each.
    Race 2 (08:30): DriverB P1, DriverA P2, DriverC P3
        → ELO delta for A and B differs from what it would be at equal starting values,
          because they now carry their Race 1 ELOs.
    """
    load_event(HEAT_RACE_1, "tripleheat", conn)
    load_event(HEAT_RACE_2, "tripleheat", conn)

    conn.execute("SELECT id FROM base.race_sessions WHERE server = 'tripleheat' ORDER BY utc_start_time")
    session_ids = [row[0] for row in conn.fetchall()]
    assert len(session_ids) == 2

    inserted = update_elo(session_ids, conn)
    assert inserted == 5, "Race1 has 2 drivers, Race2 has 3 → 5 ELO entries total"

    # Fetch ELO values in chronological order (sorted by session time + steam_id)
    conn.execute("""
        SELECT rp.steam_id, rs.utc_start_time, eh.elo_value, eh.elo_delta
        FROM base.elo_history eh
        JOIN base.race_participations rp ON rp.id = eh.participation_id
        JOIN base.race_sessions rs ON rs.id = rp.session_id
        ORDER BY rs.utc_start_time, rp.steam_id
    """)
    rows = conn.fetchall()
    # Group by session timestamp (datetime objects - compare directly)
    import collections
    by_race = collections.defaultdict(dict)
    for steam_id, ts, elo_val, elo_delta in rows:
        by_race[ts][steam_id] = (elo_val, elo_delta)

    sorted_races = sorted(by_race.keys())
    a_id = 76561199000000001
    b_id = 76561199000000002

    race1 = by_race[sorted_races[0]]
    race2 = by_race[sorted_races[1]]

    # Race 1: A won (pos=1) vs B (pos=2) both at 1000 → A +10, B -10
    assert race1[a_id][1] == pytest.approx(+10.0)
    assert race1[b_id][1] == pytest.approx(-10.0)

    # Race 2: B won (pos=1) while A=1010, B=990 (from race 1).
    # B's gain in race 2 should be > 0; A's loss reflects being favourite.
    a_r2_elo, a_r2_delta = race2[a_id]
    b_r2_elo, b_r2_delta = race2[b_id]
    assert b_r2_delta > 0, "B won race 2 and should gain ELO"
    assert a_r2_delta < b_r2_delta, "A finished 2nd and should gain less than B"

    # Proving chronological carry-over: A's race2 ELO ≠ what it would be from 1000
    # (If calc was independent, A finishing 2nd in 3-field from 1000 would give ~−6.67)
    # With carry-over A starts at 1010, so the result is different.
    assert a_r2_elo != pytest.approx(990.0), "A's final ELO must reflect race 1 carry-over"


def test_elo_idempotent(conn):
    """Running update_elo twice on the same sessions must yield identical results."""
    load_event(HEAT_RACE_1, "tripleheat", conn)
    conn.execute("SELECT id FROM base.race_sessions WHERE server = 'tripleheat'")
    session_ids = [row[0] for row in conn.fetchall()]

    inserted_first = update_elo(session_ids, conn)
    inserted_second = update_elo(session_ids, conn)

    assert inserted_first == 2
    assert inserted_second == 0, "Second call must not insert duplicates (idempotent)"

    # Values unchanged after second call
    conn.execute("SELECT COUNT(*) FROM base.elo_history")
    assert conn.fetchone()[0] == 2


# ── Bootstrap cutoff protection ───────────────────────────────────────────────

def _insert_minimal_session(conn, session_id: str, utc_start_time, steam_ids: list,
                             track_guid: str, vehicle_guid: str) -> None:
    """Insert a bare-minimum race_session + participations for cutoff tests."""
    conn.execute(
        """
        INSERT INTO base.race_sessions
            (id, utc_start_time, host, track_guid, server, finished_state, participant_count)
        VALUES (%s, %s, 76561190000000001, %s, 'tripleheat', 'Finished', %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (session_id, utc_start_time, track_guid, len(steam_ids)),
    )
    for pos, steam_id in enumerate(steam_ids, 1):
        conn.execute(
            """
            INSERT INTO base.race_participations
                (id, session_id, steam_id, is_ai, vehicle_guid, position, laps_completed)
            VALUES (%s, %s, %s, false, %s, %s, 5)
            ON CONFLICT (id) DO NOTHING
            """,
            (f"{session_id}-p{pos}", session_id, steam_id, vehicle_guid, pos),
        )


def test_elo_bootstrap_cutoff_blocks_historical_sessions(conn):
    """
    Sessions at or before MAX(elo_bootstrap.last_race_at) must be silently
    skipped by update_elo — their ELO contribution is already in the bootstrap
    seed. Only sessions strictly AFTER the cutoff produce new ELO entries.
    """
    cutoff = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    before = datetime(2025, 5, 15, 20, 0, 0, tzinfo=timezone.utc)   # historical
    after  = datetime(2025, 7, 15, 20, 0, 0, tzinfo=timezone.utc)   # new

    track_guid   = "test-track-bootstrap-cutoff"
    vehicle_guid = "test-vehicle-bootstrap-cutoff"
    steam_ids    = [7656119899900001, 7656119899900002]

    conn.execute(
        "INSERT INTO base.tracks (guid, name) VALUES (%s, 'Cutoff Test Track') ON CONFLICT DO NOTHING",
        (track_guid,),
    )
    conn.execute(
        "INSERT INTO base.vehicles (guid, name) VALUES (%s, 'Cutoff Car') ON CONFLICT DO NOTHING",
        (vehicle_guid,),
    )
    for steam_id, name in zip(steam_ids, ["CutoffDriverA", "CutoffDriverB"]):
        conn.execute(
            "INSERT INTO base.drivers (steam_id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (steam_id, name),
        )
        conn.execute(
            """
            INSERT INTO base.elo_bootstrap
                (steam_id, elo_value, number_races, last_race_at, source)
            VALUES (%s, 1000.0, 10, %s, 'test')
            ON CONFLICT (steam_id) DO UPDATE SET last_race_at = EXCLUDED.last_race_at
            """,
            (steam_id, cutoff),
        )

    _insert_minimal_session(conn, "sess-hist-cutoff", before, steam_ids, track_guid, vehicle_guid)
    _insert_minimal_session(conn, "sess-new-cutoff",  after,  steam_ids, track_guid, vehicle_guid)

    inserted = update_elo(["sess-hist-cutoff", "sess-new-cutoff"], conn)

    assert inserted == 2, (
        f"Only the post-cutoff session should produce ELO (2 drivers), got {inserted}"
    )

    # Historical session must have no ELO entries
    conn.execute(
        "SELECT COUNT(*) FROM base.elo_history eh "
        "JOIN base.race_participations rp ON rp.id = eh.participation_id "
        "WHERE rp.session_id = %s",
        ("sess-hist-cutoff",),
    )
    assert conn.fetchone()[0] == 0, "Historical session (before cutoff) must not have ELO entries"

    # New session must have exactly 2 ELO entries (one per driver)
    conn.execute(
        "SELECT COUNT(*) FROM base.elo_history eh "
        "JOIN base.race_participations rp ON rp.id = eh.participation_id "
        "WHERE rp.session_id = %s",
        ("sess-new-cutoff",),
    )
    assert conn.fetchone()[0] == 2, "Post-cutoff session must have 2 ELO entries"


def test_elo_no_bootstrap_processes_all_sessions(conn):
    """
    When elo_bootstrap is empty (fresh install, no migration), the cutoff
    defaults to -infinity and all sessions are processed normally.
    """
    track_guid   = "test-track-no-bootstrap"
    vehicle_guid = "test-vehicle-no-bootstrap"
    steam_ids    = [7656119899900003, 7656119899900004]
    utc_time     = datetime(2025, 3, 1, 20, 0, 0, tzinfo=timezone.utc)

    conn.execute(
        "INSERT INTO base.tracks (guid, name) VALUES (%s, 'No-Bootstrap Track') ON CONFLICT DO NOTHING",
        (track_guid,),
    )
    conn.execute(
        "INSERT INTO base.vehicles (guid, name) VALUES (%s, 'No-Bootstrap Car') ON CONFLICT DO NOTHING",
        (vehicle_guid,),
    )
    for steam_id, name in zip(steam_ids, ["NoBsDriverA", "NoBsDriverB"]):
        conn.execute(
            "INSERT INTO base.drivers (steam_id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (steam_id, name),
        )

    # No bootstrap entries → elo_bootstrap is empty for these drivers (prepared_db truncated it)
    _insert_minimal_session(conn, "sess-no-bootstrap", utc_time, steam_ids, track_guid, vehicle_guid)

    inserted = update_elo(["sess-no-bootstrap"], conn)

    assert inserted == 2, (
        "Without bootstrap, all sessions should be processed (cutoff = -infinity)"
    )


# ── tire telemetry gating by server ───────────────────────────────────────────

_DETAILS_LOG = """\
# Event details:
FormatVersion 1
EventType Circuit
PlayerCount 2
0 76561199000000001 0 DriverA
1 76561199000000002 0 DriverB
TireCompoundCount 2
0 Soft 400000 1
1 Medium 800000 0.88
MaxFuel 50000

Events
10000 Start 0 0 50000 100 0 10000
10000 Start 1 0 50000 100 0 10000
610000 Lap 0 1 40000 80000 0 10000
620000 Lap 1 1 40000 75000 0 10000
1210000 Lap 0 2 30000 160000 0 10000
1220000 Lap 1 2 30000 150000 0 10000
1210000 Finished 0 2 0 160000 0 10000
1220000 Finished 1 2 0 150000 0 10000
"""


def _race_with_details(tmp_path):
    """Copy the heat_race_1 fixture + a matching details.log into tmp_path."""
    json_path = tmp_path / "20990101_000000_TestTrack_event.json"
    json_path.write_text(HEAT_RACE_1.read_text())
    log_path = tmp_path / "20990101_000000_TestTrack_event_details.log"
    log_path.write_text(_DETAILS_LOG)
    return json_path


def test_telemetry_loaded_for_tripleheat(conn, tmp_path):
    """server='tripleheat' loads tire compounds + lap telemetry from details.log."""
    result = load_event(_race_with_details(tmp_path), "tripleheat", conn)
    assert not result["skipped"]
    assert _count(conn, "base.race_tire_compounds") == 2
    assert _count(conn, "base.race_lap_telemetry") > 0


def test_telemetry_not_loaded_for_casual_heat(conn, tmp_path):
    """server='casual_heat' ignores an existing details.log (no stint charts)."""
    result = load_event(_race_with_details(tmp_path), "casual_heat", conn)
    assert not result["skipped"]
    assert _count(conn, "base.race_tire_compounds") == 0
    assert _count(conn, "base.race_lap_telemetry") == 0
