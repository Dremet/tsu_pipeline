-- 012_career_penalties.sql — admin-issued penalty points on the standings only.
--
-- Penalties subtract from a driver's championship points_total (and ONLY that;
-- per-race results/credits are untouched). Apply as postgres.

BEGIN;

CREATE TABLE career.penalties (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    season_id  integer     NOT NULL REFERENCES career.seasons(id) ON DELETE CASCADE,
    steam_id   bigint      NOT NULL,
    points     integer     NOT NULL CHECK (points > 0),  -- magnitude, subtracted
    reason     text,
    created_by bigint,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON career.penalties (season_id, steam_id);

GRANT SELECT, INSERT, DELETE ON career.penalties TO tsura;

-- Standings now net out penalty points and expose a penalty_points column.
CREATE OR REPLACE VIEW mart.v_career_standings AS
WITH base_pts AS (
    SELECT cr.season_id,
        cr.steam_id,
        d.name AS driver_name,
        d.flag AS driver_flag,
        COALESCE(dp.team_tag, d.clan) AS display_tag,
        count(*) AS races,
        sum(CASE WHEN rp."position" = 1 THEN 1 ELSE 0 END) AS wins,
        sum(cr.points_finish) AS points_finish,
        sum(cr.is_pole::integer) AS poles,
        sum(cr.is_fastest_lap::integer) AS fastest_laps,
        sum(cr.points_finish + cr.is_pole::integer + cr.is_fastest_lap::integer) AS points_gross
    FROM career.race_rewards cr
    JOIN base.race_participations rp ON rp.id = cr.participation_id
    JOIN base.drivers d ON d.steam_id = cr.steam_id
    LEFT JOIN mart.driver_profiles dp ON dp.steam_id = cr.steam_id
    GROUP BY cr.season_id, cr.steam_id, d.name, d.flag, COALESCE(dp.team_tag, d.clan)
), pen AS (
    SELECT season_id, steam_id, SUM(points)::bigint AS penalty_points
    FROM career.penalties
    GROUP BY season_id, steam_id
)
SELECT b.season_id,
       b.steam_id,
       b.driver_name,
       b.driver_flag,
       b.display_tag,
       b.races,
       b.wins,
       b.points_finish,
       b.poles,
       b.fastest_laps,
       (b.points_gross - COALESCE(p.penalty_points, 0)) AS points_total,
       COALESCE(p.penalty_points, 0)::bigint AS penalty_points
FROM base_pts b
LEFT JOIN pen p ON p.season_id = b.season_id AND p.steam_id = b.steam_id;

COMMIT;
