-- Keeps the schema's integrity triggers switched on.
--
-- The migration role owns the application's tables (it creates them when it
-- migrates), and a table's owner is ordinarily allowed to run
--     ALTER TABLE ... DISABLE TRIGGER ...
-- which would silence the guards on closed statements, the audit trail, and
-- payment applications. This event trigger refuses any ALTER TABLE that leaves
-- a trigger disabled, unless a superuser issued it.
--
-- It has to be installed by a superuser (on Amazon RDS: a member of
-- rds_superuser), once per database, and lives in a schema neither the
-- migration nor runtime role owns -- so neither can drop or replace it.
-- scripts/local_postgres.sh applies this file to the local databases as the
-- bootstrap administrator. It is safe to apply repeatedly.
--
-- What it does not do: stop the owning role from *removing* a guard with other
-- DDL (DROP TRIGGER, DROP TABLE, ALTER TABLE ... DROP CONSTRAINT). Being able
-- to change the schema is what running migrations means, and the application
-- never issues such statements outside a reviewed migration file.

SET client_min_messages = warning;   -- no notice when re-applied

CREATE SCHEMA IF NOT EXISTS aem_guard;

CREATE OR REPLACE FUNCTION aem_guard.triggers_stay_enabled() RETURNS event_trigger
LANGUAGE plpgsql AS $$
DECLARE
    offending TEXT;
BEGIN
    IF current_setting('is_superuser') = 'on' THEN
        RETURN;
    END IF;
    -- 'O' fires normally and 'A' always; anything else ('D' disabled,
    -- 'R' replica-only) means the trigger no longer guards ordinary writes.
    SELECT format('%s on %s', t.tgname, t.tgrelid::regclass) INTO offending
      FROM pg_catalog.pg_trigger t
     WHERE NOT t.tgisinternal AND t.tgenabled NOT IN ('O', 'A')
     LIMIT 1;
    IF offending IS NOT NULL THEN
        RAISE EXCEPTION 'guard_triggers: trigger % may not be disabled', offending
            USING ERRCODE = 'insufficient_privilege';
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_event_trigger
                    WHERE evtname = 'aem_triggers_stay_enabled') THEN
        CREATE EVENT TRIGGER aem_triggers_stay_enabled
            ON ddl_command_end WHEN TAG IN ('ALTER TABLE')
            EXECUTE FUNCTION aem_guard.triggers_stay_enabled();
    END IF;
END;
$$;
