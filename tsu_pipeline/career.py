"""Career per-race reward computation (credits + championship points).

The career analogue of elo.py. Career races (server='career') get NO ELO;
instead each human participation earns, per race:

  * credits       — position-based payout, slower drivers earn MORE. Linear
                    between the season's credit_first (P1) and credit_last (last).
  * points_finish — championship points from a fixed table (participation >= 1).
  * is_pole       — start_position == 1. The grid is set by the preceding
                    Hotlapping-mode qualifying (whose stats are discarded), so
                    the pole sitter is the qualifying winner.
  * is_fastest_lap — owns the fastest lap of the race.

Idempotent: upsert on participation_id (like base.elo_history), so re-runs and
back-fills are safe. Bots (is_ai) are excluded structurally.
"""
from __future__ import annotations

# Championship points by finishing position (1-based). Beyond the table, every
# classified finisher still scores the participation point (1).
POINTS = [20, 16, 12, 10, 8, 6, 5, 4, 3, 2, 1]


def points_for_position(position: int | None) -> int:
    if position is None or position < 1:
        return 0
    if position <= len(POINTS):
        return POINTS[position - 1]
    return 1


def credits_for_position(position: int | None, field_size: int,
                         credit_first: int, credit_last: int) -> int:
    """Linear payout, slower = more: P1 -> credit_first, last -> credit_last."""
    if position is None or position < 1:
        return 0
    if field_size <= 1:
        return credit_first
    frac = (position - 1) / (field_size - 1)
    return round(credit_first + (credit_last - credit_first) * frac)


def _season_for(cur, utc_start_time):
    """The season active at the given time, or None."""
    cur.execute(
        """SELECT id, credit_first, credit_last FROM career.seasons
            WHERE activated_at IS NOT NULL
              AND %s >= activated_at
              AND %s <  COALESCE(finished_at, 'infinity'::timestamptz)
            ORDER BY activated_at DESC LIMIT 1""",
        (utc_start_time, utc_start_time))
    return cur.fetchone()


def compute_career_rewards(session_ids, cur) -> int:
    """Compute + upsert rewards for the given career sessions.

    `cur` is a psycopg cursor; the caller owns the transaction. Returns the
    number of reward rows written.
    """
    written = 0
    for sid in session_ids:
        cur.execute(
            "SELECT utc_start_time, server FROM base.race_sessions WHERE id = %s",
            (sid,))
        row = cur.fetchone()
        if not row or row[1] != "career":
            continue
        utc_start_time = row[0]

        season = _season_for(cur, utc_start_time)
        season_id = season[0] if season else None
        credit_first = season[1] if season else 0
        credit_last = season[2] if season else 0

        cur.execute(
            """SELECT id, steam_id, position, start_position, fastest_lap,
                      laps_completed
                 FROM base.race_participations
                WHERE session_id = %s AND is_ai = false""",
            (sid,))
        parts = cur.fetchall()
        if not parts:
            continue
        if not any(p[5] is not None and p[5] >= 1 for p in parts):
            # phantom race (aborted/restarted event): nobody completed a lap
            continue
        field_size = len(parts)
        laps = [p[4] for p in parts if p[4] is not None]
        best_lap = min(laps) if laps else None

        for pid, steam_id, position, start_pos, fastest, _laps in parts:
            credits = (credits_for_position(position, field_size,
                                            credit_first, credit_last)
                       if season else 0)
            pts = points_for_position(position)
            is_pole = (start_pos == 1)
            is_fl = (fastest is not None and best_lap is not None
                     and fastest == best_lap)
            cur.execute(
                """INSERT INTO career.race_rewards
                     (participation_id, session_id, season_id, steam_id,
                      credits, points_finish, is_pole, is_fastest_lap)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (participation_id) DO UPDATE SET
                     season_id      = EXCLUDED.season_id,
                     credits        = EXCLUDED.credits,
                     points_finish  = EXCLUDED.points_finish,
                     is_pole        = EXCLUDED.is_pole,
                     is_fastest_lap = EXCLUDED.is_fastest_lap,
                     computed_at    = now()""",
                (pid, sid, season_id, steam_id, credits, pts, is_pole, is_fl))
            written += 1
    return written


