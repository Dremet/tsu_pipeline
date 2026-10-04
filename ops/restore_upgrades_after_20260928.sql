-- Restore Shyguy1001 and JBence to the builds used on 2026-09-28.
-- Tiers reconstructed from saved vehicles generated at 20:55; total costs
-- match the server's race-night build announcements (6100 and 5450 credits).
-- Expected current tiers include the earlier incident refunds.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
LOCK TABLE career.driver_upgrades,career.last_purchase,career.enrollments,
           career.seasons,career.upgrade_axes,career.race_rewards,career.objectives
IN SHARE ROW EXCLUSIVE MODE;
CREATE TEMP TABLE reset_targets(steam_id bigint,axis text,before_tier int,target_tier int,cost int);
INSERT INTO reset_targets VALUES
 (76561198036464386,'top_speed',10,10,250),
 (76561198036464386,'acceleration',4,3,500),
 (76561198036464386,'braking',0,0,200),
 (76561198036464386,'grip',6,4,300),
 (76561198036464386,'downforce',0,0,300),
 (76561198036464386,'sliding_gradual_range',3,3,100),
 (76561198036464386,'spring_max_length',2,2,100),
 (76561198036464386,'locking_start_time',3,3,100),
 (76561198036464386,'oversteering_braking',0,1,100),
 (76561198067261467,'top_speed',10,9,250),
 (76561198067261467,'acceleration',3,4,500),
 (76561198067261467,'braking',0,0,200),
 (76561198067261467,'grip',7,4,300),
 (76561198067261467,'downforce',0,0,300),
 (76561198067261467,'sliding_gradual_range',0,0,100),
 (76561198067261467,'spring_max_length',0,0,100),
 (76561198067261467,'locking_start_time',0,0,100),
 (76561198067261467,'oversteering_braking',0,0,100);
CREATE TEMP TABLE reset_upgrades_before AS SELECT * FROM career.driver_upgrades;
CREATE TEMP TABLE reset_purchases_before AS SELECT * FROM career.last_purchase;
CREATE TEMP TABLE reset_balances_before AS SELECT * FROM mart.v_career_credit_balance;
CREATE TEMP TABLE reset_rewards_before AS SELECT * FROM career.race_rewards;
CREATE TEMP TABLE reset_objectives_before AS SELECT * FROM career.objectives;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM career.seasons WHERE id=2 AND status='active')
       OR EXISTS (SELECT 1 FROM reset_targets t
          LEFT JOIN career.driver_upgrades du
            ON du.season_id=2 AND du.steam_id=t.steam_id AND du.axis=t.axis
          JOIN career.upgrade_axes ua ON ua.season_id=2 AND ua.axis=t.axis
          WHERE COALESCE(du.tier,0)<>t.before_tier OR ua.cost_per_tier<>t.cost)
       OR NOT EXISTS (SELECT 1 FROM reset_balances_before
                      WHERE season_id=2 AND steam_id=76561198036464386 AND balance=101)
       OR NOT EXISTS (SELECT 1 FROM reset_balances_before
                      WHERE season_id=2 AND steam_id=76561198067261467 AND balance=123)
       OR (SELECT count(*) FROM career.last_purchase
           WHERE season_id=2 AND steam_id IN (76561198036464386,76561198067261467)
             AND undone)<>2 THEN
        RAISE EXCEPTION 'Driver state changed; reset aborted before any refund';
    END IF;
END;
$$;

INSERT INTO career.driver_upgrades(season_id,steam_id,axis,tier)
SELECT 2,steam_id,axis,target_tier FROM reset_targets WHERE target_tier>0
ON CONFLICT(season_id,steam_id,axis) DO UPDATE
SET tier=EXCLUDED.tier,updated_at=now()
WHERE career.driver_upgrades.tier IS DISTINCT FROM EXCLUDED.tier;
DELETE FROM career.driver_upgrades du USING reset_targets t
WHERE du.season_id=2 AND du.steam_id=t.steam_id AND du.axis=t.axis AND t.target_tier=0;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM reset_targets t LEFT JOIN career.driver_upgrades du
               ON du.season_id=2 AND du.steam_id=t.steam_id AND du.axis=t.axis
               WHERE COALESCE(du.tier,0)<>t.target_tier) THEN
        RAISE EXCEPTION 'Target tiers not restored; rolling back';
    END IF;
    IF EXISTS (SELECT 1 FROM reset_balances_before b
        FULL JOIN mart.v_career_credit_balance a USING(season_id,steam_id)
        WHERE a.steam_id IS NULL OR b.steam_id IS NULL
           OR a.balance <> b.balance + CASE WHEN a.season_id=2 THEN COALESCE(
               (SELECT SUM((before_tier-target_tier)*cost)
                FROM reset_targets t WHERE t.steam_id=a.steam_id),0) ELSE 0 END
           OR a.earned<>b.earned OR a.backfill<>b.backfill
           OR a.objective_credits<>b.objective_credits) THEN
        RAISE EXCEPTION 'Unexpected balance/income change; rolling back';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM mart.v_career_credit_balance
                   WHERE season_id=2 AND steam_id=76561198036464386
                     AND spent=6100 AND balance=1101)
       OR NOT EXISTS (SELECT 1 FROM mart.v_career_credit_balance
                      WHERE season_id=2 AND steam_id=76561198067261467
                        AND spent=5450 AND balance=773) THEN
        RAISE EXCEPTION 'Unexpected final driver balances; rolling back';
    END IF;
    IF EXISTS ((SELECT * FROM reset_upgrades_before
                WHERE season_id<>2 OR steam_id NOT IN (76561198036464386,76561198067261467)
                EXCEPT SELECT * FROM career.driver_upgrades)
        UNION ALL (SELECT * FROM career.driver_upgrades
                   WHERE season_id<>2 OR steam_id NOT IN (76561198036464386,76561198067261467)
                   EXCEPT SELECT * FROM reset_upgrades_before))
       OR EXISTS ((SELECT * FROM career.last_purchase EXCEPT SELECT * FROM reset_purchases_before)
                  UNION ALL (SELECT * FROM reset_purchases_before EXCEPT SELECT * FROM career.last_purchase))
       OR EXISTS ((SELECT * FROM career.race_rewards EXCEPT SELECT * FROM reset_rewards_before)
                  UNION ALL (SELECT * FROM reset_rewards_before EXCEPT SELECT * FROM career.race_rewards))
       OR EXISTS ((SELECT * FROM career.objectives EXCEPT SELECT * FROM reset_objectives_before)
                  UNION ALL (SELECT * FROM reset_objectives_before EXCEPT SELECT * FROM career.objectives)) THEN
        RAISE EXCEPTION 'Unrelated driver/purchase/reward/objective changed; rolling back';
    END IF;
END;
$$;
COMMIT;

SELECT jsonb_build_object(
 'before_balances',(SELECT jsonb_agg(to_jsonb(t)) FROM reset_balances_before t),
 'before_upgrades',(SELECT jsonb_agg(to_jsonb(t)) FROM reset_upgrades_before t),
 'targets',(SELECT jsonb_agg(to_jsonb(t)) FROM reset_targets t),
 'after_balances',(SELECT jsonb_agg(to_jsonb(t)) FROM mart.v_career_credit_balance t
                   WHERE season_id=2 AND steam_id IN (76561198036464386,76561198067261467)),
 'after_upgrades',(SELECT jsonb_agg(to_jsonb(t)) FROM career.driver_upgrades t
                  WHERE season_id=2 AND steam_id IN (76561198036464386,76561198067261467)),
 'completed_at',now()
);
