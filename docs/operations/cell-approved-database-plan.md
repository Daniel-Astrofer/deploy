# Approved initial database configuration

The Cell controller now validates an optional reserved ConfigMap in the approved
JSON deployment artifact: `kerosene-cell-database-plan`, namespace
`kerosene-staging`, containing exactly one data key, `plan.json`. Its value is a
JSON string with schema `kerosene.cell.initial-databases/v1` and mode `initial`.
See `infra/stack/examples/initial-database-plan.example.json` for synthetic names
and current installed-script hashes. This is inert configuration, not SQL or an
executable hook. Never put credential values in the plan.

Include that ConfigMap in the deployment's `resources` before computing approved
configuration digests. Existing component digest calculation includes all
non-runtime resources, so changing any plan byte changes every component's
`configDigest`. The package producer and controller both validate it. Do not
replace a signed artifact's plan after publication or trust an unsigned candidate
as release authorization.

## Verified contract

- Two distinct bounded SQL database names and four distinct migration/runtime
  roles; reserved names, invalid identifiers and aliases are rejected.
- Five distinct external Secret names: PostgreSQL bootstrap, Core migration,
  Core runtime, KFE migration and KFE runtime. Only metadata/key references are
  permitted. Credential key aliases, raw passwords and unknown fields fail.
- Explicit main-container workload identities in the Bank namespace. With
  release validation, their images must match the corresponding approved
  PostgreSQL/Core/KFE image. Actual runtime datasource variables and PostgreSQL
  bootstrap variables must reference the exact nonoptional plan Secret keys.
- Exact hashes of the two **installed** SQL primitives. Script bytes are read
  only from fixed controller paths, never downloaded or executed from the plan.
  Candidate provenance records both SQL asset hashes and checks that they remain
  unchanged during configuration validation, alongside controller module hashes.
- JSON has bounded size, no duplicate fields, non-finite values or arbitrary
  executable fields. Invalid plan errors do not echo credential values.

External-Secret preflight additionally inventories migration credentials even
though they are not mounted in application workloads. It projects only names and
key names, not values. Different Secret names do not prove different principals,
database URLs, role attributes, TLS trust or actual server identity; those checks
remain mandatory execution requirements.

## Current execution boundary

Legacy manifests without this reserved ConfigMap remain inspectable. A real
initial installation requires the approved plan before Admin installation or
resource writes, even if other execution capability checks are mocked in tests.
Dry-run remains inspection, not installation proof.

No SQL runs as a result of validation or preflight. The lifecycle executor still
lacks qualified initial admission, credential issuance, migration scheduling,
tested recovery and whole-Cell acceptance. All existing live execution blockers
remain in place. This initial plan is not an update/recover authorization or a
shortcut for adopting existing databases.

The pending executor must bind actual database identities, provision external
roles/credentials, create fresh private databases, run approved migration-only
images, validate schema/history, apply restricted runtime grants and record
durable evidence **before** admitting Core/KFE startup. It must retain partial
targets and require explicit recovery rather than drop/adopt/re-enable them.
Existing installations need a separately tested history/data transition. Neither
plan validation nor a valid script digest activates signers or proves recovery.
