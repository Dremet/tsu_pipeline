"""
Tests for tsu_pipeline.loader — end-to-end with real DB (rolled back per test).
"""

import json
from pathlib import Path

import psycopg
import pytest

from tsu_pipeline.loader import load_event
from tsu_pipeline.elo import update_elo

FIXTURES = Path(__file__).parent / "fixtures"

REAL_EVENT = Path(
    "/home/data/events/20250911_205039/raw/"
    "20250911_205039_NewHampshireMotorSpeedwayv1_event.json"
)
REAL_HOTLAP = Path(
    "/home/data/hotlapping/archive/20251223_170021/raw/"
    "20251223_170021_OffTheRoad_event.json"
)
REAL_HEAT = Path(
    "/home/data/heats/20260513_211653/raw/"
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
    result = load_event(REAL_HEAT, "heats", conn)
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
    """A race where the only human participant finishes solo → no ELO entries."""
    load_event(BOT_RACE_1, "events", conn)
    conn.execute("SELECT id FROM base.race_sessions")
    session_ids = [row[0] for row in conn.fetchall()]
    inserted = update_elo(session_ids, conn)
    assert inserted == 0, "Solo human (+ bot) race must produce no ELO entries"


def test_elo_two_human_race(conn):
    """A real event with 2+ humans must produce ELO entries for each human."""
    load_event(REAL_EVENT, "events", conn)
    conn.execute("SELECT id FROM base.race_sessions")
    session_ids = [row[0] for row in conn.fetchall()]
    inserted = update_elo(session_ids, conn)
    conn.execute(
        "SELECT COUNT(*) FROM base.race_participations WHERE is_ai = false"
    )
    human_count = conn.fetchone()[0]
    assert inserted == human_count
    assert inserted >= 2
