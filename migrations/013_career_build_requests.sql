-- 013_career_build_requests.sql — admin-triggered "build & assign car" queue.
--
-- The website (role tsura) cannot write to the career user's files, so an admin
-- button enqueues a request here; a career-side cron builds the driver's .veh,
-- updates assignments.json and forces it in-game, then marks the row done.
-- Apply as postgres.

BEGIN;

CREATE TABLE career.car_build_requests (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    season_id    integer     NOT NULL REFERENCES career.seasons(id) ON DELETE CASCADE,
    steam_id     bigint      NOT NULL,
    requested_by bigint,
    requested_at timestamptz NOT NULL DEFAULT now(),
    status       text        NOT NULL DEFAULT 'pending',  -- pending / done / error
    processed_at timestamptz,
    note         text
);
CREATE INDEX ON career.car_build_requests (status, requested_at);

GRANT SELECT, INSERT ON career.car_build_requests TO tsura;      -- website enqueues
GRANT SELECT, UPDATE ON career.car_build_requests TO career_ro;  -- career cron processes

COMMIT;
