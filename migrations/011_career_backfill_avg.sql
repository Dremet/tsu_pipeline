-- 011_career_backfill_avg.sql — credits for non-participants = field average.
--
-- Replaces the old fixed (credit_first+credit_last)/2 backfill (which only
-- covered races before a driver joined) with: for EVERY scored race in the
-- season, every enrolled driver who did NOT participate gets that race's
-- AVERAGE participant credits. Covers both late joiners and skipped races.
--
-- Columns unchanged (only `balance` is read by the website); races_missed now
-- means "scored races the driver did not participate in". Apply as postgres.

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
        - COALESCE(sp.spent, 0))::bigint    AS balance
FROM career.enrollments e
JOIN career.seasons s ON s.id = e.season_id
LEFT JOIN earned   ea ON ea.season_id = e.season_id AND ea.steam_id = e.steam_id
LEFT JOIN spent    sp ON sp.season_id = e.season_id AND sp.steam_id = e.steam_id
LEFT JOIN backfill bf ON bf.season_id = e.season_id AND bf.steam_id = e.steam_id;
