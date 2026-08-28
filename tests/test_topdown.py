"""Status derivation: McVizn's case table (PRIO A section 4.6) as tests.

Every case in his table gets one test, named after the sequence it describes.
The journals are built the way the controller writes them, so a change to the
record format breaks these tests rather than silently changing verdicts.
"""

import json

import pytest

from tsu_pipeline import topdown as td

MCVIZN = 76561198131829686
FROZENI = 76561197989276622

HEAT_UID = "20260825T210000-80"


class Journal:
    """Builds a journal the way td/ledger.py does, one call per line."""

    def __init__(self, heat_id=80, rounds_total=3):
        self.records = []
        self.round = None
        self.phase = None
        self.ts = 1000.0
        self._add("heat_start", rounds_total=rounds_total)
        self.heat_id = heat_id

    def _add(self, kind, **fields):
        self.ts += 1
        rec = {"t": kind, "ts": self.ts, "heat_id": 80, "heat_uid": HEAT_UID,
               "round": self.round, "phase": self.phase}
        rec.update(fields)
        self.records.append(rec)
        return self

    def event(self, round_no=1, phase="race"):
        self.round, self.phase = round_no, phase
        return self._add("event_init")

    def roster(self, racers, spectators=(), reason=None):
        return self._add(
            "roster", reason=reason,
            racers=[{"steam_id": s, "name": n} for s, n in racers],
            spectators=[{"steam_id": s, "name": n} for s, n in spectators],
        )

    def start(self, racers, spectators=()):
        self._add("event_start")
        return self.roster(racers, spectators, reason="event_start")

    def init_roster(self, racers, spectators=()):
        return self.roster(racers, spectators, reason="event_init")

    def end(self):
        return self._add("event_end")

    def spectate(self, steam_id=None, name=None):
        return self._add("spectate", steam_id=steam_id, name=name)

    def unspectate(self, steam_id=None, name=None):
        return self._add("unspectate", steam_id=steam_id, name=name)

    def retired(self, steam_id=None, name=None):
        return self._add("retired", steam_id=steam_id, name=name)

    def leave(self, steam_id=None, name=None):
        return self._add("leave", steam_id=steam_id, name=name, spectator=False)

    def join(self, steam_id=None, name=None):
        return self._add("join", steam_id=steam_id, name=name, spectator=False)

    def heat(self):
        return td.Heat(self.records)


def verdict(journal, steam_id=MCVIZN, round_no=1, phase="race"):
    return journal.heat().verdicts(round_no, phase).get(steam_id)


ON_GRID = [(MCVIZN, "[VSR] McVizn")]
WATCHING = [(MCVIZN, "[VSR] McVizn")]


# --- section 4.6, row by row ------------------------------------------------

