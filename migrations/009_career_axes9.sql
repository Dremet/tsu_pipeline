-- 009_career_axes9.sql — expand career tuning from 4 to 9 axes.
--
-- New axes (validated on the MX-5 Cup '16, 2026-07-05):
--   grip                  -> .veh steering.grip
--   sliding_gradual_range -> .veh sliding.gradualRange
--   spring_max_length     -> .veh spring.maxLength
--   locking_start_time    -> .veh braking.lockingStartTime
--   oversteering_braking  -> .veh oversteering.braking
-- Apply as postgres (owner of schema career).

BEGIN;

ALTER TABLE career.upgrade_axes
    DROP CONSTRAINT upgrade_axes_axis_check;
ALTER TABLE career.upgrade_axes
    ADD CONSTRAINT upgrade_axes_axis_check CHECK (axis = ANY (ARRAY[
        'top_speed', 'acceleration', 'braking', 'downforce',
        'grip', 'sliding_gradual_range', 'spring_max_length',
        'locking_start_time', 'oversteering_braking']));

ALTER TABLE career.driver_upgrades
    DROP CONSTRAINT driver_upgrades_axis_check;
ALTER TABLE career.driver_upgrades
    ADD CONSTRAINT driver_upgrades_axis_check CHECK (axis = ANY (ARRAY[
        'top_speed', 'acceleration', 'braking', 'downforce',
        'grip', 'sliding_gradual_range', 'spring_max_length',
        'locking_start_time', 'oversteering_braking']));

COMMIT;
