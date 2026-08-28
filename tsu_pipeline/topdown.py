"""Turn a topdown status journal into per-race verdicts.

The controller records what happened; this decides what it meant. The split is
deliberate: the journal is raw observation and never changes, so when McVizn
sharpens a rule we re-run this over the journals we already have instead of
having lost the evidence.

The rules implemented here are McVizn's PRIO A, sections 3, 4.6 and 4.7, in his
order of priority:

  1. Participation is settled first. Only somebody who was a *racer* when the
     race was armed took part in it. A spectator at that moment did not, and no
     later unspectate can change that -- it must not even produce a row.
  2. For an actual participant, the run of the race decides:
        gave up (retired) or switched to spectator  -> DNF
        still disconnected when the race ended      -> DISCONNECT
        anything else                               -> COMPLETED

Two things this module will *not* do:

* It never calls a race a DNF because somebody was lapped. Section 3 is explicit
  about that: on a short oval a big lap deficit is normal, and the game's own
  status is the truth. Where the journal is silent, the verdict is UNKNOWN and
  the caller may fall back on the result file -- knowingly, not by accident.
* It never invents a participant. A race with no journal yields no verdicts at
  all rather than guesses.
"""

import json

# Verdicts. DISQUALIFIED is part of the vocabulary because McVizn's spec has it,
# but nothing produces it yet: no line on the script port announces one, so
# claiming otherwise would be pretending we can see something we cannot.
COMPLETED = "completed"
DNF = "dnf"
DISCONNECT = "disconnect"
DISQUALIFIED = "disqualified"
UNKNOWN = "unknown"

# Which snapshot settled the grid, and how sure we are of it.
INIT_EVENT_START = "event_start"
INIT_EVENT_INIT = "event_init"

# Record types, mirroring td/ledger.py on the controller side.
HEAT_START = "heat_start"
HEAT_END = "heat_end"
EVENT_INIT = "event_init"
EVENT_START = "event_start"
EVENT_END = "event_end"
ROSTER = "roster"
JOIN = "join"
LEAVE = "leave"
SPECTATE = "spectate"
UNSPECTATE = "unspectate"
RETIRED = "retired"


def read_journal(path):
    """Parse a .jsonl journal, skipping anything unreadable.

    A single torn line -- the controller was killed mid-write -- must cost that
    line and nothing else. The pipeline halts the whole ingest on an exception,
    so being strict here would stop every server's results over one bad heat.
    """
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("t"):
                records.append(record)
    return records