class TestMcViznCaseTable:
    def test_spectator_at_init_then_unspectate_is_not_a_participant(self):
        j = Journal().event().start(racers=[], spectators=WATCHING)
        j.unspectate(MCVIZN)
        j.end()
        assert verdict(j) is None

    def test_spectator_at_init_who_becomes_a_player_is_not_a_participant(self):
        """4.1: a running race cannot be joined, so no row may appear."""
        j = Journal().event().start(racers=[], spectators=WATCHING)
        j.unspectate(MCVIZN)
        j.roster(racers=ON_GRID)
        j.end()
        assert verdict(j) is None

    def test_player_to_normal_finish_is_completed(self):
        j = Journal().event().start(racers=ON_GRID)
        j.end()
        assert verdict(j)["status"] == td.COMPLETED

    def test_disconnect_then_reconnect_then_finish_is_completed(self):
        """4.3: the one interruption a race survives."""
        j = Journal().event().start(racers=ON_GRID)
        j.leave(MCVIZN)
        j.join(MCVIZN)
        j.end()
        assert verdict(j)["status"] == td.COMPLETED

    def test_still_disconnected_at_the_end_is_a_disconnect(self):
        j = Journal().event().start(racers=ON_GRID)
        j.leave(MCVIZN)
        j.end()
        assert verdict(j)["status"] == td.DISCONNECT

    def test_switching_to_spectator_and_staying_there_is_a_dnf(self):
        j = Journal().event().start(racers=ON_GRID)
        j.spectate(MCVIZN)
        j.end()
        assert verdict(j)["status"] == td.DNF

    def test_spectator_then_unspectate_then_finish_is_still_a_dnf(self):
        """4.4: coming back does not resume the race that was abandoned."""
        j = Journal().event().start(racers=ON_GRID)
        j.spectate(MCVIZN)
        j.unspectate(MCVIZN)
        j.end()
        assert verdict(j)["status"] == td.DNF

    def test_spectator_then_unspectate_then_disconnect_is_a_dnf(self):
        j = Journal().event().start(racers=ON_GRID)
        j.spectate(MCVIZN)
        j.unspectate(MCVIZN)
        j.leave(MCVIZN)
        j.end()
        assert verdict(j)["status"] == td.DNF

    def test_disconnect_reconnect_then_spectator_is_a_dnf(self):
        """Giving up outranks the disconnect that came before it."""
        j = Journal().event().start(racers=ON_GRID)
        j.leave(MCVIZN)
        j.join(MCVIZN)
        j.spectate(MCVIZN)
        j.end()
        assert verdict(j)["status"] == td.DNF

    def test_retired_is_a_dnf(self):
        j = Journal().event().start(racers=ON_GRID)
        j.retired(MCVIZN)
        j.end()
        assert verdict(j)["status"] == td.DNF


# --- the rules behind the table ---------------------------------------------

class TestParticipation:
    def test_a_spectator_at_the_start_gets_no_row_at_all(self):
        """4.8: no race statistic of any kind, not a row with empty fields."""
        j = Journal().event().start(racers=[(FROZENI, "[SR] Frozeni")],
                                    spectators=WATCHING)
        j.end()
        assert set(j.heat().verdicts(1, "race")) == {FROZENI}

    def test_the_race_start_snapshot_wins_over_the_init_one(self):
        """Observed 2026-08-18: they are up to half a minute apart and differ."""
        j = Journal().event().init_roster(racers=ON_GRID)
        j.spectate(MCVIZN)
        j.start(racers=[], spectators=WATCHING)
        j.end()
        assert verdict(j) is None

    def test_the_init_snapshot_is_used_when_no_start_snapshot_arrived(self):
        j = Journal().event().init_roster(racers=ON_GRID)
        j._add("event_start")
        j.end()
        v = verdict(j)
        assert v["status"] == td.COMPLETED
        assert v["grid_basis"] == td.INIT_EVENT_INIT

    def test_a_race_with_no_roster_at_all_yields_nothing(self):
        """Better an honest blank than an invented grid."""
        j = Journal().event()
        j._add("event_start")
        j.end()
        assert j.heat().verdicts(1, "race") == {}


class TestEventBoundaries:
    def test_a_transition_after_the_race_does_not_count_against_it(self):
        """Leaving while the results are up is not a DNF in that race."""
        j = Journal().event().start(racers=ON_GRID)
        j.end()
        j.spectate(MCVIZN)
        assert verdict(j)["status"] == td.COMPLETED

    def test_races_are_judged_independently(self):
        j = Journal().event(1, "race").start(racers=ON_GRID)
        j.retired(MCVIZN)
        j.end()
        j.event(2, "race").start(racers=ON_GRID)
        j.end()
        assert verdict(j, round_no=1)["status"] == td.DNF
        assert verdict(j, round_no=2)["status"] == td.COMPLETED

    def test_qualifying_and_race_of_one_round_are_separate(self):
        j = Journal().event(1, "quali").start(racers=ON_GRID)
        j.retired(MCVIZN)
        j.end()
        j.event(1, "race").start(racers=ON_GRID)
        j.end()
        assert verdict(j, phase="quali")["status"] == td.DNF
        assert verdict(j, phase="race")["status"] == td.COMPLETED


