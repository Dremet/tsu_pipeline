"""Loader tests against a recording connection.

There is no database here on purpose: what these tests check is *which* rows the
loader decides to write and how it behaves on the bad input the archive actually
contains. The SQL itself is checked by applying migration 021 to a database; the
decisions are checked here, where a broken heat file can be constructed in three
lines instead of seeded into eight tables.

The fake connection records every statement, so a test can assert on the tables
touched, the values bound, and -- the part that matters most for section 7 --
that every write is an upsert rather than a blind insert.
"""

import json

import pytest

from tsu_pipeline import topdown_loader as tl

MCVIZN = 76561198131829686
FROZENI = 76561197989276622


class FakeConn:
    """Records statements; answers the two queries the loader actually asks."""

    def __init__(self, known_drivers=(MCVIZN, FROZENI)):
        self.statements = []
        self.known = set(known_drivers)
        self._result = []

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))
        if "FROM base.drivers" in sql:
            asked = params[0] if params else []
            self._result = [(s,) for s in asked if s in self.known]
        else:
            self._result = []

    def fetchall(self):
        return self._result

    # --- helpers for the assertions -------------------------------------
    def into(self, table):
        return [p for sql, p in self.statements if f"INTO base.{table} " in sql]

    def faults(self):
        return [{"kind": p[4], "detail": p[5]} for p in self.into("topdown_data_faults")]

    def fault_kinds(self):
        return sorted(f["kind"] for f in self.faults())


def write_race(tmp_path, *, stamp, session, event=None, name="20260825_120000_T"):
    """Lay out a results folder the way move_raw_files.sh does."""
    event = event or {
        "utcStartTime": "2026-08-25T12:00:00+00:00",
        "players": [{"player": {"id": MCVIZN, "ai": False, "name": "McVizn"}}],
    }
    for suffix, payload in (("_event.json", event), ("_heat.json", stamp),
                            ("_session.json", session)):
        (tmp_path / (name + suffix)).write_text(
            "null" if payload is None else json.dumps(payload), encoding="utf-8")
    return tmp_path / (name + "_event.json")


def stamp(**over):
    base = {"heat_id": 80, "heat_uid": "20260825T115000-80", "round": 1,
            "rounds_total": 3, "phase": "race", "track": "Maple Ridge v1.1",
            "track_guid": "13ng23pntnl3-3472zvj", "vehicle": "VoZzer",
            "vehicle_guid": "17xxzrmve5gb-3868ch8", "laps": 3}
    base.update(over)
    return base


def session(points_by_player=None, finished=2, total=6):
    points_by_player = points_by_player or {MCVIZN: [1, 10]}
    return {
        "m_finishedEventsCount": finished, "m_totalEventCount": total,
        "m_playerStats": [
            {"m_statsPlayer": {"id": sid, "name": str(sid), "index": 0},
             "m_eventStats": [{"m_individualPoints": p} for p in pts],
             "m_totalPoints": sum(pts[:finished])}
            for sid, pts in points_by_player.items()
        ],
    }


def load(tmp_path, **kw):
    conn = FakeConn(known_drivers=kw.pop("known", (MCVIZN, FROZENI)))
    path = write_race(tmp_path, **kw)
    data = json.loads(path.read_text())
    result = tl.load_topdown_extras(
        path, "sess-1", "topdown", conn, data,
        participation_ids=kw.pop("participation_ids", {MCVIZN: "part-1"}),
        status_dir=str(tmp_path / "status"),
    )
    return conn, result


# --- the happy path ---------------------------------------------------------

class TestNormalRace:
    def test_heat_event_and_points_are_written(self, tmp_path):
        conn, result = load(tmp_path, stamp=stamp(), session=session())
        assert conn.into("topdown_heats")
        assert result["heat_uid"] == "20260825T115000-80"
        # Two points rows: the qualifying and the race of round 1.
        assert result["points"] == 2

    def test_the_qualifying_gets_a_row_of_its_own(self, tmp_path):
        """It has no results file, but it scores and it sets the pole."""
        conn, _ = load(tmp_path, stamp=stamp(), session=session())
        phases = [p[2] for p in conn.into("topdown_events")]
        assert sorted(phases) == ["quali", "race"]

    def test_only_the_race_carries_the_session_id(self, tmp_path):
        conn, _ = load(tmp_path, stamp=stamp(), session=session())
        by_phase = {p[2]: p[3] for p in conn.into("topdown_events")}
        assert by_phase["race"] == "sess-1"
        assert by_phase["quali"] is None

    def test_quali_and_race_points_are_stored_apart(self, tmp_path):
        conn, _ = load(tmp_path, stamp=stamp(),
                       session=session({MCVIZN: [1, 10]}))
        rows = {p[2]: p[4] for p in conn.into("topdown_points")}
        assert rows == {"quali": 1, "race": 10}

    def test_every_write_is_an_upsert(self, tmp_path):
        """Section 7: re-reading a file must update, never count twice."""
        conn, _ = load(tmp_path, stamp=stamp(), session=session())
        for table in ("topdown_heats", "topdown_events", "topdown_points"):
            for sql, _ in conn.statements:
                if f"INTO base.{table} " in sql:
                    assert "ON CONFLICT" in sql, table

    def test_the_round_comes_from_the_session_not_the_stamp(self, tmp_path):
        """Four finished events means round two, whatever the stamp claims."""
        conn, _ = load(tmp_path, stamp=stamp(round=1),
                       session=session({MCVIZN: [1, 10, 2, 20]}, finished=4))
        assert {p[1] for p in conn.into("topdown_events")} == {2}
        assert {p[2]: p[4] for p in conn.into("topdown_points")} == {"quali": 2,
                                                                    "race": 20}

    def test_that_disagreement_is_recorded_as_a_fault(self, tmp_path):
        conn, _ = load(tmp_path, stamp=stamp(round=1),
                       session=session({MCVIZN: [1, 10, 2, 20]}, finished=4))
        assert "round_disagreement" in conn.fault_kinds()


