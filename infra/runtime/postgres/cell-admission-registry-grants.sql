-- Dedicated registry only; externally provisioned service LOGIN role.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SELECT set_config('kerosene.registry.database', :'registry_database', true),
       set_config('kerosene.registry.role', :'service_role', true);
DO $registry$
DECLARE target_role text := current_setting('kerosene.registry.role');
BEGIN
    IF current_database() <> current_setting('kerosene.registry.database')
       OR target_role !~ '^[a-z][a-z0-9_]{0,62}$'
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=target_role AND rolcanlogin
          AND NOT rolinherit AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
          AND NOT rolreplication AND NOT rolbypassrls)
       OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=target_role)
       OR EXISTS (SELECT 1 FROM pg_shdepend d JOIN pg_roles r ON r.oid=d.refobjid
          WHERE d.refclassid='pg_authid'::regclass AND d.deptype='o' AND r.rolname=target_role) THEN
        RAISE EXCEPTION 'Registry service role or database binding is unsafe';
    END IF;
    IF has_table_privilege(target_role, 'cell_admission.consumed', 'UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
       OR has_column_privilege(target_role, 'cell_admission.consumed', 'consumed_at', 'INSERT,UPDATE')
       OR has_any_column_privilege(target_role, 'cell_admission.consumed', 'UPDATE,REFERENCES')
       OR has_schema_privilege(target_role, 'cell_admission', 'CREATE') THEN
        RAISE EXCEPTION 'Registry service role has forbidden existing privileges';
    END IF;
END
$registry$;
REVOKE ALL ON SCHEMA cell_admission FROM PUBLIC;
REVOKE ALL ON TABLE cell_admission.consumed FROM PUBLIC;
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'service_role') \gexec
SELECT format('GRANT USAGE ON SCHEMA cell_admission TO %I', :'service_role') \gexec
-- Fresh role only. No owner privileges, DDL, mutation or clock override.
SELECT format('GRANT SELECT ON cell_admission.consumed TO %I', :'service_role') \gexec
SELECT format('GRANT INSERT (network_id,epoch,nonce,cell_id,cluster_uid,operator_id,change_id,approval_digest,admission_digest,issued_at_unix_seconds,expires_at_unix_seconds) ON cell_admission.consumed TO %I', :'service_role') \gexec
COMMIT;