# --------------------------------------------------------------------------
# Race-day bonus objectives ("challenges") — one random, standings-dependent
# objective per driver and race day, assigned at session-prep time by
# career_prepare_session.py (career server). Achieving it pays its credits
# via mart.v_career_credit_balance. Evaluated here, idempotently, from the
# same participation data as the rewards.
# --------------------------------------------------------------------------

def _objective_hit(objective, params, steam_id, parts, best_lap) -> bool:
    """Did this driver's objective 'hit' in one race? `parts` maps steam_id ->
    {pos, grid, fl} for the race's human participants."""
    me = parts.get(steam_id)
    if not me or me["pos"] is None:
        return False
    if objective == "rival_race":
        rival = parts.get(params.get("rival"))
        # a rival who did not take part (or did not classify) counts as beaten
        return rival is None or rival["pos"] is None or me["pos"] < rival["pos"]
    if objective == "rival_quali":
        rival = parts.get(params.get("rival"))
        if me["grid"] is None:
            return False
        return rival is None or rival["grid"] is None or me["grid"] < rival["grid"]
    if objective == "overtaker":
        return me["grid"] is not None and me["pos"] < me["grid"]
    if objective == "underdog":
        richer = params.get("richer") or []
        return any(
            (r in parts) and (parts[r]["pos"] is None or me["pos"] < parts[r]["pos"])
            for r in richer)
    if objective == "pole":
        return me["grid"] == 1
    if objective == "fastest_lap":
        return (me["fl"] is not None and best_lap is not None
                and me["fl"] == best_lap)
    if objective == "podium2":
        return me["pos"] <= 3
    return False


def evaluate_objectives(session_ids, cur) -> int:
    """Re-score the race-day objectives of every Berlin date touched by
    `session_ids`. All of that day's real career races (>= 1 completed human
    lap) are loaded and each objective is scored from scratch — idempotent,
    like the reward computation. Returns the number of objectives updated."""
    cur.execute("SELECT to_regclass('career.objectives') IS NOT NULL")
    if not cur.fetchone()[0]:
        return 0            # migration 014 not applied yet
    days = set()
    for sid in session_ids:
        cur.execute(
            "SELECT (utc_start_time AT TIME ZONE 'Europe/Berlin')::date "
            "FROM base.race_sessions WHERE id = %s AND server = 'career'",
            (sid,))
        row = cur.fetchone()
        if row:
            days.add(row[0])
    updated = 0
    for day in sorted(days):
        cur.execute(
            """SELECT rs.id FROM base.race_sessions rs
                WHERE rs.server = 'career'
                  AND (rs.utc_start_time AT TIME ZONE 'Europe/Berlin')::date = %s
                  AND EXISTS (SELECT 1 FROM base.race_participations rp
                               WHERE rp.session_id = rs.id AND rp.is_ai = false
                                 AND rp.laps_completed >= 1)
                ORDER BY rs.utc_start_time""", (day,))
        race_ids = [r[0] for r in cur.fetchall()]
        races = []
        for rid in race_ids:
            cur.execute(
                """SELECT steam_id, position, start_position, fastest_lap
                     FROM base.race_participations
                    WHERE session_id = %s AND is_ai = false""", (rid,))
            parts = {r[0]: {"pos": r[1], "grid": r[2], "fl": r[3]}
                     for r in cur.fetchall()}
            fls = [p["fl"] for p in parts.values() if p["fl"] is not None]
            races.append((parts, min(fls) if fls else None))
        cur.execute("SELECT id, steam_id, objective, params "
                    "FROM career.objectives WHERE race_date = %s", (day,))
        for oid, steam_id, objective, params in cur.fetchall():
            params = params or {}
            need = max(1, int(params.get("n", 1)))
            hits = sum(1 for race in races
                       if _objective_hit(objective, params, steam_id, *race))
            cur.execute(
                "UPDATE career.objectives SET achieved = %s, progress = %s, "
                "evaluated_at = now() WHERE id = %s",
                (hits >= need, f"{min(hits, need)}/{need}", oid))
            updated += 1
    return updated