# --- the input the archive really contains ----------------------------------

class TestDegradedInput:
    def test_a_null_session_file_loses_points_not_the_race(self, tmp_path):
        """38 of 197 folders hold a literal `null` where the stats should be."""
        conn, result = load(tmp_path, stamp=stamp(), session=None)
        assert result["points"] == 0
        assert "missing_session_stats" in conn.fault_kinds()
        assert conn.into("topdown_heats")          # the heat still lands

    def test_a_null_heat_stamp_is_recorded_and_gives_up_quietly(self, tmp_path):
        conn, result = load(tmp_path, stamp=None, session=session())
        assert result["heat_uid"] is None
        assert conn.fault_kinds() == ["missing_heat_stamp"]

    def test_a_missing_journal_is_a_fault_not_a_silent_blank(self, tmp_path):
        """Otherwise "no status" would read as "everybody finished"."""
        conn, result = load(tmp_path, stamp=stamp(), session=session())
        assert result["status"] == 0
        assert "missing_journal" in conn.fault_kinds()

    def test_points_that_do_not_add_up_are_reported(self, tmp_path):
        """Section 8: 18 shown, 15 reconstructable, must not pass unnoticed."""
        s = session({MCVIZN: [1, 10]})
        s["m_playerStats"][0]["m_totalPoints"] = 18
        conn, _ = load(tmp_path, stamp=stamp(), session=s)
        assert "points_mismatch" in conn.fault_kinds()

    def test_nothing_raises_on_the_worst_case(self, tmp_path):
        """A raise here halts the ingest for all seven servers."""
        conn, result = load(tmp_path, stamp={}, session=None)
        assert result["faults"] >= 1


class TestForeignKeySafety:
    def test_a_player_the_drivers_table_does_not_know_is_skipped(self, tmp_path):
        """Scoring in the qualifying and leaving before the race is normal.

        Such a player is in the session stats but in none of this race's
        players, so writing their points would break the foreign key and, with
        one transaction per file, take the whole race down.
        """
        conn, result = load(
            tmp_path,
            stamp=stamp(),
            session=session({MCVIZN: [1, 10], 999: [6, 4]}),
            known=(MCVIZN,),
        )
        assert {p[3] for p in conn.into("topdown_points")} == {MCVIZN}
        assert result["points"] == 2

    def test_bots_never_get_points(self, tmp_path):
        """They share one steam id, so crediting it would invent a scorer."""
        event = {
            "utcStartTime": "2026-08-25T12:00:00+00:00",
            "players": [
                {"player": {"id": MCVIZN, "ai": False, "name": "McVizn"}},
                {"player": {"id": 76561199107580352, "ai": True, "name": "AI 1"}},
            ],
        }
        conn, _ = load(
            tmp_path, stamp=stamp(), event=event,
            session=session({MCVIZN: [1, 10], 76561199107580352: [0, 0]}),
            known=(MCVIZN, 76561199107580352),
        )
        assert {p[3] for p in conn.into("topdown_points")} == {MCVIZN}


class TestLegacyRaces:
    def test_a_race_from_before_the_journal_still_gets_a_heat(self, tmp_path):
        """The 159 races already loaded have a heat_id but no heat_uid."""
        conn, result = load(tmp_path, stamp=stamp(heat_uid=None),
                            session=session())
        assert result["heat_uid"] == "legacy20260825-80"
        assert conn.into("topdown_heats")

    def test_the_synthesised_key_separates_a_reused_heat_id(self, tmp_path):
        """The July test heats carry ids the live counter will hand out again."""
        july = tmp_path / "july"
        august = tmp_path / "august"
        july.mkdir(); august.mkdir()
        _, a = load(july, stamp=stamp(heat_uid=None, heat_id=100),
                    session=session())
        _, b = load(august, stamp=stamp(heat_uid=None, heat_id=100),
                    session=session(),
                    event={"utcStartTime": "2026-11-02T12:00:00+00:00",
                           "players": [{"player": {"id": MCVIZN, "ai": False,
                                                   "name": "McVizn"}}]})
        assert a["heat_uid"] != b["heat_uid"]

    def test_a_legacy_race_does_not_go_looking_for_a_journal(self, tmp_path):
        """There cannot be one, so it is not reported as missing either."""
        conn, _ = load(tmp_path, stamp=stamp(heat_uid=None), session=session())
        assert "missing_journal" not in conn.fault_kinds()


