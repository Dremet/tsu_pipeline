"""Regression checks using an isolated copy of the production DB schema.

Run with --noconftest and CAREER_REPAIR_TEST_DSN. Each case rolls back.
"""
import os

import psycopg
import pytest

from tsu_pipeline.career import compute_career_rewards, evaluate_objectives


@pytest.fixture
def cur():
    with psycopg.connect(os.environ['CAREER_REPAIR_TEST_DSN']) as conn:
        assert conn.info.dbname == 'tsu_career_incident_test_20261003'
        cursor = conn.cursor()
        try:
            yield cursor
        finally:
            conn.rollback()


def seed(cur):
    cur.execute("INSERT INTO career.seasons "
                "(name,base_vehicle_name,base_vehicle_veh,start_credits,"
                "credit_first,credit_last,status,activated_at) VALUES "
                "('Exclusion test','Car','/car.veh',1000,200,700,'active',"
                "'2026-08-28T00:00:00Z') RETURNING id")
    season = cur.fetchone()[0]
    cur.execute("INSERT INTO base.tracks(guid,name,level_type) "
                "VALUES ('exclude-track','Exclusion test track','Circuit')")
    for steam_id in [90001,90002]:
        cur.execute("INSERT INTO base.drivers(steam_id,name) VALUES (%s,%s)",
                    (steam_id, f'Driver {steam_id}'))
        cur.execute("INSERT INTO career.enrollments(season_id,steam_id) "
                    "VALUES (%s,%s)", (season,steam_id))
    for sid, timestamp, participants in [
        ('regular-test', '2026-09-28T19:00:00Z', 2),
        ('excluded-test', '2026-09-30T05:19:05Z', 1),
    ]:
        cur.execute("INSERT INTO base.race_sessions "
                    "(id,utc_start_time,host,track_guid,server,finished_state,"
                    "max_laps,participant_count) VALUES "
                    "(%s,%s,1,'exclude-track','career','Finished',7,%s)",
                    (sid,timestamp,participants))
        for position, steam_id in enumerate([90001,90002][:participants],1):
            cur.execute("INSERT INTO base.race_participations "
                        "(id,session_id,steam_id,is_ai,position,start_position,"
                        "fastest_lap,laps_completed) VALUES "
                        "(%s,%s,%s,false,%s,%s,%s,7)",
                        (f'{sid}-{steam_id}',sid,steam_id,position,position,100+position))
    return season


def test_exclusion_removes_credits_and_points_and_survives_reprocessing(cur):
    season = seed(cur)
    assert compute_career_rewards(['regular-test','excluded-test'],cur) == 3
    cur.execute("SELECT steam_id,balance FROM mart.v_career_credit_balance "
                "WHERE season_id=%s ORDER BY steam_id",(season,))
    assert cur.fetchall() == [(90001,1400),(90002,1900)]
    cur.execute("SELECT * FROM career.race_rewards WHERE session_id='regular-test'")
    regular_rewards = cur.fetchall()
    cur.execute("INSERT INTO career.excluded_sessions(session_id,reason) "
                "VALUES ('excluded-test','Unscheduled solo race')")
    assert compute_career_rewards(['excluded-test'],cur) == 0
    cur.execute("SELECT steam_id,balance FROM mart.v_career_credit_balance "
                "WHERE season_id=%s ORDER BY steam_id",(season,))
    assert cur.fetchall() == [(90001,1200),(90002,1700)]
    cur.execute("SELECT * FROM career.race_rewards WHERE session_id='regular-test'")
    assert cur.fetchall() == regular_rewards
    for view in ['v_career_results','v_career_race_sessions']:
        cur.execute(f"SELECT count(*) FROM mart.{view} WHERE session_id='excluded-test'")
        assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM base.race_sessions WHERE id='excluded-test'")
    assert cur.fetchone()[0] == 1
    # Protect against older scorers and direct SQL re-inserting the same reward.
    cur.execute("INSERT INTO career.race_rewards "
                "(participation_id,session_id,season_id,steam_id,credits,points_finish) "
                "VALUES ('excluded-test-90001','excluded-test',%s,90001,200,20)",
                (season,))
    assert cur.rowcount == 0
    cur.execute("DELETE FROM base.race_participations WHERE session_id='excluded-test'")
    cur.execute("DELETE FROM base.race_sessions WHERE id='excluded-test'")
    cur.execute("SELECT count(*) FROM career.excluded_sessions WHERE session_id='excluded-test'")
    assert cur.fetchone()[0] == 1


def test_objectives_ignore_excluded_race(cur):
    season = seed(cur)
    cur.execute("INSERT INTO career.objectives "
                "(season_id,steam_id,race_date,objective,params,description,credits) "
                "VALUES (%s,90001,'2026-09-30','pole','{\"n\":1}','Pole',100)",
                (season,))
    assert evaluate_objectives(['excluded-test'],cur) == 1
    cur.execute("SELECT achieved,progress FROM career.objectives WHERE season_id=%s",(season,))
    assert cur.fetchone() == (True,'1/1')
    cur.execute("INSERT INTO career.excluded_sessions(session_id,reason) "
                "VALUES ('excluded-test','Unscheduled solo race')")
    assert evaluate_objectives(['excluded-test'],cur) == 1
    cur.execute("SELECT achieved,progress FROM career.objectives WHERE season_id=%s",(season,))
    assert cur.fetchone() == (False,'0/1')


def test_exclusion_survives_import_before_first_scoring(cur):
    seed(cur)
    cur.execute("INSERT INTO career.excluded_sessions(session_id,reason) "
                "VALUES ('excluded-test','Unscheduled solo race')")
    assert compute_career_rewards(['regular-test','excluded-test'],cur) == 2
    cur.execute("SELECT DISTINCT session_id FROM career.race_rewards")
    assert cur.fetchall() == [('regular-test',)]


def test_original_incident_file_cannot_recreate_rewards(cur):
    from pathlib import Path
    from tsu_pipeline.loader import load_event

    seed(cur)
    original = Path('/tmp/career-incident-original_event.json')
    if not original.exists():
        pytest.skip('original incident file is only available during incident repair')
    result = load_event(original,'career',cur)
    assert not result['skipped']
    sid = '59ca1b40a0968ae0cac9ea74ae068733'
    cur.execute('SELECT participant_count FROM base.race_sessions WHERE id=%s',(sid,))
    assert cur.fetchone() == (1,)
    assert compute_career_rewards([sid],cur) == 1
    cur.execute('INSERT INTO career.excluded_sessions(session_id,reason) VALUES (%s,%s)',
                (sid,'Unscheduled solo race, 2026-09-30'))
    result = load_event(original,'career',cur)
    assert not result['skipped']
    assert compute_career_rewards([sid],cur) == 0
    cur.execute('SELECT count(*) FROM career.race_rewards WHERE session_id=%s',(sid,))
    assert cur.fetchone()[0] == 0
    cur.execute('SELECT count(*) FROM base.race_participations WHERE session_id=%s',(sid,))
    assert cur.fetchone()[0] == 1
