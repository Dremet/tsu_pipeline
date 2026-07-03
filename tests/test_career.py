"""Tests for tsu_pipeline.career — credit/points formulas + reward computation
and the mart.v_career_* views (standings, credit balance, upgrades)."""
import pytest

from tsu_pipeline.career import (
    compute_career_rewards,
    credits_for_position,
    points_for_position,
    POINTS,
)


# ------------------------------------------------------------ pure functions

def test_points_table():
    assert points_for_position(1) == 20
    assert points_for_position(2) == 16
    assert points_for_position(11) == 1
    assert points_for_position(12) == 1     # participation point beyond table
    assert points_for_position(50) == 1
    assert points_for_position(None) == 0
    assert len(POINTS) == 11


def test_credits_linear_slower_more():
    # P1 -> first, last -> last, linear between; slower earns MORE
    assert credits_for_position(1, 4, 100, 300) == 100
    assert credits_for_position(4, 4, 100, 300) == 300
    assert credits_for_position(2, 4, 100, 300) == 167   # 100 + 200*(1/3)
    assert credits_for_position(3, 4, 100, 300) == 233
    # single participant -> treated as winner
    assert credits_for_position(1, 1, 100, 300) == 100


# ------------------------------------------------------------ integration

def _seed_race(cur):
    """Insert one career race with 4 humans + an active season. Returns ids."""
    ts = "2026-07-06T19:00:00+00:00"
    cur.execute("INSERT INTO career.seasons "
                "(name, base_vehicle_name, base_vehicle_veh, start_credits, "
                " credit_first, credit_last, status, activated_at) "
                "VALUES ('S1','BaseCar','/x/base.veh',500,100,300,'active',"
                "        '2026-07-01T00:00:00+00:00') RETURNING id")
    season_id = cur.fetchone()[0]
    for axis, base, step, cost in [("top_speed", 180, 5, 50),
                                   ("acceleration", 1.0, 0.1, 50),
                                   ("braking", 20, 2, 50),
                                   ("downforce", 0.5, 0.05, 50)]:
        cur.execute("INSERT INTO career.upgrade_axes "
                    "(season_id,axis,base_value,step_per_tier,max_tier,cost_per_tier) "
                    "VALUES (%s,%s,%s,%s,5,%s)", (season_id, axis, base, step, cost))

    cur.execute("INSERT INTO base.tracks(guid,name,level_type) "
                "VALUES ('trk','Test Circuit','Circuit') ON CONFLICT DO NOTHING")
    cur.execute("INSERT INTO base.race_sessions"
                "(id,utc_start_time,host,track_guid,server,finished_state,"
                " max_laps,participant_count) "
                "VALUES ('csess',%s,1,'trk','career','Finished',10,4)", (ts,))
    # (steam_id, name, position, start_position, fastest_lap)
    rows = [(1001, "Alice", 1, 2, 90.0),
            (1002, "Bob",   2, 1, 91.0),   # start P1 -> pole/quali win
            (1003, "Cara",  3, 3, 85.0),   # min lap -> fastest lap
            (1004, "Dan",   4, 4, 92.0)]
    for sid, name, pos, sp, fl in rows:
        cur.execute("INSERT INTO base.drivers(steam_id,name) VALUES (%s,%s) "
                    "ON CONFLICT DO NOTHING", (sid, name))
        cur.execute("INSERT INTO base.race_participations"
                    "(id,session_id,steam_id,is_ai,position,start_position,fastest_lap) "
                    "VALUES (%s,'csess',%s,false,%s,%s,%s)",
                    (f"p{sid}", sid, pos, sp, fl))
    return season_id


def test_compute_rewards(conn):
    season_id = _seed_race(conn)
    n = compute_career_rewards(["csess"], conn)
    assert n == 4

    conn.execute("SELECT steam_id,credits,points_finish,is_pole,is_fastest_lap "
                 "FROM career.race_rewards WHERE session_id='csess' ORDER BY steam_id")
    r = {row[0]: row[1:] for row in conn.fetchall()}
    # credits linear (100..300), points from table, pole=Bob, FL=Cara
    assert r[1001] == (100, 20, False, False)
    assert r[1002] == (167, 16, True, False)
    assert r[1003] == (233, 12, False, True)
    assert r[1004] == (300, 10, False, False)


