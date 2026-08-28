-- 021: topdown heat statistics (McVizn's PRIO A).
--
-- A topdown heat is not a race but three rounds, each a qualifying followed by
-- a race, and the qualifying scores points of its own. The existing base.*
-- tables describe single races and cannot express any of that: there is no heat
-- to group races under, nowhere to put a qualifying that produced no result
-- file, and no way to say "this player was watching, not racing".
--
-- Four tables, each with the smallest job it can have:
--
--   topdown_heats        the heat itself
--   topdown_events       its six events; races point at their race_session
--   topdown_points       points per player per event, qualifying included
--   topdown_race_status  who took part in a race, and how it ended for them
--   topdown_data_faults  what did not add up, so it cannot be missed
--
-- Idempotency (section 7) is structural, not a code convention: every table has
-- a natural primary key built from the heat, the event and the player, so
-- re-reading the same files updates rows instead of counting anything twice.
--
-- Nothing here touches the existing tables or views. Loading topdown stats can
-- be switched off by simply not calling the loader.

BEGIN;

-- The pipeline runs as `data`, so the tables have to belong to `data` like
-- every other base.* table. Applied as `postgres` without this, they come out
-- postgres-owned and every insert from the pipeline fails on permissions --
-- and only at ingest time, long after the migration reported success. The
-- earlier migrations did not need this because they altered existing tables
-- instead of creating them.
SET ROLE data;

-- ── The heat ────────────────────────────────────────────────────────────────
--
-- Keyed by heat_uid, not heat_id. The controller's counter lives in
-- topdown_state.json and has been reset before: the test heats of 2026-07-31
-- carry ids (99, 100) that the live counter will hand out again. heat_uid is
-- "<UTC timestamp>-<heat_id>" and stays unique across restarts.

CREATE TABLE IF NOT EXISTS base.topdown_heats (
    heat_uid     TEXT PRIMARY KEY,
    heat_id      INT  NOT NULL,
    server       TEXT NOT NULL,
    rounds_total INT,
    started_at   TIMESTAMPTZ,
    ended_at     TIMESTAMPTZ,
    -- 'finished', 'abandoned' (last human left) or 'superseded' (restarted by
    -- vote or admin). An abandoned heat still counts towards Total Heats.
    end_reason   TEXT,
    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Its events ──────────────────────────────────────────────────────────────
--
-- session_id is null for every qualifying: those run in hotlapping mode and
-- move_raw_files.sh discards their result file, so there is no race_session to
-- point at. The row still has to exist, because the qualifying awards points
-- and holds the pole position.

CREATE TABLE IF NOT EXISTS base.topdown_events (
    heat_uid     TEXT NOT NULL REFERENCES base.topdown_heats(heat_uid)
                      ON DELETE CASCADE,
    round        INT  NOT NULL,
    phase        TEXT NOT NULL CHECK (phase IN ('quali', 'race')),
    session_id   TEXT REFERENCES base.race_sessions(id),
    track_guid   TEXT REFERENCES base.tracks(guid),
    vehicle_guid TEXT REFERENCES base.vehicles(guid),
    laps         INT,
    started_at   TIMESTAMPTZ,
    ended_at     TIMESTAMPTZ,
    PRIMARY KEY (heat_uid, round, phase),
    CONSTRAINT chk_quali_has_no_session CHECK (
        phase = 'race' OR session_id IS NULL
    )
);

-- One race_session belongs to at most one heat event, so a re-read cannot file
-- the same race under two rounds.
CREATE UNIQUE INDEX IF NOT EXISTS topdown_events_session_uniq
    ON base.topdown_events (session_id) WHERE session_id IS NOT NULL;

-- ── Points ──────────────────────────────────────────────────────────────────
--
-- Read straight from TSU's own session stats file rather than recomputed from
-- a points table, which is what makes section 8 checkable: the same file also
-- states the total those points have to add up to.
--
-- Humans only. Bots share one steam id between them and hold no statistics.

CREATE TABLE IF NOT EXISTS base.topdown_points (
    heat_uid TEXT   NOT NULL,
    round    INT    NOT NULL,
    phase    TEXT   NOT NULL,
    steam_id BIGINT NOT NULL REFERENCES base.drivers(steam_id),
    points   INT    NOT NULL,
    PRIMARY KEY (heat_uid, round, phase, steam_id),
    FOREIGN KEY (heat_uid, round, phase)
        REFERENCES base.topdown_events (heat_uid, round, phase) ON DELETE CASCADE
);

-- ── Participation and outcome ───────────────────────────────────────────────
--
-- A row here means "this player took part in this event" -- that is, was a
-- racer and not a spectator when the grid was locked in. A player who watched
-- gets no row at all, rather than a row with empty fields (section 4.8).
--
-- Qualifying gets rows too, even though only races count towards Total Races:
-- a pole position belongs to whoever was actually driving the qualifying, so
-- the same participation question has to be answered there.
--
-- The three evidence flags are kept so a surprising verdict can be explained
-- without re-reading the journal by hand (section 0: points and statuses must
-- stay traceable).

CREATE TABLE IF NOT EXISTS base.topdown_race_status (
    heat_uid            TEXT   NOT NULL,
    round               INT    NOT NULL,
    phase               TEXT   NOT NULL CHECK (phase IN ('quali', 'race')),
    steam_id            BIGINT NOT NULL REFERENCES base.drivers(steam_id),
    status              TEXT   NOT NULL CHECK (status IN
                            ('completed', 'dnf', 'disconnect',
                             'disqualified', 'unknown')),
    -- Which roster snapshot settled the grid: 'event_start' (preferred),
    -- 'event_init' (fallback), or null when no journal existed. The two are up
    -- to half a minute apart and players do change their mind in between.
    grid_basis          TEXT,
    retired             BOOLEAN NOT NULL DEFAULT false,
    spectated           BOOLEAN NOT NULL DEFAULT false,
    disconnected_at_end BOOLEAN NOT NULL DEFAULT false,
    -- Null for a qualifying (no result file) and for a participant the result
    -- file never mentions -- someone who disconnected before the first
    -- checkpoint is still a participant who did not finish.
    participation_id    TEXT REFERENCES base.race_participations(id),
    PRIMARY KEY (heat_uid, round, phase, steam_id),
    FOREIGN KEY (heat_uid, round, phase)
        REFERENCES base.topdown_events (heat_uid, round, phase) ON DELETE CASCADE
);

-- ── Data faults ─────────────────────────────────────────────────────────────
--
-- Section 8 asks for inconsistencies to be treated as faults rather than
-- quietly ignored -- but the pipeline stops dead on an exception
-- (ERROR_OCCURED halts every server's ingest until someone removes the file),
-- so a single odd heat must not be fatal. Recording the fault keeps it visible
-- without holding up the other six servers.
--
-- Kinds in use:
--   'points_mismatch'   per-event points do not add up to the session total
--   'round_disagreement' heat stamp and session disagree on which race this is
--   'missing_journal'   race has results but no status journal
--   'null_event_stats'  TSU wrote a null result file for a finished race

CREATE TABLE IF NOT EXISTS base.topdown_data_faults (
    id         BIGSERIAL PRIMARY KEY,
    heat_uid   TEXT,
    round      INT,
    phase      TEXT,
    session_id TEXT,
    kind       TEXT NOT NULL,
    detail     TEXT,
    noticed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS topdown_data_faults_kind
    ON base.topdown_data_faults (kind, noticed_at DESC);

COMMIT;