class Heat:
    """One heat's journal, grouped into its events."""

    def __init__(self, records):
        self.records = records
        head = next((r for r in records if r["t"] == HEAT_START), {})
        self.heat_uid = head.get("heat_uid") or _first(records, "heat_uid")
        self.heat_id = head.get("heat_id") or _first(records, "heat_id")
        self.rounds_total = head.get("rounds_total")
        self.started_at = head.get("ts")
        tail = next((r for r in reversed(records) if r["t"] == HEAT_END), {})
        self.ended_at = tail.get("ts")
        self.end_reason = tail.get("reason")
        self.names = _name_index(records)

    def events(self):
        """Every (round, phase) the journal saw, in order."""
        seen = []
        for rec in self.records:
            key = (rec.get("round"), rec.get("phase"))
            if key == (None, None) or key in seen:
                continue
            seen.append(key)
        return seen

    def slice(self, round_no, phase):
        return [r for r in self.records
                if r.get("round") == round_no and r.get("phase") == phase]

    def verdicts(self, round_no, phase):
        """Who took part in this race, and how it ended for each of them.

        Returns {steam_id: {...}}. Absent from the mapping means "did not take
        part", which is a different statement from "took part and has no time".
        """
        records = self.slice(round_no, phase)
        grid, basis = self._grid(records)
        if grid is None:
            return {}

        started = _index_of(records, EVENT_START)
        ended = _index_of(records, EVENT_END)
        # Only what happened during the race counts. A spectator switch made
        # while the results screen is up belongs to the next race, not this one.
        window = records[started if started is not None else 0:
                         ended if ended is not None else len(records)]

        out = {}
        for steam_id, name in grid.items():
            out[steam_id] = self._verdict(steam_id, name, window, basis)
        return out

    def _grid(self, records):
        """Who was racing when the grid was locked in, and which snapshot said so.

        The race start is the better answer: init and start are up to half a
        minute apart, and players do change their mind in between (observed
        2026-08-18). The init snapshot is the fallback for a race whose start
        snapshot never arrived.
        """
        for reason, basis in ((EVENT_START, INIT_EVENT_START),
                              (EVENT_INIT, INIT_EVENT_INIT)):
            snap = next((r for r in records
                         if r["t"] == ROSTER and r.get("reason") == reason), None)
            if snap is not None:
                return ({p["steam_id"]: p.get("name")
                         for p in snap.get("racers") or []
                         if p.get("steam_id") is not None}, basis)
        return None, None

    def _verdict(self, steam_id, name, window, basis):
        spectated = retired = False
        disconnected = False
        for rec in window:
            if self._who(rec) != steam_id:
                continue
            kind = rec["t"]
            if kind == RETIRED:
                retired = True
            elif kind == SPECTATE:
                spectated = True
            elif kind == LEAVE:
                disconnected = True
            elif kind == JOIN:
                # The one interruption a race survives (4.3). It resumes the
                # race that was already running; it never starts a second one.
                disconnected = False

        # Giving up outranks a disconnect whichever way round they happened:
        # "Player -> Disconnect -> Reconnect -> Spectator" is a DNF too (4.6).
        if retired or spectated:
            status = DNF
        elif disconnected:
            status = DISCONNECT
        else:
            status = COMPLETED

        return {
            "steam_id": steam_id,
            "name": name,
            "status": status,
            "grid_basis": basis,
            # What the verdict rests on, so a surprising row can be explained
            # without re-reading the journal by hand.
            "evidence": {
                "retired": retired,
                "spectated": spectated,
                "disconnected_at_end": status == DISCONNECT,
            },
        }

    def _who(self, rec):
        """The steam id a record is about, by name if it carried none.

        Only the roster replies carry ids; "X retired." carries a name. The
        controller resolves what it can, but a player who joined seconds earlier
        is not in its roster yet, so the name index closes that gap.
        """
        if rec.get("steam_id") is not None:
            return rec["steam_id"]
        return self.names.get(rec.get("name"))


def _first(records, key):
    for rec in records:
        if rec.get(key) is not None:
            return rec[key]
    return None


def _index_of(records, kind):
    for i, rec in enumerate(records):
        if rec["t"] == kind:
            return i
    return None


def _name_index(records):
    """name -> steam_id, from every roster reply in the heat."""
    index = {}
    for rec in records:
        if rec["t"] != ROSTER:
            continue
        for group in ("racers", "spectators"):
            for entry in rec.get(group) or []:
                if entry.get("name") and entry.get("steam_id") is not None:
                    index[entry["name"]] = entry["steam_id"]
    return index


# --- points -----------------------------------------------------------------
#
# TSU writes a session stats file next to every event's results. It is
# cumulative: the copy saved beside race N carries the scores of every event so
# far, qualifying included. That is what makes McVizn's "the parts must add up
# to the session total" checkable at all -- the total is in the same file.
#
# One round is a qualifying and then a race, so the event index of round N is:
#
#     qualifying = 2N - 2        race = 2N - 1
#
# Confirmed against `m_finishedEventsCount`, which is exactly 2N in the file
# saved beside race N.

QUALI = "quali"
RACE = "race"


class PointsMismatch(Exception):
    """The parts do not add up to the total the session itself reports.

    Section 8 asks for exactly this to be loud: a session showing 18 points that
    reconstructs to 15 is a data fault, not a rounding detail, and swallowing it
    would leave the profile page quietly wrong.
    """


def event_index(round_no, phase):
    """Where round N's qualifying and race sit, counting from the heat's start."""
    return 2 * int(round_no) - (2 if phase == QUALI else 1)


