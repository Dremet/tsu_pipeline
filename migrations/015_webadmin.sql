-- 015: cross-server admin rights for the tsura.org admin area.
-- One row = one user may manage one server's admin panel. The panel owner
-- (hardcoded in the website) always has access and is the only one who may
-- edit this table via the UI.
BEGIN;

CREATE SCHEMA IF NOT EXISTS webadmin;

CREATE TABLE IF NOT EXISTS webadmin.server_admins (
    server   text        NOT NULL
             CHECK (server IN ('career', 'tripleheat', 'casual_heat', 'hotlapping')),
    steam_id bigint      NOT NULL,
    note     text,
    added_by bigint,
    added_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (server, steam_id)
);

GRANT USAGE ON SCHEMA webadmin TO tsura;
GRANT SELECT, INSERT, UPDATE, DELETE ON webadmin.server_admins TO tsura;

-- carry over the existing career web admins
INSERT INTO webadmin.server_admins (server, steam_id, note)
SELECT 'career', steam_id, note FROM career.admins
ON CONFLICT DO NOTHING;

COMMIT;
