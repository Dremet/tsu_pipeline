-- 019: show AI drivers in race results (needed for the topdown heat server).
--
-- Topdown fills its heats with bots, so a race is typically one human against
-- seven AI. The view hid every bot (inner join on base.drivers plus
-- "WHERE is_ai = false"), which made a topdown race look like a single driver
-- finishing fourth of four. The rows were always in base.race_participations --
-- is_ai/bot_name and the chk_ai_identity constraint have been there all along --
-- only the mart view dropped them.
--
-- Nothing else changes: topdown is the only server with AI participations
-- (99 bot rows on 2026-08-11; every other server has exactly 0).
--
-- Careful: human_participant_count used to be a plain count(*) over the
-- partition, which only equalled the human count because of the WHERE clause.
-- With bots in the view it has to filter explicitly, otherwise the "at least
-- four humans" rule on /races would start counting bots for every server.

BEGIN;

CREATE OR REPLACE VIEW mart.v_race_results AS
 SELECT rp.id AS participation_id,
    rs.id AS session_id,
    rs.utc_start_time,
    rs.server,
    rs.finished_state,
    rs.track_guid,
    t.name AS track_name,
    t.level_type AS track_type,
    rp.steam_id,
    COALESCE(d.name, rp.bot_name) AS driver_name,
    d.flag AS driver_flag,
    d.clan AS driver_clan,
    rp.vehicle_guid,
    v.name AS vehicle_name,
    rp."position",
    rp.finish_time - COALESCE(rs.race_start_offset_s, 0::double precision) AS finish_time,
    rp.laps_completed,
    rs.participant_count,
    eh.elo_value,
    eh.elo_delta,
    COALESCE(( SELECT eh2.elo_value
           FROM base.elo_history eh2
             JOIN base.race_participations rp2 ON rp2.id = eh2.participation_id
             JOIN base.race_sessions rs2 ON rs2.id = rp2.session_id
          WHERE rp2.steam_id = rp.steam_id
          ORDER BY rs2.utc_start_time DESC
         LIMIT 1), eb.elo_value) AS current_elo,
    count(*) FILTER (WHERE rp.is_ai = false) OVER (PARTITION BY rs.id)
        AS human_participant_count,
    rp.fastest_lap,
    COALESCE(dp.team_tag, d.clan) AS display_tag,
    rp.start_position,
    -- Appended rather than slotted in next to driver_name: CREATE OR REPLACE
    -- can only add columns at the end, and going through DROP would take the
    -- view's grants (the website reads it as `tsura`) with it.
    rp.is_ai
   FROM base.race_participations rp
     JOIN base.race_sessions rs ON rp.session_id = rs.id
     JOIN base.tracks t ON rs.track_guid = t.guid
     LEFT JOIN base.drivers d ON rp.steam_id = d.steam_id
     LEFT JOIN base.vehicles v ON rp.vehicle_guid = v.guid
     LEFT JOIN base.elo_history eh ON rp.id = eh.participation_id
     LEFT JOIN base.elo_bootstrap eb ON rp.steam_id = eb.steam_id
     LEFT JOIN mart.driver_profiles dp ON dp.steam_id = rp.steam_id;

COMMIT;
