# Initial isolated service database creation

`infra/runtime/postgres/create-service-databases.sql` creates two fresh databases
for four externally provisioned service roles. Passwords, role creation and
credential distribution stay with the external secret manager. The primitive
does not adopt an existing database, rotate credentials, migrate data or start
services. It is not yet integrated into the signed Cell lifecycle executor.

Use an explicitly approved PostgreSQL administration connection bound to the
`postgres` maintenance database. Names here are illustrative, not credentials:

```
psql -X --no-password -d postgres \
  -v core_database=kerosene_core -v kfe_database=kerosene_kfe \
  -v core_migration_role=kerosene_core_migration \
  -v core_runtime_role=kerosene_core_runtime \
  -v kfe_migration_role=kerosene_kfe_migration \
  -v kfe_runtime_role=kerosene_kfe_runtime \
  -f infra/runtime/postgres/create-service-databases.sql
```

Bind endpoint/trust/admin credentials using a protected libpq configuration,
never password-bearing argv or URLs. The installer still needs to bind actual
server identity and approved configuration; a database name is not proof of that.

## Contract and failure handling

- Both database names must be absent before the first create. Four distinct roles
  must already exist, permit login and have no superuser, database/role creation,
  replication or RLS bypass. Runtime roles must be NOINHERIT. Existing role
  memberships or ownership dependencies anywhere in the cluster reject this
  fresh-role contract. This is not an existing-role adoption tool.
- Names are bounded lowercase SQL identifiers; reserved maintenance/template
  names, duplicates and invalid names are rejected. psql values are escaped as
  strings, then identifiers are server-quoted with `format('%I',...)`.
- A session advisory lock serializes cooperating provisioners. It does not lock
  out other privileged administrators or replace Bank release authorization.
  Prechecks cover both targets before either is created; normal PostgreSQL
  duplicate-name enforcement also remains active.
- Each database uses `template0`, its own migration owner and
  `ALLOW_CONNECTIONS=false` at creation. PUBLIC privileges are revoked before
  connections are enabled. The runtime role cannot connect until the later
  [runtime permission step](cell-database-runtime-privileges.md) succeeds.
- `CREATE DATABASE` cannot be part of an atomic transaction. A failure after a
  successful create retains that target, potentially with connections disabled.
  Stop, inventory actual targets/ACLs and recover explicitly; do not blindly retry,
  drop targets, change ownership or re-enable a pre-existing database. Re-running
  this primitive refuses any existing target. No password or role is altered.

After creation, run each approved migration JAR with its own migration credentials,
validate history/schema and apply owner-scoped runtime grants. Only then distribute
runtime connection credentials and admit application startup. Existing shared
databases require separately tested history/data transition and coordinated
recovery, not this initial-creation path. No signer activation is performed.

## Executed evidence

The opt-in `service-database-grants-postgres-test.py` now uses this actual primitive
to provision its unique loopback lab databases before running the real Core/KFE
JARs. It checks pre-grant runtime connection denial, existing-target rejection,
second-target collision without first-target creation, history preservation,
concurrent creation with one winner, invalid/injected names and role aliases,
plus the existing restricted-role checks. All 12 actual PostgreSQL cases passed
locally with the JAR inputs enabled; this does not claim hosted CI execution.
All lab databases/roles are retained for
inspection. These tests do not qualify production TLS, a complete service startup,
credential issuance, interrupted-create recovery or the full Cell installer.
