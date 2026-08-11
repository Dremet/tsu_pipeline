-- 020: let the admin area manage the topdown heat server.
--
-- webadmin.server_admins constrains `server` to the panels that existed when
-- it was written (migration 015, extended by 016 for the event server). The
-- topdown panel (/admin/topdown) is new, so without this constraint change
-- granting anyone topdown rights fails with a check violation -- the owner
-- would keep working (he bypasses the table) and nobody else could be added,
-- which is exactly the kind of thing that only shows up at the worst moment.
--
-- No rows are seeded: topdown's in-game admins are already in the config
-- (/srv/tsura/server_config/topdown.json) and the panel's admin page is the
-- place to grant web access deliberately.
BEGIN;

ALTER TABLE webadmin.server_admins DROP CONSTRAINT IF EXISTS server_admins_server_check;
ALTER TABLE webadmin.server_admins ADD CONSTRAINT server_admins_server_check
    CHECK (server IN ('career', 'tripleheat', 'casual_heat', 'hotlapping',
                      'events', 'topdown'));

COMMIT;