def test_standings_view(conn):
    _seed_race(conn)
    compute_career_rewards(["csess"], conn)
    conn.execute("SELECT steam_id, wins, points_total, poles, fastest_laps "
                 "FROM mart.v_career_standings ORDER BY points_total DESC")
    rows = conn.fetchall()
    by = {r[0]: r[1:] for r in rows}
    assert by[1001] == (1, 20, 0, 0)   # win, 20 pts
    assert by[1002] == (0, 17, 1, 0)   # 16 + pole
    assert by[1003] == (0, 13, 0, 1)   # 12 + fastest lap
    assert by[1004] == (0, 10, 0, 0)


def test_credit_balance_and_backfill(conn):
    season_id = _seed_race(conn)
    compute_career_rewards(["csess"], conn)

    # Alice joined before the race -> earns her 100, no backfill
    conn.execute("INSERT INTO career.enrollments(season_id,steam_id,joined_at) "
                 "VALUES (%s,1001,'2026-07-02T00:00:00+00:00')", (season_id,))
    # She buys top_speed tier 2 (cost 50/tier -> spent 100)
    conn.execute("INSERT INTO career.driver_upgrades(season_id,steam_id,axis,tier) "
                 "VALUES (%s,1001,'top_speed',2)", (season_id,))
    # Newcomer joined AFTER the race -> mid-field backfill for 1 missed race
    conn.execute("INSERT INTO career.enrollments(season_id,steam_id,joined_at) "
                 "VALUES (%s,2001,'2026-07-07T00:00:00+00:00')", (season_id,))

    conn.execute("SELECT steam_id,start_credits,earned,races_missed,backfill,spent,balance "
                 "FROM mart.v_career_credit_balance ORDER BY steam_id")
    bal = {r[0]: r[1:] for r in conn.fetchall()}
    # Alice: 500 start + 100 earned + 0 backfill - 100 spent = 500
    assert bal[1001] == (500, 100, 0, 0, 100, 500)
    # Newcomer: 500 start + 0 earned + 1 missed * mid-field(200) - 0 spent = 700
    assert bal[2001] == (500, 0, 1, 200, 0, 700)


def test_upgrades_view_final_value(conn):
    season_id = _seed_race(conn)
    conn.execute("INSERT INTO career.driver_upgrades(season_id,steam_id,axis,tier) "
                 "VALUES (%s,1001,'top_speed',3)", (season_id,))
    conn.execute("SELECT final_value, spent FROM mart.v_career_upgrades "
                 "WHERE steam_id=1001 AND axis='top_speed'")
    final_value, spent = conn.fetchone()
    assert final_value == pytest.approx(180 + 3 * 5)   # base 180 + 3*step 5 = 195
    assert spent == 150                                # 3 tiers * 50


def test_loader_flows_career_label(conn, tmp_path):
    """A race loaded with server='career' lands in base with server='career' and
    computes rewards — proving the category flows through the existing loader."""
    import json
    import shutil
    from pathlib import Path
    from tsu_pipeline.loader import load_event

    src = Path(__file__).parent / "fixtures" / "heat_race_1.json"
    dst = tmp_path / "20260706_190000_Test_event.json"
    shutil.copy(src, dst)

    res = load_event(dst, "career", conn)
    assert not res["skipped"]
    conn.execute("SELECT DISTINCT server FROM base.race_sessions")
    assert conn.fetchone()[0] == "career"

    conn.execute("SELECT id FROM base.race_sessions WHERE server='career'")
    sids = [r[0] for r in conn.fetchall()]
    n = compute_career_rewards(sids, conn)
    assert n >= 1
    conn.execute("SELECT COUNT(*) FROM mart.v_career_results")
    assert conn.fetchone()[0] >= 1
