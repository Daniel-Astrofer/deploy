# Cell database migration and runtime roles

`infra/runtime/postgres/service-runtime-grants.sql` is an atomic psql permissions
step for a freshly provisioned, isolated Core or KFE database after approved
migrations. It does not create roles, passwords or databases, adopt an existing
installation, execute migrations or authorize a release. It is not yet invoked
by the Cell lifecycle executor; migration/recovery execution blockers remain.
Initial targets may be created using the separately reviewed
[database creation primitive](cell-database-initial-provisioning.md); that step
also leaves role/password provisioning external.

Run from a separately provisioned privileged administration connection using
protected libpq credentials, never a password-bearing URL/argv:

```
psql -X --no-password -v ON_ERROR_STOP=1 \
  -v service=core -v expected_database=kerosene_core \
  -v migration_role=kerosene_core_migration -v runtime_role=kerosene_core_runtime \
  -f infra/runtime/postgres/service-runtime-grants.sql
```

The libpq connection must explicitly bind the intended database and endpoint.
Repeat separately with `service=kfe` and its independently provisioned names.
Names above are examples, not credentials. A qualified installer must independently
verify actual database identities and approved artifacts before this step.

## Enforced permissions

- Database owner is its distinct migration role. Both roles must exist, permit
  login and lack superuser, database/role creation, replication and RLS bypass.
  Runtime is NOINHERIT; neither role may have inherited memberships. Runtime
  must not own database/schema/table/function/type objects.
- Existing foreign-schema data permissions and accessible application
  security-definer routines block this generic policy instead of being silently
  accepted or repaired. Granted tables/sequences must belong to the migration
  role. Presence of a history table alone is not migration verification: the
  approved JAR must validate its complete history first.
- PUBLIC database connection/create/temp privileges are revoked. Runtime gets
  CONNECT, not database CREATE or TEMP. Schema CREATE is revoked from PUBLIC;
  runtime gets USAGE and owner-scoped data DML, not TRUNCATE or trigger privileges.
  Core's data scope is `auth`/`public`; KFE's is `financial`. Sequence permissions
  are USAGE/SELECT, not UPDATE. Default grants apply only to future objects made
  by the same migration role in the same scope.
- `public.flyway_schema_history` is a special read-only exception, not application
  data DML. Neither PUBLIC nor runtime receives history write permission.
  Existing financial append-only triggers and constraints are unchanged.

The current historical chains still create unused compatibility objects. These
are not granted to the other service's runtime role. Future object/role changes
require reviewed migrations and re-verification; this policy is not a complete
cluster-wide permission audit or removal of historical compatibility data.

## Verification and recovery boundary

`infra/tests/service-database-grants-postgres-test.py` is an opt-in loopback-only
test. It creates unique synthetic databases/roles, migrates using both real local
JARs and migration-role credentials, applies grants and checks runtime validation,
up-to-date Flyway execution, own-data writes, default grants and denial of DDL,
history mutation, foreign data and cross-service connection. Invalid bindings,
privileged roles and an incomplete target must fail; ACL changes roll back.
Synthetic targets are retained, not automatically dropped.

Set `CELL_DATABASE_DISPOSABLE=true`, explicit `CELL_DATABASE_PGHOST=127.0.0.1`,
`CELL_DATABASE_PGPORT`, external admin user/password and absolute regular local
JAR paths through `CELL_DATABASE_CORE_JAR` and `CELL_DATABASE_KFE_JAR`. Test admin
credentials are not inherited by child JAR processes. This is actual PostgreSQL
verification, not a complete service-startup, restore or live Cell qualification.

All script changes run in one transaction; failure rolls back permissions. A
successful commit deliberately persists restrictions and has no automatic broad
regrant fallback. Restore the target's verified pre-change ACL evidence only
through explicit recovery. Do not run on a populated shared-source database.
Keep that source untouched while transition targets and coordinated data/ACL
restore are rehearsed. No signer is activated by this permissions step.
