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

## Migration Job preparation

`initial_database_migration_jobs` now builds inert Core/KFE Job specifications
from the validated plan and approved image identities. It does not submit them.
The fixed Java command runs `/app/app.jar` with exactly one migration argument;
no shell, arbitrary hook, image startup script or workload command is reused.
Only three datasource variables from the service's migration Secret are supplied.
Runtime/custody configuration, ports and runtime Service/PDB labels are not copied.

The `capabilities` preparation mode instead supplies an empty environment list,
no Secret references or Secret volumes, and a 30-second deadline. Its fixed
argument is `--cell-migration=capabilities`; its Job identity differs from
`migrate` and `validate` and remains within Kubernetes' 63-character limit.
This does not erase image-baked environment values or isolate network access.
The future executor must first establish network isolation and independently
qualify the image; self-reported JSON is not release authorization, an
attestation, or proof that another execution path is free of side effects.

`initial_database_capability_resources` prepares, in order, a dedicated
update/plan-bound namespace with restricted Pod Security admission, a namespace-
wide ingress/egress deny policy, and the two credential-free probe Jobs in that
namespace. No resources are submitted. This avoids inheriting staging's global
DNS allow policy: Kubernetes NetworkPolicy allows are additive, so another deny
policy in staging would not remove DNS access. Before execution, refuse an
existing probe namespace (including residual Jobs or permissive policies),
verify live CNI enforcement and apply/verify isolation before creating pods.
Namespace names and a deny manifest alone cannot establish that protection.
`verify_database_capabilities_output` accepts at most 4096 bytes of UTF-8 stdout
containing one exact capabilities object for the expected Core/KFE component.
Duplicate/extra fields, startup logs, concatenated documents, unsupported
operations, invalid encoding and non-finite JSON are rejected without echoing
stdout. The collector must bound subprocess/log collection before calling it;
this parser does not bound an already allocated input. It validates syntax and
contract only, not pod UID, image identity, exit status or network isolation.
Retain failed probe evidence; do not automatically delete namespaces or adopt
leftover workloads. Explicit recovery must verify ownership and actual resources.

Jobs are nonroot, read-only apart from bounded `/tmp`, without service-account
tokens, extra capabilities, init containers or automatic retries. The
PodSpec explicitly disables service-link environment injection and host network,
PID and IPC namespaces; credentials/endpoints are not inferred from Services.
These fields must remain unchanged by admission before execution.
They have a
five-minute deadline for `migrate`/`validate` and no automatic TTL cleanup. Names bind update identity,
plan digest, component and operation; complete identities remain in annotations.
Existing Jobs must still be matched by actual UID/spec and journal ownership
before the future executor could resume them. Names alone are not completion proof.

Generated Jobs do not enter the deployment resource allowlist: callers still
cannot supply arbitrary archive Jobs. Before execution, the controller must
verify actual database target/role identity, provide independently approved TLS
trust and registry access, and qualify a migration-specific NetworkPolicy. It
must await and verify each migration result before admitting application startup,
with explicit recovery on timeout/failure. These mechanisms and live OCI/cluster
qualification remain incomplete; execution capability blockers stay unchanged.
In particular, a signed old image may ignore `--cell-migration` and start Spring
normally. Never run these specifications with migration credentials merely
because the image digest is approved. An independently verified build and isolated
OCI test must prove that the exact image implements the migration-only contract
without HTTP/workers or financial startup effects before Job execution is allowed.
