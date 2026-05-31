"""
ELO calculation — separate step, run after load_event.

update_elo(session_ids, conn) processes the given race sessions in
chronological order and writes elo_history rows.

Bots (is_ai=True) are filtered out before any calculation.
A session with fewer than 2 human participants produces no ELO entries.
Minimum ELO is 100; starting ELO is 1000.
"""

K_FACTOR = 20
D = 400
MIN_ELO = 100.0
START_ELO = 1000.0


def _expected_score(elo_a: float, elo_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / D))


def _calc_elo_for_session(
    participants: list[dict],  # [{id, steam_id, position}, ...]
    elo_map: dict,             # steam_id -> current elo
) -> list[dict]:
    """
    Returns list of {participation_id, steam_id, elo_value, elo_delta}.
    Empty if fewer than 2 participants (solo race → no change).
    Bots must already be removed from `participants` before calling.
    """
    n = len(participants)
    if n < 2:
        return []

    results = []
    for row in participants:
        pid = row["id"]
        sid = row["steam_id"]
        pos = row["position"]
        old_elo = elo_map.get(sid, START_ELO)

        opponents = [r for r in participants if r["steam_id"] != sid]
        opp_count = len(opponents)  # = n - 1

        expected_sum = sum(
            _expected_score(old_elo, elo_map.get(o["steam_id"], START_ELO))
            for o in opponents
        )

        # Normalise: divide by (n * opp_count / 2) so scores sum to 1 across field
        denom = n * opp_count / 2.0
        expected_score = expected_sum / denom
        scoring = (n - pos) / denom

        elo_change = K_FACTOR * opp_count * (scoring - expected_score)
        new_elo = max(old_elo + elo_change, MIN_ELO)
        delta = new_elo - old_elo

        results.append(
            {
                "participation_id": pid,
                "steam_id": sid,
                "elo_value": new_elo,
                "elo_delta": delta,
            }
        )

    return results


def _get_current_elo_map(conn, steam_ids: list[int]) -> dict:
    """
    Return {steam_id: latest_elo_value} for the given drivers.

    Priority:
      1. Most recent elo_history entry (computed by this pipeline)
      2. elo_bootstrap value (imported from old racing DB)
      3. START_ELO (1000) — handled by the caller via dict.get()
    """
    if not steam_ids:
        return {}
    conn.execute(
        """
        WITH live AS (
            SELECT
                rp.steam_id,
                eh.elo_value,
                rs.utc_start_time AS ts,
                ROW_NUMBER() OVER (
                    PARTITION BY rp.steam_id
                    ORDER BY rs.utc_start_time DESC
                ) AS rn
            FROM base.elo_history eh
            JOIN base.race_participations rp ON rp.id = eh.participation_id
            JOIN base.race_sessions rs ON rs.id = rp.session_id
            WHERE rp.steam_id = ANY(%s)
        ),
        live_latest AS (
            SELECT steam_id, elo_value FROM live WHERE rn = 1
        ),
        bootstrap AS (
            SELECT steam_id, elo_value
            FROM base.elo_bootstrap
            WHERE steam_id = ANY(%s)
        )
        SELECT
            COALESCE(ll.steam_id, b.steam_id) AS steam_id,
            COALESCE(ll.elo_value, b.elo_value) AS elo_value
        FROM bootstrap b
        FULL OUTER JOIN live_latest ll USING (steam_id)
        WHERE COALESCE(ll.steam_id, b.steam_id) IS NOT NULL
        """,
        (steam_ids, steam_ids),
    )
    return {row[0]: row[1] for row in conn.fetchall()}


