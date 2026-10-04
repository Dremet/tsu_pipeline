-- One-time correction, authorized by the owner on 2026-10-03.
-- Run as postgres after 022, with upgrade writes temporarily paused.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SET LOCAL TIME ZONE 'Europe/Berlin';
LOCK TABLE career.driver_upgrades, career.last_purchase, career.enrollments,
           career.race_rewards, career.objectives, career.excluded_sessions
IN SHARE ROW EXCLUSIVE MODE;

CREATE TEMP TABLE repair_balances_before AS
SELECT * FROM mart.v_career_credit_balance WHERE season_id=2;
CREATE TEMP TABLE repair_rewards_before AS
SELECT * FROM career.race_rewards
WHERE session_id <> '59ca1b40a0968ae0cac9ea74ae068733';
CREATE TEMP TABLE repair_upgrades_before AS SELECT * FROM career.driver_upgrades;
CREATE TEMP TABLE repair_purchases_before AS SELECT * FROM career.last_purchase;
CREATE TEMP TABLE repair_objectives_before AS SELECT * FROM career.objectives;
CREATE TEMP TABLE repair_base_before AS
SELECT * FROM base.race_sessions WHERE id='59ca1b40a0968ae0cac9ea74ae068733';
CREATE TEMP TABLE repair_state_before AS
SELECT jsonb_build_object(
    'captured_at',now(),
    'balances',(SELECT jsonb_agg(to_jsonb(t)) FROM repair_balances_before t),
    'upgrades',(SELECT jsonb_agg(to_jsonb(t)) FROM repair_upgrades_before t),
    'last_purchase',(SELECT jsonb_agg(to_jsonb(t)) FROM repair_purchases_before t),
    'objectives',(SELECT jsonb_agg(to_jsonb(t)) FROM repair_objectives_before t),
    'incident_rewards',(SELECT jsonb_agg(to_jsonb(t)) FROM career.race_rewards t
                        WHERE session_id='59ca1b40a0968ae0cac9ea74ae068733')
) AS snapshot;

CREATE TEMP TABLE repair_targets(steam_id bigint,axis text,tier_before int,cost int,
                                balance_before int,changed_at timestamptz,is_last boolean);
INSERT INTO repair_targets VALUES
 (76561198813518085,'top_speed',7,250,91,'2026-10-03 01:09:29.721669+02',true),
 (76561198036464386,'grip',7,300,1,'2026-10-03 03:34:31.27147+02',true),
 (76561199023733877,'oversteering_braking',3,100,99,'2026-10-03 21:45:07.756596+02',true),
 (76561199023733877,'top_speed',8,250,99,'2026-10-03 21:45:01.039667+02',false),
 (76561198071671577,'braking',4,200,4,'2026-10-03 22:50:54.241686+02',true),
 (76561198067261467,'grip',8,300,23,'2026-10-03 23:04:21.056056+02',true);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM career.seasons WHERE id=2 AND status='active')
       OR (SELECT count(*) FROM repair_balances_before) <> 20 THEN
        RAISE EXCEPTION 'Season/enrollment state changed; correction aborted';
    END IF;
    IF EXISTS (SELECT 1 FROM career.excluded_sessions
               WHERE session_id='59ca1b40a0968ae0cac9ea74ae068733') THEN
        RAISE EXCEPTION 'Incident already excluded; refusing a second refund';
    END IF;
    IF (SELECT count(*) FROM career.race_rewards
        WHERE session_id='59ca1b40a0968ae0cac9ea74ae068733') <> 1
       OR NOT EXISTS (SELECT 1 FROM career.race_rewards
                      WHERE session_id='59ca1b40a0968ae0cac9ea74ae068733'
                        AND steam_id=76561198047389943 AND season_id=2
                        AND credits=200 AND points_finish=20
                        AND is_pole AND is_fastest_lap) THEN
        RAISE EXCEPTION 'Incident reward state changed; correction aborted';
    END IF;
    IF EXISTS (SELECT 1 FROM repair_targets t
               LEFT JOIN repair_balances_before b USING(steam_id)
               LEFT JOIN career.driver_upgrades du
                 ON du.season_id=2 AND du.steam_id=t.steam_id AND du.axis=t.axis
               LEFT JOIN career.upgrade_axes ua ON ua.season_id=2 AND ua.axis=t.axis
               WHERE b.balance IS DISTINCT FROM t.balance_before
                  OR du.tier IS DISTINCT FROM t.tier_before
                  OR du.updated_at IS DISTINCT FROM t.changed_at
                  OR ua.cost_per_tier IS DISTINCT FROM t.cost)
       OR EXISTS (SELECT 1 FROM repair_targets t
                  LEFT JOIN career.last_purchase lp
                    ON lp.season_id=2 AND lp.steam_id=t.steam_id
                  WHERE t.is_last AND (lp.axis IS DISTINCT FROM t.axis
                     OR lp.tier_after IS DISTINCT FROM t.tier_before
                     OR lp.bought_at IS DISTINCT FROM t.changed_at
                     OR lp.undone IS DISTINCT FROM false)) THEN
        RAISE EXCEPTION 'Balance/last purchase/tier state changed; correction aborted';
    END IF;
