-- Migration 006: race_start_offset_s
-- Stores the raw game-clock offset (in seconds) at the moment the race started,
-- computed from checkpointTimes[0]["times"][0] in the event JSON.
-- finish_time in race_participations is the raw game-clock tick / 10000;
-- subtracting this offset yields the net race duration.
-- NULL = offset unknown (historical sessions loaded before this migration).
-- mart.v_race_results uses COALESCE(race_start_offset_s, 0) as fallback.

ALTER TABLE base.race_sessions
    ADD COLUMN IF NOT EXISTS race_start_offset_s FLOAT;
