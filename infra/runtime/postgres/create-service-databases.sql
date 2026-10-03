-- Explicit fresh-install primitive, not a legacy transition or release gate.
-- Roles/passwords must already be provisioned by the external secret manager.
\set ON_ERROR_STOP on
SET lock_timeout = '5s';
SET statement_timeout = '30s';
SELECT set_config('kerosene.cell.core_database', :'core_database', false),
       set_config('kerosene.cell.kfe_database', :'kfe_database', false),
       set_config('kerosene.cell.core_migration_role', :'core_migration_role', false),
       set_config('kerosene.cell.core_runtime_role', :'core_runtime_role', false),
       set_config('kerosene.cell.kfe_migration_role', :'kfe_migration_role', false),
       set_config('kerosene.cell.kfe_runtime_role', :'kfe_runtime_role', false);
-- One maintenance session serializes cooperating initial provisioners. This
-- is not a lock against other privileged administrators or a release quorum.
SELECT pg_advisory_lock(hashtextextended('kerosene.cell.create-service-databases/v1', 0));
DO $cell$
DECLARE
    database_names text[] := ARRAY[current_setting('kerosene.cell.core_database'), current_setting('kerosene.cell.kfe_database')];
    role_names text[] := ARRAY[current_setting('kerosene.cell.core_migration_role'), current_setting('kerosene.cell.core_runtime_role'),
                              current_setting('kerosene.cell.kfe_migration_role'), current_setting('kerosene.cell.kfe_runtime_role')];
    runtime_names text[] := ARRAY[current_setting('kerosene.cell.core_runtime_role'), current_setting('kerosene.cell.kfe_runtime_role')];
    object_name text;
BEGIN
    IF current_database() <> 'postgres' THEN
        RAISE EXCEPTION 'Initial provisioning requires the explicit postgres maintenance database';
    END IF;
    FOREACH object_name IN ARRAY database_names || role_names LOOP
        IF object_name !~ '^[a-z][a-z0-9_]{0,62}$' OR object_name IN ('postgres', 'template0', 'template1') THEN
            RAISE EXCEPTION 'Invalid fresh service database or role name';
        END IF;
    END LOOP;
    IF (SELECT count(DISTINCT name) FROM unnest(database_names) AS names(name)) <> 2
       OR (SELECT count(DISTINCT name) FROM unnest(role_names) AS names(name)) <> 4 THEN
        RAISE EXCEPTION 'Fresh service databases and all four roles must be distinct';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_database WHERE datname = ANY(database_names)) THEN
        RAISE EXCEPTION 'Initial provisioning refuses an existing service database; use explicit recovery';
    END IF;
    IF (SELECT count(*) FROM pg_roles WHERE rolname = ANY(role_names) AND rolcanlogin
         AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls) <> 4
       OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ANY(runtime_names) AND rolinherit) THEN
        RAISE EXCEPTION 'All four externally provisioned roles must satisfy the service privilege policy';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = ANY(role_names))
       OR EXISTS (SELECT 1 FROM pg_shdepend d JOIN pg_roles r ON r.oid = d.refobjid
                   WHERE d.refclassid = 'pg_authid'::regclass AND d.deptype = 'o' AND r.rolname = ANY(role_names)) THEN
        RAISE EXCEPTION 'Fresh service roles must not inherit roles or own existing objects';
    END IF;
END
$cell$;

-- CREATE DATABASE cannot run in a transaction. Each target is born with
-- connections disabled, then gets a private ACL before connections are enabled.
-- On failure, retain any partial target (possibly disabled) for explicit recovery.
-- Never drop, adopt, reassign owners, reset passwords or re-enable old targets.
SELECT format('CREATE DATABASE %I OWNER %I TEMPLATE template0 ALLOW_CONNECTIONS false',
              current_setting('kerosene.cell.core_database'), current_setting('kerosene.cell.core_migration_role'))
\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_setting('kerosene.cell.core_database'))
\gexec
SELECT format('ALTER DATABASE %I ALLOW_CONNECTIONS true', current_setting('kerosene.cell.core_database'))
\gexec
SELECT format('CREATE DATABASE %I OWNER %I TEMPLATE template0 ALLOW_CONNECTIONS false',
              current_setting('kerosene.cell.kfe_database'), current_setting('kerosene.cell.kfe_migration_role'))
\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_setting('kerosene.cell.kfe_database'))
\gexec
SELECT format('ALTER DATABASE %I ALLOW_CONNECTIONS true', current_setting('kerosene.cell.kfe_database'))
\gexec
SELECT pg_advisory_unlock(hashtextextended('kerosene.cell.create-service-databases/v1', 0));
