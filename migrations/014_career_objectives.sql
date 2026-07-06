-- 014_career_objectives.sql — race-day bonus objectives ("challenges").
--
-- Every enrolled driver gets ONE random objective per race day (assigned at
-- session-prep time by career_prepare_session.py, standings-dependent so it
-- is realistically achievable). Achieving it that day pays `credits` (100).
-- The pipeline (tsu_pipeline.career.evaluate_objectives) re-scores a day's
-- objectives idempotently after each ingested career race.
--
-- Also: phantom races (aborted/restarted events where no human completed a
-- single lap, laps_completed <= 0 for everyone) are excluded from
-- mart.v_career_results — compute_career_rewards skips them going forward.
--
-- Apply as postgres.

CREATE TABLE IF NOT EXISTS career.objectives (
    id           bigserial PRIMARY KEY,
    season_id    integer NOT NULL REFERENCES career.seasons(id) ON DELETE CASCADE,
    steam_id     bigint  NOT NULL,
    race_date    date    NOT NULL,
    objective    text    NOT NULL CHECK (objective IN
                   ('rival_race','rival_quali','overtaker','underdog',
                    'pole','fastest_lap','podium2')),
    params       jsonb   NOT NULL DEFAULT '{}'::jsonb,
    description  text    NOT NULL,
    credits      integer NOT NULL DEFAULT 100,
    achieved     boolean NOT NULL DEFAULT false,
    progress     text,
    evaluated_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (season_id, steam_id, race_date)
);

GRANT SELECT ON career.objectives TO tsura;
GRANT SELECT, INSERT, UPDATE, DELETE ON career.objectives TO data;
-- the career server's prep step assigns objectives (its role is otherwise RO)
GRANT SELECT, INSERT, UPDATE ON career.objectives TO career_ro;
GRANT USAGE ON SEQUENCE career.objectives_id_seq TO career_ro, data;

-- objectives + driver names for the website
CREATE OR REPLACE VIEW mart.v_career_objectives AS
SELECT o.id, o.season_id, o.steam_id, o.race_date, o.objective, o.params,
       o.description, o.credits, o.achieved, o.progress, o.evaluated_at,
       d.name AS driver_name
FROM career.objectives o
LEFT JOIN base.drivers d ON d.steam_id = o.steam_id;

GRANT SELECT ON mart.v_career_objectives TO tsura, data, career_ro;

-- balance: achieved objectives pay out (column appended, order preserved)
CREATE OR REPLACE VIEW mart.v_career_credit_balance AS
WITH earned AS (
    SELECT season_id, steam_id,
           COALESCE(SUM(credits), 0)::bigint AS earned
    FROM career.race_rewards
    GROUP BY season_id, steam_id
), spent AS (
    SELECT du.season_id, du.steam_id,
           COALESCE(SUM(du.tier * ua.cost_per_tier), 0)::bigint AS spent
    FROM career.driver_upgrades du
    JOIN career.upgrade_axes ua
      ON ua.season_id = du.season_id AND ua.axis = du.axis
    GROUP BY du.season_id, du.steam_id
), race_avg AS (            -- average credits paid to participants, per scored race
    SELECT season_id, session_id, AVG(credits) AS avg_credits
    FROM career.race_rewards
    GROUP BY season_id, session_id
), participated AS (       -- (season, race, driver) tuples that actually raced
    SELECT DISTINCT season_id, session_id, steam_id
    FROM career.race_rewards
), backfill AS (           -- per driver: field-average credits for missed races
    SELECT e.season_id, e.steam_id,
           COUNT(*)::bigint AS races_missed,
           COALESCE(ROUND(SUM(ra.avg_credits)), 0)::bigint AS backfill
    FROM career.enrollments e
    JOIN race_avg ra ON ra.season_id = e.season_id
    LEFT JOIN participated p
      ON p.season_id = ra.season_id
     AND p.session_id = ra.session_id
     AND p.steam_id  = e.steam_id
    WHERE p.steam_id IS NULL           -- driver did NOT participate in this race
    GROUP BY e.season_id, e.steam_id
), objectives AS (         -- achieved race-day challenges pay their credits
    SELECT season_id, steam_id,
           COALESCE(SUM(credits) FILTER (WHERE achieved), 0)::bigint AS objective_credits
    FROM career.objectives
    GROUP BY season_id, steam_id
)
SELECT e.season_id,
       e.steam_id,
       s.start_credits,
       COALESCE(ea.earned, 0)::bigint       AS earned,
       COALESCE(bf.races_missed, 0)::bigint AS races_missed,
       COALESCE(bf.backfill, 0)::bigint     AS backfill,
       COALESCE(sp.spent, 0)::bigint        AS spent,
       (s.start_credits
        + COALESCE(ea.earned, 0)
        + COALESCE(bf.backfill, 0)
        + COALESCE(ob.objective_credits, 0)
        - COALESCE(sp.spent, 0))::bigint    AS balance,
       COALESCE(ob.objective_credits, 0)::bigint AS objective_credits
FROM career.enrollments e
JOIN career.seasons s ON s.id = e.season_id
LEFT JOIN earned     ea ON ea.season_id = e.season_id AND ea.steam_id = e.steam_id
LEFT JOIN spent      sp ON sp.season_id = e.season_id AND sp.steam_id = e.steam_id
LEFT JOIN backfill   bf ON bf.season_id = e.season_id AND bf.steam_id = e.steam_id
LEFT JOIN objectives ob ON ob.season_id = e.season_id AND ob.steam_id = e.steam_id;

-- hide phantom races (no human completed a lap) from career results
CREATE OR REPLACE VIEW mart.v_career_results AS
 SELECT rp.id AS participation_id,
    rs.id AS session_id,
    rs.utc_start_time,
    t.name AS track_name,
    t.level_type AS track_type,
    rp.steam_id,
    d.name AS driver_name,
    d.flag AS driver_flag,
    COALESCE(dp.team_tag, d.clan) AS display_tag,
    rp."position",
    rp.start_position,
    rp.finish_time - COALESCE(rs.race_start_offset_s, 0::double precision) AS finish_time,
    rp.laps_completed,
    rp.fastest_lap,
    rs.participant_count,
    cr.season_id,
    cr.credits,
    cr.points_finish,
    cr.is_pole,
    cr.is_fastest_lap,
    cr.points_finish + cr.is_pole::integer + cr.is_fastest_lap::integer AS points_total
   FROM base.race_participations rp
     JOIN base.race_sessions rs ON rp.session_id = rs.id
     JOIN base.tracks t ON rs.track_guid = t.guid
     JOIN base.drivers d ON rp.steam_id = d.steam_id
     LEFT JOIN mart.driver_profiles dp ON dp.steam_id = rp.steam_id
     LEFT JOIN career.race_rewards cr ON cr.participation_id = rp.id
  WHERE rs.server = 'career'::text AND rp.is_ai = false
    AND EXISTS (SELECT 1 FROM base.race_participations real_lap
                 WHERE real_lap.session_id = rs.id AND real_lap.is_ai = false
                   AND real_lap.laps_completed >= 1);
