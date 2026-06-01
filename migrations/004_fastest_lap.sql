-- Add fastest_lap column to base.race_participations.
-- Historical records (loaded before this migration) will have NULL.
-- New records will have the minimum lap time extracted from checkpoint data.

ALTER TABLE base.race_participations
    ADD COLUMN IF NOT EXISTS fastest_lap FLOAT;
