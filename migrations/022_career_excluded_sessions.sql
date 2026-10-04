-- Durable exclusions from Career scoring. Original results stay in base.*.
-- Apply as postgres; this does not change how game sessions are started.
BEGIN;

CREATE TABLE IF NOT EXISTS career.excluded_sessions (
    session_id text PRIMARY KEY,
    reason text NOT NULL CHECK (length(trim(reason)) > 0),
    excluded_at timestamptz NOT NULL DEFAULT now(),
    excluded_by text NOT NULL DEFAULT session_user
);
-- Deliberately no cascading FK: an exclusion survives deletion/re-import.
GRANT SELECT ON career.excluded_sessions TO data, tsura, career_ro;

CREATE OR REPLACE FUNCTION career.prevent_excluded_reward()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM career.excluded_sessions
               WHERE session_id = NEW.session_id) THEN
        RETURN NULL;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS prevent_excluded_reward ON career.race_rewards;
CREATE TRIGGER prevent_excluded_reward
BEFORE INSERT OR UPDATE ON career.race_rewards
FOR EACH ROW EXECUTE FUNCTION career.prevent_excluded_reward();

CREATE OR REPLACE FUNCTION career.remove_excluded_rewards()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM career.race_rewards WHERE session_id = NEW.session_id;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS remove_excluded_rewards ON career.excluded_sessions;
CREATE TRIGGER remove_excluded_rewards
AFTER INSERT OR UPDATE ON career.excluded_sessions
FOR EACH ROW EXECUTE FUNCTION career.remove_excluded_rewards();

-- Preserve the installed view definitions, columns and dependent views.
DO $$
DECLARE
    view_name text;
    definition text;
BEGIN
    FOREACH view_name IN ARRAY ARRAY['v_career_race_sessions', 'v_career_results']
    LOOP
        definition := pg_get_viewdef(('mart.' || view_name)::regclass, true);
        IF position('career.excluded_sessions' IN definition) = 0 THEN
            definition := rtrim(rtrim(definition), ';');
            EXECUTE format(
                'CREATE OR REPLACE VIEW mart.%I AS %s
                 AND NOT EXISTS (SELECT 1 FROM career.excluded_sessions excluded
                                 WHERE excluded.session_id = rs.id)',
                view_name, definition);
        END IF;
    END LOOP;
END;
$$;
COMMIT;
