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
            """SELECT id, steam_id, position, start_position, fastest_lap
                 FROM base.race_participations
                WHERE session_id = %s AND is_ai = false""",
            (sid,))
        parts = cur.fetchall()
        if not parts:
            continue
        field_size = len(parts)
        laps = [p[4] for p in parts if p[4] is not None]
        best_lap = min(laps) if laps else None

        for pid, steam_id, position, start_pos, fastest in parts:
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
