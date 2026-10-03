-- Fresh, isolated service database only. Run through psql -X -v ON_ERROR_STOP=1
-- with service, expected_database, migration_role and runtime_role variables.
-- Names are not credentials. Passwords and role provisioning remain external.
BEGIN;
SELECT set_config('kerosene.cell.service', :'service', true),
       set_config('kerosene.cell.expected_database', :'expected_database', true),
       set_config('kerosene.cell.migration_role', :'migration_role', true),
       set_config('kerosene.cell.runtime_role', :'runtime_role', true);

DO $cell$
DECLARE
    service_name text := current_setting('kerosene.cell.service');
    migration_name text := current_setting('kerosene.cell.migration_role');
    runtime_name text := current_setting('kerosene.cell.runtime_role');
    migration_oid oid;
    runtime_oid oid;
    allowed_schemas text[];
    schema_name text;
    object_record record;
BEGIN
    IF service_name NOT IN ('core', 'kfe')
       OR current_database() <> current_setting('kerosene.cell.expected_database')
       OR migration_name = runtime_name THEN
        RAISE EXCEPTION 'Invalid isolated service database binding';
    END IF;
    SELECT oid INTO migration_oid FROM pg_roles
      WHERE rolname = migration_name AND rolcanlogin AND NOT rolsuper
        AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls;
    SELECT oid INTO runtime_oid FROM pg_roles
      WHERE rolname = runtime_name AND rolcanlogin AND NOT rolsuper AND NOT rolinherit
        AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls;
    IF migration_oid IS NULL OR runtime_oid IS NULL THEN
        RAISE EXCEPTION 'Service roles are missing or privileged';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member IN (runtime_oid, migration_oid))
       OR EXISTS (SELECT 1 FROM pg_database WHERE datdba = runtime_oid)
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = runtime_oid)
       OR EXISTS (SELECT 1 FROM pg_class WHERE relowner = runtime_oid)
       OR EXISTS (SELECT 1 FROM pg_proc WHERE proowner = runtime_oid)
       OR EXISTS (SELECT 1 FROM pg_type WHERE typowner = runtime_oid) THEN
        RAISE EXCEPTION 'Runtime role must not inherit roles or own database objects';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_database
                   WHERE datname = current_database() AND datdba = migration_oid) THEN
        RAISE EXCEPTION 'Database must belong to its migration role';
    END IF;
    allowed_schemas := CASE WHEN service_name = 'core' THEN ARRAY['auth', 'public']
                           ELSE ARRAY['financial'] END;
    IF EXISTS (SELECT 1 FROM pg_namespace n
               WHERE n.nspname NOT IN ('public', 'pg_catalog', 'information_schema')
                 AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
                 AND NOT n.nspname = ANY(allowed_schemas)
                 AND has_schema_privilege(runtime_oid, n.oid, 'CREATE')) THEN
        RAISE EXCEPTION 'Runtime role has cross-owner schema creation privileges';
    END IF;
    IF to_regclass('public.flyway_schema_history') IS NULL THEN
        RAISE EXCEPTION 'Approved migrations must complete before runtime grants';
    END IF;
    IF has_table_privilege(runtime_oid, 'public.flyway_schema_history', 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
       OR has_any_column_privilege(runtime_oid, 'public.flyway_schema_history', 'INSERT,UPDATE,REFERENCES') THEN
        RAISE EXCEPTION 'Runtime role has pre-existing migration history write privileges';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                 AND has_function_privilege(runtime_oid, p.oid, 'EXECUTE')) THEN
        RAISE EXCEPTION 'Runtime access to security-definer routines requires a separate reviewed policy';
    END IF;
    -- Every granted object must be owned by the approved migration role.
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE (n.nspname = ANY(allowed_schemas)
                      OR (n.nspname = 'public' AND c.relname = 'flyway_schema_history'))
                 AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
                 AND c.relowner <> migration_oid) THEN
        RAISE EXCEPTION 'Service object ownership does not match its migration role';
    END IF;
    -- Reject pre-existing foreign grants rather than revoke another owner's
    -- permissions or mistake a restored shared database for a fresh target.
    FOR object_record IN
        SELECT c.oid, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
           AND NOT n.nspname = ANY(allowed_schemas)
           AND NOT (n.nspname = 'public' AND c.relname = 'flyway_schema_history')
           AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
    LOOP
        IF (object_record.relkind = 'S' AND has_sequence_privilege(runtime_oid, object_record.oid, 'USAGE,SELECT,UPDATE'))
           OR (object_record.relkind <> 'S' AND
               (has_table_privilege(runtime_oid, object_record.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege(runtime_oid, object_record.oid, 'SELECT,INSERT,UPDATE,REFERENCES'))) THEN
            RAISE EXCEPTION 'Runtime role has cross-owner object privileges';
        END IF;
    END LOOP;
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM %I', current_database(), runtime_name);
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), runtime_name);
    EXECUTE 'REVOKE CREATE ON SCHEMA public FROM PUBLIC';
    EXECUTE format('REVOKE ALL ON SCHEMA public FROM %I', runtime_name);
    FOREACH schema_name IN ARRAY allowed_schemas LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = schema_name) THEN
            RAISE EXCEPTION 'Required service schema is absent';
        END IF;
        IF schema_name <> 'public' AND NOT EXISTS
           (SELECT 1 FROM pg_namespace WHERE nspname = schema_name AND nspowner = migration_oid) THEN
            RAISE EXCEPTION 'Service schema does not belong to its migration role';
        END IF;
        EXECUTE format('REVOKE ALL ON SCHEMA %I FROM %I', schema_name, runtime_name);
        EXECUTE format('REVOKE CREATE ON SCHEMA %I FROM PUBLIC', schema_name);
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', schema_name, runtime_name);
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I', schema_name, runtime_name);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO %I', schema_name, runtime_name);
        EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM %I', schema_name, runtime_name);
        EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO %I', schema_name, runtime_name);
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I', migration_name, schema_name, runtime_name);
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO %I', migration_name, schema_name, runtime_name);
    END LOOP;
    EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', runtime_name);
    EXECUTE 'REVOKE ALL ON TABLE public.flyway_schema_history FROM PUBLIC';
    EXECUTE format('REVOKE ALL ON TABLE public.flyway_schema_history FROM %I', runtime_name);
    EXECUTE format('GRANT SELECT ON TABLE public.flyway_schema_history TO %I', runtime_name);
END
$cell$;
COMMIT;
