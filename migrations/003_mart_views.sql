-- Mart views v2: extended race + hotlap views and driver profile view.
-- These replace the initial stubs from 001_base_schema.sql.
-- Run idempotent (CREATE OR REPLACE).

-- ── v_race_results ────────────────────────────────────────────────────────────
-- Wide race result view: one row per human participant.
-- Bots are filtered out (is_ai = false).

CREATE OR REPLACE VIEW mart.v_race_results AS
SELECT
    rp.id              AS participation_id,
    rs.id              AS session_id,
    rs.utc_start_time,
    rs.server,
    rs.finished_state,
    rs.track_guid,
    t.name             AS track_name,
    t.level_type       AS track_type,
    rp.steam_id,
    d.name             AS driver_name,
    d.flag             AS driver_flag,
    d.clan             AS driver_clan,
    rp.vehicle_guid,
    v.name             AS vehicle_name,
    rp.position,
    rp.finish_time,
    rp.laps_completed,
    rs.participant_count,
    -- ELO (only populated for server='heats')
    eh.elo_value,
    eh.elo_delta,
    -- Convenience: current ELO from history or bootstrap
    COALESCE(
        (SELECT eh2.elo_value
         FROM base.elo_history eh2
         JOIN base.race_participations rp2 ON rp2.id = eh2.participation_id
         JOIN base.race_sessions rs2 ON rs2.id = rp2.session_id
         WHERE rp2.steam_id = rp.steam_id
         ORDER BY rs2.utc_start_time DESC
         LIMIT 1),
        eb.elo_value
    ) AS current_elo,
    -- Human participant count per session (bots excluded by WHERE is_ai=false)
    COUNT(*) OVER (PARTITION BY rs.id) AS human_participant_count
FROM base.race_participations rp
JOIN base.race_sessions rs    ON rp.session_id = rs.id
JOIN base.tracks t            ON rs.track_guid = t.guid
JOIN base.drivers d           ON rp.steam_id = d.steam_id
LEFT JOIN base.vehicles v     ON rp.vehicle_guid = v.guid
LEFT JOIN base.elo_history eh ON rp.id = eh.participation_id
LEFT JOIN base.elo_bootstrap eb ON rp.steam_id = eb.steam_id
WHERE rp.is_ai = false;


-- ── v_hotlap_results ─────────────────────────────────────────────────────────
-- Wide hotlap view: one row per individual lap (all laps, not just best).
-- Best lap per (event, driver) = MIN(lap_time).

CREATE OR REPLACE VIEW mart.v_hotlap_results AS
SELECT
    hl.id              AS lap_id,
    hl.event_id,
    he.utc_start_time,
    he.server,
    he.track_guid,
    t.name             AS track_name,
    t.level_type       AS track_type,
    hl.steam_id,
    d.name             AS driver_name,
    d.flag             AS driver_flag,
    d.clan             AS driver_clan,
    hl.vehicle_guid,
    v.name             AS vehicle_name,
    hl.lap_number,
    hl.lap_time,
    hl.sector_times,
    -- Convenience: is this lap the best time for this driver in this event?
    hl.lap_time = MIN(hl.lap_time) OVER (
        PARTITION BY hl.event_id, hl.steam_id
    ) AS is_best_lap
FROM base.hotlap_laps hl
JOIN base.hotlap_events he ON hl.event_id = he.id
JOIN base.tracks t         ON he.track_guid = t.guid
JOIN base.drivers d        ON hl.steam_id = d.steam_id
LEFT JOIN base.vehicles v  ON hl.vehicle_guid = v.guid;


-- ── v_hotlap_sessions ────────────────────────────────────────────────────────
-- One row per hotlap event. Kept for backward compatibility / index card.

CREATE OR REPLACE VIEW mart.v_hotlap_sessions AS
SELECT
    he.id              AS event_id,
    he.utc_start_time,
    he.server,
    t.name             AS track_name,
    COUNT(DISTINCT hl.steam_id)                                       AS driver_count,
    COUNT(*)                                                          AS total_laps,
    ARRAY_AGG(DISTINCT v.name) FILTER (WHERE v.name IS NOT NULL)      AS cars_used
FROM base.hotlap_events he
JOIN base.tracks t        ON t.guid  = he.track_guid
JOIN base.hotlap_laps hl  ON hl.event_id = he.id
LEFT JOIN base.vehicles v ON v.guid  = hl.vehicle_guid
WHERE he.server = 'hotlapping'
GROUP BY he.id, he.utc_start_time, he.server, t.name;


-- ── v_hotlap_grouped_sessions ─────────────────────────────────────────────────
-- Groups consecutive hotlap events with the same track into one session.
-- A new session starts only when the track changes (same rule as old dbt).
-- group_id = id of the first event in the group (stable hash, usable in URLs).

