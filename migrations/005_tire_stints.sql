-- Tire telemetry tables from *_event_details.log
-- Apply AFTER 001_base_schema.sql and 004_fastest_lap.sql.
-- The mart.v_tire_stints view is defined in 003_mart_views.sql — re-run that after this.

CREATE TABLE IF NOT EXISTS base.race_tire_compounds (
    session_id      TEXT     NOT NULL REFERENCES base.race_sessions(id),
    compound_index  SMALLINT NOT NULL,
    compound_name   TEXT     NOT NULL,    -- "Soft", "Medium", "Hard"
    max_wear        INTEGER  NOT NULL,    -- raw value for 100% worn (varies per track/car)
    max_performance FLOAT    NOT NULL,    -- relative grip (1.0 = best)
    PRIMARY KEY (session_id, compound_index)
);

-- One row per human driver per completed lap. Only for sessions that have tire data.
-- tire_wear: cumulative raw value (0 = new tires, max_wear = fully worn)
-- fuel_remaining: NULL when the race has no fuel tracking (no MaxFuel header)
-- stint_number: 1-based; increments at each real tire change (PitOut with new tires)
CREATE TABLE IF NOT EXISTS base.race_lap_telemetry (
    id               TEXT     PRIMARY KEY,  -- MD5(participation_id | lap_number)
    participation_id TEXT     NOT NULL REFERENCES base.race_participations(id),
    session_id       TEXT     NOT NULL REFERENCES base.race_sessions(id),
    lap_number       SMALLINT NOT NULL,     -- 1-based completed laps
    compound_name    TEXT     NOT NULL,     -- "Soft", "Medium", "Hard"
    tire_wear        INTEGER  NOT NULL,     -- raw; 0 = new → max_wear = worn
    fuel_remaining   INTEGER,              -- raw remaining; NULL if no fuel tracking
    hit_points       INTEGER  NOT NULL,    -- 10000 = undamaged
    stint_number     SMALLINT NOT NULL,    -- 1-based; new stint = tire change
    UNIQUE (participation_id, lap_number)
);

GRANT SELECT ON base.race_tire_compounds TO tsura;
GRANT SELECT ON base.race_lap_telemetry  TO tsura;
