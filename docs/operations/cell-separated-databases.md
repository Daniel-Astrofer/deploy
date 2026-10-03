# Separate Core and KFE database wiring

`infra/kubernetes/overlays/staging-separated-databases` renders the existing
complete Bank-plane staging runtime with an opt-in database wiring component.
It changes Core/KFE datasource Secret references and separates PostgreSQL's
privileged bootstrap credentials from both application identities. The independent Vault
plane is unchanged and remains required for the complete Cell.

| Workload | Externally provisioned Secret | Required keys |
| --- | --- | --- |
| Core (`server`) | `kerosene-core-db-secrets` | `jdbc-url`, `application-user`, `application-password` |
| KFE (`kfe-service`) | `kerosene-kfe-db-secrets` | `jdbc-url`, `application-user`, `application-password` |
| PostgreSQL bootstrap | `kerosene-postgres-bootstrap` | `bootstrap-user`, `bootstrap-password` |

The Secrets belong in `kerosene-staging`; no credential values are generated or
committed. Endpoints must identify different databases, possibly on one PostgreSQL
cluster. Different Secret names alone do not prove different databases or roles.
The official PostgreSQL image creates `POSTGRES_USER` as a privileged bootstrap
identity on an empty data directory; never use that identity for Core or KFE.
The new overlay leaves the existing data path and readiness probes unchanged.
Changing these environment references does not rotate passwords or create roles
in an initialized database; this is not an existing-volume transition mechanism.
The installer must verify actual database identity and privilege separation,
not decode credentials into logs or accept naming as evidence.

This is a renderable input, **not a qualified installer or migration cutover**:

```
kubectl kustomize infra/kubernetes/overlays/staging-separated-databases
```

Do not apply this overlay to an existing shared database deployment as a shortcut.
It does not create databases, copy data, adopt history, grant privileges, execute
migrations or prove recovery. Base/staging/local defaults remain unchanged for
backwards compatibility. Release packaging must bind the rendered configuration
and immutable images to the approved release before execution. Existing live
execution blockers remain in force.

## New installation requirements

Provision empty Core and KFE databases with distinct migration owners and runtime
roles. Migration owners may execute approved DDL; runtime roles must not be
superusers or own the other service's objects. Revoke cross-database access and
grant only explicit owner-scoped runtime privileges. Execute each approved JAR's
`--cell-migration=migrate` mode with its own migration credentials; then validate
its history and JPA schema before services start with runtime credentials.

The explicit [initial database creation primitive](cell-database-initial-provisioning.md)
now creates private targets from pre-provisioned roles and refuses existing names.
It is verified in the disposable PostgreSQL test but is not called automatically
by this overlay or lifecycle executor; external role/credential issuance remains.

Core requires its forward V14 schema migration; its older historical chain lacks
content tables and notification fields. Do not satisfy missing schema by running
KFE migrations against the Core database. Runtime Flyway settings are preserved
by this component, not silently disabled; migration/runtime-role compatibility
must be tested before qualification. Historical compatibility objects still need
explicit ownership cleanup. Database provisioning and these gates are not yet
automated by this overlay.

The atomic [runtime permission step](cell-database-runtime-privileges.md) now has
real PostgreSQL/local-JAR tests for both services. It grants data access separately
from migration ownership and verifies up-to-date Flyway works without DDL rights;
it still requires explicit administration, external role provisioning and an
approved database binding, and is not yet integrated into lifecycle execution.

## Existing installation and recovery

Quiesce producers and preserve a verified shared-source backup and full migration
history. Restore into isolated transition targets without altering the source.
Adopt only artifact/checksum-verified Core historical entries into a dedicated
Core history; retain and validate KFE's full history in its target. Preserve
identity IDs, activation state, sequences, data and relevant constraints. Reject
untrusted or missing history rather than repair, baseline or ignore it.

Rehearse schema/data partition and both services' remote contracts against the
isolated targets; perform coordinated cutover only with tested recovery evidence.
Before new writes, recovery can use the preserved source and prior approved
configuration. After new writes, switching endpoints back loses divergent state;
use a separately tested coordinated restore/replay procedure instead. There is
no automatic rollback or automatic signer activation in this component.