class TestNameResolution:
    def test_a_transition_carrying_only_a_name_is_still_attributed(self):
        """"X retired." has no id; the roster replies in the heat supply it."""
        j = Journal().event().start(racers=ON_GRID)
        j.retired(steam_id=None, name="[VSR] McVizn")
        j.end()
        assert verdict(j)["status"] == td.DNF

    def test_an_unknown_name_is_ignored_rather_than_guessed(self):
        j = Journal().event().start(racers=ON_GRID)
        j.retired(steam_id=None, name="somebody else entirely")
        j.end()
        assert verdict(j)["status"] == td.COMPLETED


class TestHeatCompletion:
    """Section 2: all three races, all finished, or it does not count."""

    def _heat(self, per_round):
        j = Journal(rounds_total=3)
        for round_no, what in enumerate(per_round, start=1):
            j.event(round_no, "quali").start(racers=ON_GRID).end()
            j.event(round_no, "race")
            if what == "absent":
                j.start(racers=[], spectators=WATCHING).end()
                continue
            j.start(racers=ON_GRID)
            if what == "dnf":
                j.retired(MCVIZN)
            elif what == "disconnect":
                j.leave(MCVIZN)
            j.end()
        return j.heat()

    def test_all_three_finished_counts(self):
        assert td.heat_completed_for(self._heat(["ok", "ok", "ok"]), MCVIZN)

    def test_one_dnf_breaks_it(self):
        assert not td.heat_completed_for(self._heat(["ok", "dnf", "ok"]), MCVIZN)

    def test_one_disconnect_breaks_it(self):
        assert not td.heat_completed_for(
            self._heat(["ok", "ok", "disconnect"]), MCVIZN)

    def test_a_late_joiner_never_completes_a_heat(self):
        """Section 2: missing the first race is enough, however well it went."""
        assert not td.heat_completed_for(self._heat(["absent", "ok", "ok"]), MCVIZN)

    def test_a_heat_that_never_ran_its_races_does_not_count(self):
        j = Journal(rounds_total=3)
        j.event(1, "race").start(racers=ON_GRID).end()
        assert not td.heat_completed_for(j.heat(), MCVIZN)


class TestJournalReading:
    def test_a_torn_line_costs_that_line_only(self, tmp_path):
        """The pipeline halts everything on an exception, so it must not raise."""
        path = tmp_path / "heat.jsonl"
        path.write_text(
            json.dumps({"t": "heat_start", "heat_uid": HEAT_UID, "heat_id": 80})
            + "\n{ this is half a line\n"
            + json.dumps({"t": "heat_end", "heat_uid": HEAT_UID, "reason": "finished"})
            + "\n",
            encoding="utf-8",
        )
        records = td.read_journal(path)
        assert [r["t"] for r in records] == ["heat_start", "heat_end"]

    def test_heat_metadata_is_read_back(self):
        j = Journal(rounds_total=3)
        j.event(1, "race").start(racers=ON_GRID).end()
        j._add("heat_end", reason="finished")
        heat = j.heat()
        assert heat.heat_uid == HEAT_UID
        assert heat.rounds_total == 3
        assert heat.end_reason == "finished"

    def test_events_are_listed_in_order(self):
        j = Journal()
        for round_no in (1, 2):
            j.event(round_no, "quali").end()
            j.event(round_no, "race").end()
        assert j.heat().events() == [
            (1, "quali"), (1, "race"), (2, "quali"), (2, "race"),
        ]


class TestLappingIsNotADnf:
    def test_nothing_here_looks_at_laps(self):
        """Section 3: a lap deficit must never become a DNF on its own.

        Guarded by construction -- the journal has no lap data at all -- so this
        test states the intent for whoever later adds a "helpful" shortcut.
        """
        j = Journal().event().start(racers=ON_GRID)
        j.end()
        v = verdict(j)
        assert v["status"] == td.COMPLETED
        assert "laps" not in json.dumps(v)


# --- points (section 0 and 8) -----------------------------------------------

