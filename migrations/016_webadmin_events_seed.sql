-- 016: add the manually hosted event server ("#1 Event Server") to the
-- admin area and seed the per-server web admins from the in-game
-- remoteAdmins lists of the TSU servers (state 2026-07-10).
-- 14505447 (a non-SteamID64 entry in events/hotlapping) is skipped: it can
-- never log in to tsura.org via Steam.
BEGIN;

ALTER TABLE webadmin.server_admins DROP CONSTRAINT IF EXISTS server_admins_server_check;
ALTER TABLE webadmin.server_admins ADD CONSTRAINT server_admins_server_check
    CHECK (server IN ('career', 'tripleheat', 'casual_heat', 'hotlapping', 'events'));

INSERT INTO webadmin.server_admins (server, steam_id, note)
VALUES
    -- tripleheat (game.json remoteAdmins)
    ('tripleheat', 76561197989276622, 'seeded from in-game admins'),
    ('tripleheat', 76561198131829686, 'seeded from in-game admins'),
    ('tripleheat', 76561198096169747, 'seeded from in-game admins'),
    -- casual heat
    ('casual_heat', 76561197989276622, 'seeded from in-game admins'),
    ('casual_heat', 76561198131829686, 'seeded from in-game admins'),
    ('casual_heat', 76561198096169747, 'seeded from in-game admins'),
    -- career
    ('career', 76561198131829686, 'seeded from in-game admins'),
    ('career', 76561198813518085, 'seeded from in-game admins'),
    -- hotlapping
    ('hotlapping', 76561197989276622, 'seeded from in-game admins'),
    ('hotlapping', 76561199107580352, 'seeded from in-game admins'),
    ('hotlapping', 76561198066556060, 'seeded from in-game admins'),
    ('hotlapping', 76561198055146098, 'seeded from in-game admins'),
    ('hotlapping', 76561198131829686, 'seeded from in-game admins'),
    ('hotlapping', 76561198991237382, 'seeded from in-game admins'),
    ('hotlapping', 76561198096169747, 'seeded from in-game admins'),
    ('hotlapping', 76561198056976420, 'seeded from in-game admins'),
    ('hotlapping', 76561197961472772, 'seeded from in-game admins'),
    ('hotlapping', 76561197962301532, 'seeded from in-game admins'),
    ('hotlapping', 76561198813518085, 'seeded from in-game admins'),
    ('hotlapping', 76561199023733877, 'seeded from in-game admins'),
    -- events ("#1 Event Server")
    ('events', 76561197989276622, 'seeded from in-game admins'),
    ('events', 76561199107580352, 'seeded from in-game admins'),
    ('events', 76561198055146098, 'seeded from in-game admins'),
    ('events', 76561198131829686, 'seeded from in-game admins'),
    ('events', 76561198991237382, 'seeded from in-game admins'),
    ('events', 76561198096169747, 'seeded from in-game admins'),
    ('events', 76561198056976420, 'seeded from in-game admins'),
    ('events', 76561197961472772, 'seeded from in-game admins'),
    ('events', 76561198414940358, 'seeded from in-game admins'),
    ('events', 76561198813518085, 'seeded from in-game admins'),
    ('events', 76561198313054452, 'seeded from in-game admins')
ON CONFLICT DO NOTHING;

COMMIT;