CREATE OR REPLACE VIEW mart.v_hotlap_grouped_sessions AS
WITH ordered AS (
    SELECT
        he.id              AS event_id,
        he.utc_start_time,
        he.track_guid,
        LAG(he.track_guid) OVER (ORDER BY he.utc_start_time, he.id) AS prev_track_guid
    FROM base.hotlap_events he
    WHERE he.server = 'hotlapping'
),
change_points AS (
    SELECT *,
        CASE WHEN track_guid IS DISTINCT FROM prev_track_guid OR prev_track_guid IS NULL
             THEN 1 ELSE 0 END AS is_new_session
    FROM ordered
),
session_nums AS (
    SELECT *,
        SUM(is_new_session) OVER (ORDER BY utc_start_time, event_id) AS session_num
    FROM change_points
),
with_group_id AS (
    SELECT *,
        FIRST_VALUE(event_id) OVER (
            PARTITION BY session_num ORDER BY utc_start_time, event_id
        ) AS group_id
    FROM session_nums
)
SELECT
    g.group_id,
    t.name                                                             AS track_name,
    MIN(g.utc_start_time)                                             AS session_start,
    MAX(g.utc_start_time)                                             AS session_end,
    COUNT(DISTINCT g.event_id)                                        AS event_count,
    COUNT(DISTINCT hl.steam_id)                                       AS driver_count,
    COUNT(hl.id)                                                      AS total_laps,
    ARRAY_AGG(DISTINCT v.name) FILTER (WHERE v.name IS NOT NULL)      AS cars_used,
    MIN(hl.lap_time)                                                  AS best_lap_time
FROM with_group_id g
JOIN base.tracks t         ON t.guid = g.track_guid
JOIN base.hotlap_laps hl   ON hl.event_id = g.event_id
LEFT JOIN base.vehicles v  ON v.guid = hl.vehicle_guid
GROUP BY g.group_id, t.name;


-- ── v_hotlap_group_results ────────────────────────────────────────────────────
-- All laps from all events within a session group, with group_id for filtering.
-- is_best_lap: best lap per (group, driver) across the entire session.

CREATE OR REPLACE VIEW mart.v_hotlap_group_results AS
WITH ordered AS (
    SELECT
        he.id              AS event_id,
        he.utc_start_time,
        he.track_guid,
        LAG(he.track_guid) OVER (ORDER BY he.utc_start_time, he.id) AS prev_track_guid
    FROM base.hotlap_events he
    WHERE he.server = 'hotlapping'
),
change_points AS (
    SELECT *,
        CASE WHEN track_guid IS DISTINCT FROM prev_track_guid OR prev_track_guid IS NULL
             THEN 1 ELSE 0 END AS is_new_session
    FROM ordered
),
session_nums AS (
    SELECT *,
        SUM(is_new_session) OVER (ORDER BY utc_start_time, event_id) AS session_num
    FROM change_points
),
with_group_id AS (
    SELECT
        event_id,
        track_guid,
        FIRST_VALUE(event_id) OVER (
            PARTITION BY session_num ORDER BY utc_start_time, event_id
        ) AS group_id
    FROM session_nums
)
SELECT
    hl.id              AS lap_id,
    hl.event_id,
    g.group_id,
    he.utc_start_time,
    t.name             AS track_name,
    t.level_type       AS track_type,
    hl.steam_id,
    d.name             AS driver_name,
    d.flag             AS driver_flag,
    d.clan             AS driver_clan,
    hl.vehicle_guid,
    v.name             AS vehicle_name,
    hl.lap_number,
    hl.lap_time,
    hl.sector_times,
    hl.lap_time = MIN(hl.lap_time) OVER (
        PARTITION BY g.group_id, hl.steam_id
    ) AS is_best_lap
FROM base.hotlap_laps hl
JOIN with_group_id g       ON g.event_id = hl.event_id
JOIN base.hotlap_events he ON he.id = hl.event_id
JOIN base.tracks t         ON t.guid = he.track_guid
JOIN base.drivers d        ON d.steam_id = hl.steam_id
LEFT JOIN base.vehicles v  ON v.guid = hl.vehicle_guid;


-- ── v_driver_profile ─────────────────────────────────────────────────────────
-- One row per driver. Aggregates Tripleheat ELO + hotlap stats + race stats.
-- Designed for profile pages (Phase 4): "at a glance" for a given steam_id.

