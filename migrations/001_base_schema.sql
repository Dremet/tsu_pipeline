-- Base schema for TSU pipeline
-- Run once against TSU_TEST_POSTGRES_URL

CREATE SCHEMA IF NOT EXISTS base;
CREATE SCHEMA IF NOT EXISTS mart;

-- ── Entities ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS base.drivers (
    steam_id   BIGINT PRIMARY KEY,
    name       TEXT NOT NULL,
    flag       TEXT,
    clan       TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS base.tracks (
    guid       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    level_type TEXT,
    maker_id   BIGINT
);

CREATE TABLE IF NOT EXISTS base.vehicles (
    guid TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

-- ── Race mode ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS base.race_sessions (
    id              TEXT PRIMARY KEY,  -- md5(utc_start_time || '|' || host)
    utc_start_time  TIMESTAMPTZ NOT NULL,
    host            BIGINT NOT NULL,
    track_guid      TEXT NOT NULL REFERENCES base.tracks(guid),
    server          TEXT NOT NULL,
    finished_state  TEXT NOT NULL,
    max_laps        INT,
    participant_count INT,
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS base.race_participations (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES base.race_sessions(id),
    -- human drivers: steam_id set, bot_name null
    -- bots:          steam_id null, bot_name set
    steam_id       BIGINT REFERENCES base.drivers(steam_id),
    is_ai          BOOLEAN NOT NULL DEFAULT false,
    bot_name       TEXT,
    vehicle_guid   TEXT REFERENCES base.vehicles(guid),
    finish_time    FLOAT,
    laps_completed INT,
    last_checkpoint INT,
    position        INT,
    start_position  INT,
    CONSTRAINT chk_ai_identity CHECK (
        (is_ai = false AND steam_id IS NOT NULL AND bot_name IS NULL) OR
        (is_ai = true  AND steam_id IS NULL     AND bot_name IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS base.elo_history (
    participation_id TEXT PRIMARY KEY REFERENCES base.race_participations(id),
    elo_value        FLOAT NOT NULL,
    elo_delta        FLOAT NOT NULL
);

-- ── Hotlapping mode ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS base.hotlap_events (
    id             TEXT PRIMARY KEY,  -- md5(utc_start_time || '|' || host)
    utc_start_time TIMESTAMPTZ NOT NULL,
    host           BIGINT NOT NULL,
    track_guid     TEXT NOT NULL REFERENCES base.tracks(guid),
    server         TEXT NOT NULL,
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per individual (valid) lap driven.
-- Best time per player per event = MIN(lap_time).
CREATE TABLE IF NOT EXISTS base.hotlap_laps (
    id           SERIAL PRIMARY KEY,
    event_id     TEXT NOT NULL REFERENCES base.hotlap_events(id),
    steam_id     BIGINT NOT NULL REFERENCES base.drivers(steam_id),
    vehicle_guid TEXT REFERENCES base.vehicles(guid),
    lap_number   INT NOT NULL,
    lap_time     FLOAT NOT NULL,
    sector_times FLOAT[],
    UNIQUE (event_id, steam_id, lap_number)
);

-- ── Mart views ───────────────────────────────────────────────────────────────
-- Full definitions live in migrations/003_mart_views.sql.
-- Here we drop-and-create to ensure idempotent schema setup.

DROP VIEW IF EXISTS mart.v_driver_profile CASCADE;
DROP VIEW IF EXISTS mart.v_race_results CASCADE;
DROP VIEW IF EXISTS mart.v_hotlap_results CASCADE;
DROP VIEW IF EXISTS mart.v_hotlap_sessions CASCADE;