# --- with a journal ---------------------------------------------------------

def write_journal(tmp_path, heat_uid, records):
    status = tmp_path / "status"
    status.mkdir(exist_ok=True)
    (status / f"{heat_uid}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def journal_records(heat_uid, racers, spectators=(), extra=()):
    def people(entries):
        return [{"steam_id": s, "name": str(s)} for s in entries]

    base = {"heat_uid": heat_uid, "heat_id": 80, "round": 1, "phase": "race"}
    recs = [{"t": "heat_start", "ts": 1000, "rounds_total": 3, **base},
            {"t": "event_init", "ts": 1001, **base},
            {"t": "event_start", "ts": 1002, **base},
            {"t": "roster", "ts": 1003, "reason": "event_start",
             "racers": people(racers), "spectators": people(spectators), **base}]
    recs += [dict(r, **base) for r in extra]
    recs.append({"t": "event_end", "ts": 1099, **base})
    return recs


class TestStatusRows:
    def test_a_finisher_gets_a_completed_row(self, tmp_path):
        uid = "20260825T115000-80"
        write_journal(tmp_path, uid, journal_records(uid, [MCVIZN]))
        conn, result = load(tmp_path, stamp=stamp(), session=session())
        rows = conn.into("topdown_race_status")
        assert result["status"] == 1
        assert rows[0][4] == "completed"
        assert rows[0][9] == "part-1"        # linked to the participation row

    def test_a_spectator_gets_no_row_at_all(self, tmp_path):
        """Section 4.8, and the reason the journal exists in the first place."""
        uid = "20260825T115000-80"
        write_journal(tmp_path, uid,
                      journal_records(uid, [FROZENI], spectators=[MCVIZN]))
        conn, _ = load(tmp_path, stamp=stamp(), session=session())
        assert {p[3] for p in conn.into("topdown_race_status")} == {FROZENI}

    def test_a_retirement_becomes_a_dnf_with_its_evidence(self, tmp_path):
        uid = "20260825T115000-80"
        write_journal(tmp_path, uid, journal_records(
            uid, [MCVIZN],
            extra=[{"t": "retired", "ts": 1050, "steam_id": MCVIZN,
                    "name": str(MCVIZN)}]))
        conn, _ = load(tmp_path, stamp=stamp(), session=session())
        row = conn.into("topdown_race_status")[0]
        assert row[4] == "dnf"
        assert row[6] is True                # retired flag kept as evidence

    def test_no_missing_journal_fault_when_one_is_there(self, tmp_path):
        uid = "20260825T115000-80"
        write_journal(tmp_path, uid, journal_records(uid, [MCVIZN]))
        conn, _ = load(tmp_path, stamp=stamp(), session=session())
        assert "missing_journal" not in conn.fault_kinds()


class TestRestartedRace:
    """Heat 78 on 2026-08-19 ran round 1 twice: aborted, then for real.

    Both folders reach the pipeline. The aborted one is `Stopped_NoPoints` and
    scores nothing; the real one scores normally. Because every write is keyed
    on (heat, round, phase, player), the second load *replaces* the first rather
    than adding to it -- which is exactly what section 7 asks for, and the
    reason the heat totals of the whole archive reconstruct exactly.
    """

    def _load_into(self, tmp_path, folder, points):
        d = tmp_path / folder
        d.mkdir()
        conn = FakeConn()
        path = write_race(d, stamp=stamp(), session=session({MCVIZN: points}))
        data = json.loads(path.read_text())
        tl.load_topdown_extras(path, f"sess-{folder}", "topdown", conn, data,
                               participation_ids={}, status_dir=str(d / "status"))
        return conn

    def test_the_rerun_overwrites_the_aborted_attempt(self, tmp_path):
        aborted = self._load_into(tmp_path, "aborted", [0, 0])
        real = self._load_into(tmp_path, "real", [1, 10])

        # Same key both times, so the second load lands on the first one's rows.
        def keys(conn):
            return {(p[0], p[1], p[2], p[3]) for p in conn.into("topdown_points")}
        assert keys(aborted) == keys(real)

        assert {p[2]: p[4] for p in aborted.into("topdown_points")} == {"quali": 0,
                                                                       "race": 0}
        assert {p[2]: p[4] for p in real.into("topdown_points")} == {"quali": 1,
                                                                    "race": 10}

    def test_the_race_row_is_updated_not_duplicated(self, tmp_path):
        real = self._load_into(tmp_path, "real", [1, 10])
        for sql, _ in real.statements:
            if "INTO base.topdown_points " in sql:
                assert "DO UPDATE SET points = EXCLUDED.points" in sql