def update_elo(session_ids: list[str], conn, *, server: str = "heats") -> int:
    """
    Calculate and persist ELO for the given sessions.

    ELO is computed ONLY for Tripleheat sessions (server='heats' by default).
    NOTE: In the new system, server='heats' always means Tripleheat (not
    Casual-Heat). The label is kept as 'heats' for historical continuity with
    the old racing-DB; it will NOT be renamed when the Tripleheat server moves.

    Passing a different server is possible but should be deliberate — per
    project design, Liga-Events do not receive ELO.

    Sessions are processed in ascending utc_start_time order.
    Already-calculated sessions (existing elo_history rows) are skipped.
    Calling update_elo multiple times on the same sessions is idempotent.

    Returns the number of new elo_history rows inserted.
    """
    if not session_ids:
        return 0

    # Fetch all sessions + human participants, sorted chronologically.
    # Two structural guards:
    #   1. server filter — Events never get ELO by accident.
    #   2. bootstrap cutoff — sessions at or before MAX(elo_bootstrap.last_race_at)
    #      are historical: their ELO contribution is already captured in the bootstrap
    #      seed. Only sessions strictly AFTER the cutoff are new races. When no
    #      bootstrap exists (fresh install), the cutoff is -infinity → all sessions
    #      are processed.
    conn.execute(
        """
        SELECT
            rs.id       AS session_id,
            rs.utc_start_time,
            rp.id       AS participation_id,
            rp.steam_id,
            rp.position,
            rp.laps_completed,
            rp.finish_time,
            rp.last_checkpoint,
            EXISTS (
                SELECT 1 FROM base.elo_history eh
                WHERE eh.participation_id = rp.id
            ) AS already_calculated
        FROM base.race_sessions rs
        JOIN base.race_participations rp ON rp.session_id = rs.id
        WHERE rs.id = ANY(%s)
          AND rs.server = %s
          AND rp.is_ai = false
          AND rp.steam_id IS NOT NULL
          AND rs.utc_start_time > COALESCE(
              (SELECT MAX(last_race_at) FROM base.elo_bootstrap),
              '-infinity'::timestamptz
          )
        ORDER BY rs.utc_start_time ASC, rs.id
        """,
        (session_ids, server),
    )
    rows = conn.fetchall()
    if not rows:
        return 0

    # Group by session
    sessions: dict[str, dict] = {}
    for row in rows:
        sid = row[0]
        if sid not in sessions:
            sessions[sid] = {"utc_start_time": row[1], "participants": [], "skip": False}
        sessions[sid]["participants"].append(
            {
                "id": row[2],
                "steam_id": row[3],
                "position": row[4],
                "laps_completed": row[5],
                "finish_time": row[6],
                "last_checkpoint": row[7],
                "already_calculated": row[8],
            }
        )

    # Collect all unique steam_ids to build initial elo_map
    all_steam_ids = list({p["steam_id"] for s in sessions.values() for p in s["participants"]})
    elo_map = _get_current_elo_map(conn, all_steam_ids)

    inserted = 0
    for sid, session in sorted(sessions.items(), key=lambda x: x[1]["utc_start_time"]):
        participants = session["participants"]

        # Skip session if all participants already have ELO calculated
        if all(p["already_calculated"] for p in participants):
            continue

        # Re-sort by race result within this session
        sorted_participants = sorted(
            participants,
            key=lambda p: (
                -(p["laps_completed"] or 0),
                p["finish_time"] or float("inf"),
                -(p["last_checkpoint"] or 0),
            ),
        )
        # Re-assign positions (consistent with loader.py position_map)
        for i, p in enumerate(sorted_participants):
            p["position"] = i + 1

        elo_rows = _calc_elo_for_session(sorted_participants, elo_map)

        for row in elo_rows:
            conn.execute(
                """
                INSERT INTO base.elo_history (participation_id, elo_value, elo_delta)
                VALUES (%s, %s, %s)
                ON CONFLICT (participation_id) DO UPDATE
                    SET elo_value = EXCLUDED.elo_value,
                        elo_delta = EXCLUDED.elo_delta
                """,
                (row["participation_id"], row["elo_value"], row["elo_delta"]),
            )
            inserted += 1
            elo_map[row["steam_id"]] = row["elo_value"]

    return inserted
