-- ELO bootstrap: stores legacy ELO values imported from the old racing DB.
-- Used by update_elo as fallback when a driver has no elo_history entries yet.
-- Once a driver races on the new Tripleheat pipeline, their elo_history takes
-- over and elo_bootstrap becomes historical documentation only.

CREATE TABLE IF NOT EXISTS base.elo_bootstrap (
    steam_id      BIGINT PRIMARY KEY REFERENCES base.drivers(steam_id),
    elo_value     FLOAT   NOT NULL,
    number_races  INT,
    last_race_at  TIMESTAMPTZ,
    source        TEXT    NOT NULL DEFAULT 'racing_db_migration',
    imported_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
