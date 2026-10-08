-- 023: Casual Heat is retired, a "Fun Modes" server (user `fun`) takes over
--      its ports (7757/7758).
--
-- webadmin.server_admins constrains `server` to the panels that exist, so
-- the new panel (/admin/fun) needs its key in the CHECK. 'casual_heat' stays
-- allowed: it is harmless and keeps old rows/exports valid, but its admin
-- rows are removed -- the panel is gone, nobody can use them.
--
-- Admins for fun: André (owner, also implicit) and McVizn, as agreed
-- 2026-10-08. Race history of casual_heat in base.* / mart.* is NOT touched.
--
-- Apply as the database owner, then check the owner of the table stays `data`
-- (ALTER does not change it). No new tables, so no `SET ROLE data` needed.
BEGIN;

ALTER TABLE webadmin.server_admins DROP CONSTRAINT IF EXISTS server_admins_server_check;
ALTER TABLE webadmin.server_admins ADD CONSTRAINT server_admins_server_check
    CHECK (server IN ('career', 'tripleheat', 'casual_heat', 'hotlapping',
                      'events', 'topdown', 'fun'));

DELETE FROM webadmin.server_admins WHERE server = 'casual_heat';

INSERT INTO webadmin.server_admins (server, steam_id, note) VALUES
    ('fun', 76561197989276622, 'André / Dremet'),
    ('fun', 76561198131829686, 'McVizn')
ON CONFLICT DO NOTHING;

COMMIT;
