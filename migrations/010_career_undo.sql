-- 010_career_undo.sql — one-step undo for upgrade purchases.
--
-- Tracks each driver's most recent purchase per season. The website's
-- /garage/undo route may revert exactly this purchase once (undone flips to
-- true; the next buy resets it). Refunds need no bookkeeping: the credit
-- balance derives "spent" from driver_upgrades tiers.
-- Apply as postgres.

BEGIN;

CREATE TABLE career.last_purchase (
    season_id  integer     NOT NULL REFERENCES career.seasons(id) ON DELETE CASCADE,
    steam_id   bigint      NOT NULL,
    axis       text        NOT NULL,
    tier_after integer     NOT NULL CHECK (tier_after >= 1),
    bought_at  timestamptz NOT NULL DEFAULT now(),
    undone     boolean     NOT NULL DEFAULT false,
    PRIMARY KEY (season_id, steam_id)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON career.last_purchase TO tsura;
GRANT SELECT ON career.last_purchase TO data;
GRANT SELECT ON career.last_purchase TO career_ro;

COMMIT;