CREATE OR REPLACE VIEW mart.v_driver_profile AS
WITH elo_current AS (
    -- Latest ELO from live history, falling back to bootstrap seed
    SELECT
        d.steam_id,
        COALESCE(
            (SELECT eh.elo_value
             FROM base.elo_history eh
             JOIN base.race_participations rp ON rp.id = eh.participation_id
             JOIN base.race_sessions rs ON rs.id = rp.session_id
             WHERE rp.steam_id = d.steam_id AND rs.server = 'heats'
             ORDER BY rs.utc_start_time DESC
             LIMIT 1),
            eb.elo_value
        ) AS elo_value,
        eb.number_races AS legacy_race_count,
        eb.last_race_at AS legacy_last_race
    FROM base.drivers d
    LEFT JOIN base.elo_bootstrap eb ON eb.steam_id = d.steam_id
),
elo_ranked AS (
    -- Rank elo_history entries per driver by recency for delta/trend
    SELECT
        rp.steam_id,
        eh.elo_delta,
        ROW_NUMBER() OVER (
            PARTITION BY rp.steam_id
            ORDER BY rs.utc_start_time DESC
        ) AS rn
    FROM base.elo_history eh
    JOIN base.race_participations rp ON rp.id = eh.participation_id
    JOIN base.race_sessions rs       ON rs.id = rp.session_id
    WHERE rs.server = 'heats'
),
elo_last AS (
    SELECT steam_id, elo_delta AS heat_elo_delta
    FROM elo_ranked
    WHERE rn = 1
),
elo_trend AS (
    SELECT steam_id, SUM(elo_delta) AS heat_elo_trend_6
    FROM elo_ranked
    WHERE rn <= 6
    GROUP BY steam_id
),
heat_stats AS (
    -- Only count sessions AFTER the bootstrap cutoff to avoid double-counting.
    -- Historical Tripleheat sessions (loaded for display) are already reflected
    -- in elo_bootstrap.number_races; counting them here again would be wrong.
    SELECT
        rp.steam_id,
        COUNT(*)                          AS heat_races,
        SUM(CASE WHEN rp.position = 1 THEN 1 ELSE 0 END) AS heat_wins,
        MIN(rp.position)                  AS heat_best_position,
        MAX(rs.utc_start_time)            AS heat_last_race_at
    FROM base.race_participations rp
    JOIN base.race_sessions rs ON rs.id = rp.session_id
    WHERE rs.server = 'heats'
      AND rp.is_ai = false
      AND rs.utc_start_time > COALESCE(
          (SELECT MAX(last_race_at) FROM base.elo_bootstrap),
          '-infinity'::timestamptz
      )
    GROUP BY rp.steam_id
),
event_stats AS (
    SELECT
        rp.steam_id,
        COUNT(*)                          AS event_races,
        SUM(CASE WHEN rp.position = 1 THEN 1 ELSE 0 END) AS event_wins,
        MAX(rs.utc_start_time)            AS event_last_race_at
    FROM base.race_participations rp
    JOIN base.race_sessions rs ON rs.id = rp.session_id
    WHERE rs.server = 'events' AND rp.is_ai = false
    GROUP BY rp.steam_id
),
hotlap_stats AS (
    SELECT
        hl.steam_id,
        COUNT(DISTINCT he.id)             AS hotlap_events,
        COUNT(*)                          AS hotlap_total_laps,
        MIN(hl.lap_time)                  AS hotlap_alltime_best,
        MAX(he.utc_start_time)            AS hotlap_last_session_at
    FROM base.hotlap_laps hl
    JOIN base.hotlap_events he ON he.id = hl.event_id
    GROUP BY hl.steam_id
)
SELECT
    d.steam_id,
    d.name             AS driver_name,
    d.flag             AS driver_flag,
    d.clan             AS driver_clan,
    -- Tripleheat ELO
    ec.elo_value       AS heat_elo,
    COALESCE(hs.heat_races, 0) + COALESCE(ec.legacy_race_count, 0) AS heat_total_races,
    COALESCE(hs.heat_races, 0) AS heat_races_new_pipeline,
    COALESCE(hs.heat_wins,  0) AS heat_wins,
    hs.heat_best_position,
    GREATEST(hs.heat_last_race_at, ec.legacy_last_race) AS heat_last_race_at,
    -- Liga-Events (no ELO)
    COALESCE(es.event_races, 0) AS event_races,
    COALESCE(es.event_wins,  0) AS event_wins,
    es.event_last_race_at,
    -- Hotlapping
    COALESCE(hls.hotlap_events,     0) AS hotlap_events,
    COALESCE(hls.hotlap_total_laps, 0) AS hotlap_total_laps,
    hls.hotlap_alltime_best,
    hls.hotlap_last_session_at,
    -- ELO delta and trend from live elo_history (NULL until first live Tripleheat race)
    el.heat_elo_delta,
    et.heat_elo_trend_6
FROM base.drivers d
LEFT JOIN elo_current   ec  ON ec.steam_id  = d.steam_id
LEFT JOIN elo_last      el  ON el.steam_id  = d.steam_id
LEFT JOIN elo_trend     et  ON et.steam_id  = d.steam_id
LEFT JOIN heat_stats    hs  ON hs.steam_id  = d.steam_id
LEFT JOIN event_stats   es  ON es.steam_id  = d.steam_id
LEFT JOIN hotlap_stats  hls ON hls.steam_id = d.steam_id;


-- ── Grants ───────────────────────────────────────────────────────────────────
-- tsura (website read-only user) needs SELECT on all mart views.
-- CREATE OR REPLACE VIEW preserves existing grants, but we re-grant idempotently.

GRANT SELECT ON mart.v_race_results            TO tsura;
GRANT SELECT ON mart.v_hotlap_results          TO tsura;
GRANT SELECT ON mart.v_hotlap_sessions         TO tsura;
GRANT SELECT ON mart.v_hotlap_grouped_sessions TO tsura;
GRANT SELECT ON mart.v_hotlap_group_results    TO tsura;
GRANT SELECT ON mart.v_driver_profile          TO tsura;