END;
$$;

INSERT INTO career.excluded_sessions(session_id,reason,excluded_by)
VALUES ('59ca1b40a0968ae0cac9ea74ae068733',
        'Unauthorized solo Career race on 2026-09-30; owner requested exclusion and credit correction.',
        'Codex, authorized by Dremet');
UPDATE career.driver_upgrades du SET tier=du.tier-1,updated_at=now()
FROM repair_targets t WHERE du.season_id=2 AND du.steam_id=t.steam_id AND du.axis=t.axis;
UPDATE career.last_purchase lp SET undone=true
WHERE season_id=2 AND steam_id IN (SELECT steam_id FROM repair_targets);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM repair_balances_before b
        FULL JOIN (SELECT * FROM mart.v_career_credit_balance WHERE season_id=2) a
          USING(season_id,steam_id)
        WHERE a.steam_id IS NULL OR b.steam_id IS NULL
           OR a.balance <> b.balance - 200 + COALESCE(
              (SELECT SUM(cost) FROM repair_targets t WHERE t.steam_id=a.steam_id),0)
           OR a.backfill <> b.backfill-200
           OR a.earned <> b.earned OR a.objective_credits <> b.objective_credits
           OR a.balance < 0
    ) THEN
        RAISE EXCEPTION 'Unexpected balance changes; rolling back';
    END IF;
    IF EXISTS ((SELECT * FROM repair_rewards_before
                EXCEPT SELECT * FROM career.race_rewards)
               UNION ALL (SELECT * FROM career.race_rewards
                          EXCEPT SELECT * FROM repair_rewards_before)) THEN
        RAISE EXCEPTION 'Regular rewards changed; rolling back';
    END IF;
    IF EXISTS (SELECT 1 FROM career.race_rewards
               WHERE session_id='59ca1b40a0968ae0cac9ea74ae068733')
       OR EXISTS (SELECT 1 FROM mart.v_career_results
                  WHERE session_id='59ca1b40a0968ae0cac9ea74ae068733')
       OR EXISTS (SELECT 1 FROM mart.v_career_race_sessions
                  WHERE session_id='59ca1b40a0968ae0cac9ea74ae068733') THEN
        RAISE EXCEPTION 'Incident still scored/visible; rolling back';
    END IF;
    IF EXISTS ((SELECT * FROM repair_objectives_before
                EXCEPT SELECT * FROM career.objectives)
               UNION ALL (SELECT * FROM career.objectives
                          EXCEPT SELECT * FROM repair_objectives_before)) THEN
        RAISE EXCEPTION 'Objectives changed; rolling back';
    END IF;
    IF EXISTS ((SELECT * FROM repair_base_before
                EXCEPT SELECT * FROM base.race_sessions)
               UNION ALL (SELECT * FROM base.race_sessions
                          WHERE id='59ca1b40a0968ae0cac9ea74ae068733'
                          EXCEPT SELECT * FROM repair_base_before)) THEN
        RAISE EXCEPTION 'Original race metadata changed; rolling back';
    END IF;
    IF (SELECT count(*) FROM career.driver_upgrades a
        JOIN repair_upgrades_before b USING(season_id,steam_id,axis)
        WHERE a IS DISTINCT FROM b) <> 6
       OR (SELECT count(*) FROM career.last_purchase a
           JOIN repair_purchases_before b USING(season_id,steam_id)
           WHERE a IS DISTINCT FROM b) <> 5 THEN
        RAISE EXCEPTION 'Unexpected upgrade/purchase change count; rolling back';
    END IF;
END;
$$;
COMMIT;

SELECT jsonb_build_object(
    'before',(SELECT snapshot FROM repair_state_before),
    'after',jsonb_build_object(
        'balances',(SELECT jsonb_agg(to_jsonb(t)) FROM mart.v_career_credit_balance t WHERE season_id=2),
        'upgrades',(SELECT jsonb_agg(to_jsonb(t)) FROM career.driver_upgrades t WHERE season_id=2),
        'last_purchase',(SELECT jsonb_agg(to_jsonb(t)) FROM career.last_purchase t WHERE season_id=2),
        'excluded_sessions',(SELECT jsonb_agg(to_jsonb(t)) FROM career.excluded_sessions t),
        'regular_reward_count',(SELECT count(*) FROM career.race_rewards WHERE season_id=2)
    )
);