def indices_for_race(session):
    """Locate the race this file was written for, and its qualifying.

    Derived from the file's own `m_finishedEventsCount` rather than from the
    round in the heat stamp, because the two can disagree: restart the
    controller while a session is running and its round counter begins again
    while the session keeps counting. It happened on 2026-07-31 (heat 2, round
    1, four events already finished), and trusting the stamp there would have
    filed the last race's points against the first race.

    The file is saved as the race ends, so that race is the last finished event.
    """
    finished = int(session.get("m_finishedEventsCount") or 0)
    if finished < 2:
        return None, None
    return finished - 2, finished - 1


def points_from_session(session, round_no=None):
    """Per-player points for one round's qualifying and race.

    Returns {steam_id: {"quali": int, "race": int}} for humans. Bots are left
    out: they share one steam id between them and hold no statistics.

    `round_no` is optional and only cross-checked -- see `round_disagreement`.
    """
    quali_index, race_index = indices_for_race(session)
    if race_index is None:
        return {}
    out = {}
    for player_stats in session.get("m_playerStats") or []:
        player = player_stats.get("m_statsPlayer") or {}
        steam_id = player.get("id")
        if steam_id is None:
            continue
        events = player_stats.get("m_eventStats") or []
        row = {}
        for phase, index in ((QUALI, quali_index), (RACE, race_index)):
            if 0 <= index < len(events):
                row[phase] = int(events[index].get("m_individualPoints") or 0)
        if row:
            # Several bots share the host's steam id, distinguished only by
            # their session index. Summing them would invent points for a human
            # who never scored, so the first entry per id wins and the loader
            # drops ids that the result file marks as AI.
            out.setdefault(steam_id, row)
    return out


def round_disagreement(session, round_no):
    """None when the heat stamp and the session agree on which race this is.

    Otherwise the pair (round the stamp claims, round the session implies), for
    the caller to record as the data fault it is. It is not fatal: the points
    are still attributed correctly, because they are read off the session.
    """
    finished = int(session.get("m_finishedEventsCount") or 0)
    if round_no is None or finished < 2:
        return None
    implied = finished // 2
    return None if implied == int(round_no) else (int(round_no), implied)


def session_totals(session):
    """The per-player totals the session itself reports."""
    out = {}
    for player_stats in session.get("m_playerStats") or []:
        player = player_stats.get("m_statsPlayer") or {}
        if player.get("id") is None:
            continue
        out.setdefault(player["id"], int(player_stats.get("m_totalPoints") or 0))
    return out


def check_session_points(session, strict=True):
    """Verify that the recorded per-event points rebuild the session totals.

    Returns {steam_id: (rebuilt, reported)} for every player who disagrees.
    """
    mismatches = {}
    for player_stats in session.get("m_playerStats") or []:
        player = player_stats.get("m_statsPlayer") or {}
        steam_id = player.get("id")
        if steam_id is None:
            continue
        finished = int(session.get("m_finishedEventsCount") or 0)
        events = (player_stats.get("m_eventStats") or [])[:finished]
        rebuilt = sum(int(e.get("m_individualPoints") or 0) for e in events)
        reported = int(player_stats.get("m_totalPoints") or 0)
        if rebuilt != reported:
            mismatches[steam_id] = (rebuilt, reported)
    if mismatches and strict:
        raise PointsMismatch(
            "per-event points do not add up to the session total: "
            + ", ".join(f"{sid}: {got} != {want}"
                        for sid, (got, want) in sorted(mismatches.items()))
        )
    return mismatches


def heat_completed_for(heat, steam_id, rounds_total=None):
    """Did this player complete the heat in McVizn's sense (section 2)?

    All of it, or none of it: present from the start, a registered racer at the
    init of every race, every race actually finished. A late joiner never
    completes a heat, which is why the count of races raced has to match the
    heat's full length rather than merely being non-zero.
    """
    total = rounds_total if rounds_total is not None else heat.rounds_total
    if not total:
        return False
    races = [(r, "race") for r in range(1, int(total) + 1)]
    for round_no, phase in races:
        verdict = heat.verdicts(round_no, phase).get(steam_id)
        if verdict is None or verdict["status"] != COMPLETED:
            return False
    return True