def session(*per_player, finished=6, total=6):
    """A session stats file shaped like TSU writes it.

    Each argument is (steam_id, [points per event]); the total is derived, which
    is what makes a deliberately broken total in the mismatch tests visible.
    """
    stats = []
    for steam_id, points in per_player:
        stats.append({
            "m_statsPlayer": {"id": steam_id, "name": str(steam_id), "index": 0},
            "m_eventStats": [{"m_individualPoints": p, "m_rank": 1} for p in points],
            "m_totalPoints": sum(points[:finished]),
        })
    return {"m_finishedEventsCount": finished, "m_totalEventCount": total,
            "m_playerStats": stats}


class TestPoints:
    def test_quali_and_race_of_the_round_are_kept_apart(self):
        """Section 6: a qualifying point is not a race point."""
        s = session((MCVIZN, [1, 10, 0, 6, 1, 4]), finished=6)
        assert td.points_from_session(s) == {MCVIZN: {"quali": 1, "race": 4}}

    def test_the_race_is_located_by_the_files_own_event_count(self):
        """The file saved beside race 2 has four finished events, not six."""
        s = session((MCVIZN, [1, 10, 0, 6]), finished=4, total=6)
        assert td.points_from_session(s) == {MCVIZN: {"quali": 0, "race": 6}}

    def test_the_first_round_reads_the_first_two_events(self):
        s = session((MCVIZN, [1, 10]), finished=2, total=6)
        assert td.points_from_session(s) == {MCVIZN: {"quali": 1, "race": 10}}

    def test_a_session_with_no_finished_race_yields_nothing(self):
        s = session((MCVIZN, [1]), finished=1, total=6)
        assert td.points_from_session(s) == {}

    def test_qualifying_points_are_part_of_the_heat_total(self):
        """Section 0: some events score in qualifying and it must not be lost."""
        s = session((MCVIZN, [1, 10, 1, 10, 1, 10]), finished=6)
        collected = 0
        for round_no in (1, 2, 3):
            sliced = session((MCVIZN, [1, 10, 1, 10, 1, 10]),
                             finished=2 * round_no, total=6)
            row = td.points_from_session(sliced)[MCVIZN]
            collected += row["quali"] + row["race"]
        assert collected == td.session_totals(s)[MCVIZN] == 33


class TestPointsConsistency:
    def test_a_session_that_adds_up_passes(self):
        assert td.check_session_points(session((MCVIZN, [1, 10, 1, 10, 1, 10]))) == {}

    def test_a_session_that_does_not_add_up_is_raised_not_swallowed(self):
        """Section 8: 18 shown, 15 reconstructable, is a fault to surface."""
        s = session((MCVIZN, [10, 5]), finished=2)
        s["m_playerStats"][0]["m_totalPoints"] = 18
        with pytest.raises(td.PointsMismatch):
            td.check_session_points(s)

    def test_unfinished_events_are_not_counted_towards_the_total(self):
        """Only the events the session says are done may contribute."""
        s = session((MCVIZN, [1, 10, 7, 7]), finished=2)
        assert td.check_session_points(s) == {}

    def test_the_report_names_who_disagrees_and_by_how_much(self):
        s = session((MCVIZN, [10]), (FROZENI, [6]), finished=1)
        s["m_playerStats"][0]["m_totalPoints"] = 12
        assert td.check_session_points(s, strict=False) == {MCVIZN: (10, 12)}


class TestRoundCrossCheck:
    def test_agreement_is_silent(self):
        assert td.round_disagreement(session((MCVIZN, [1, 10]), finished=2), 1) is None

    def test_a_stamp_from_a_restarted_controller_is_reported(self):
        """The real case from 2026-07-31: stamp said round 1, session said two."""
        s = session((MCVIZN, [1, 10, 1, 10]), finished=4)
        assert td.round_disagreement(s, 1) == (1, 2)

    def test_the_points_stay_correct_despite_the_disagreement(self):
        s = session((MCVIZN, [1, 10, 2, 20]), finished=4)
        assert td.points_from_session(s, round_no=1) == {MCVIZN: {"quali": 2,
                                                                  "race": 20}}
