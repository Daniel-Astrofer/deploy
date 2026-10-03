# Cell lifecycle: implemented interfaces and current safety boundary

The Cell comprises Admin/jctl, Core, KFE, Node, Vault, web-page, PostgreSQL,
Redis, Bitcoin, LND and Tor. A complete release records all eleven immutable
component images/configurations and ten source repositories. This controller
does not yet provide a qualified one-command live installer for the full Cell.
Do not describe a successful plan, dry-run, readiness probe or archive import
as a successful live installation.

The opt-in [separate database wiring](cell-separated-databases.md) renders Core
and KFE with independent external datasource Secret references. It preserves
existing defaults and supplies no credentials, database provisioning or legacy
cutover. Secret-reference preflight covers both new bindings; this is not proof
that their contents identify distinct databases or correctly scoped roles.
The new-install overlay also separates PostgreSQL bootstrap credentials from
application credentials. The [runtime privilege step](cell-database-runtime-privileges.md)
has actual PostgreSQL/JAR verification but still requires external provisioning
and approved administration; it is not invoked by the lifecycle executor yet.
The [approved initial database plan](cell-approved-database-plan.md) now binds
database/role/Secret/workload metadata and installed SQL hashes to component
configuration digests. Preflight includes its migration credential references;
actual installation requires a plan, but no SQL is executed by its validation.

## Bootstrap and operator workflow

Provision public TUF root, roster, snapshot-provider public key and real
consensus anchor out of band. Never trust anchors embedded in a downloaded
candidate. Independently install the governance verifier and approve its
binary digest. Private keys, sessions and Kubernetes credentials stay outside
the release archive and update journal.

```sh
infra/kerosene-stack init --cell-dir /protected/cell-a --cell-id cell-a \
  --tuf-trusted-root /protected/root.json \
  --validator-roster /protected/roster.json \
  --snapshot-provider-key /protected/snapshot-provider.pub \
  --consensus-anchor /protected/consensus-anchor.json \
  --consensus-verifier /protected/kerosene-release-consensus \
  --kubeconfig /protected/cell-a.kubeconfig --kube-context staging-cell-a
infra/kerosene-stack preflight --cell-dir /protected/cell-a
infra/kerosene-stack preflight --cell-dir /protected/cell-a \
  --release /protected/package/release-lock.json \
  --deployment-manifest /protected/package/deployment.json
infra/kerosene-stack status --cell-dir /protected/cell-a
```

Initialization refuses an existing directory and starts no service. Public
trust files and configuration use private local permissions. Preflight reports
infrastructure reachability separately from `financialReadinessVerified` and
`applyQualified`. An omitted Kubernetes binding prevents approved-manifest
execution. The saved explicit context is bound to the `kube-system` namespace
UID; commands do not use the currently selected kubectl context. A replaced
cluster requires independent bootstrap, not a journal reset.

With the paired release/manifest inputs, preflight additionally validates exact
approved configuration digests and inventories mandatory external Secrets in
the bound cluster. It covers environment keys, envFrom, init containers,
Secret/projected volumes and imagePullSecrets, merging repeated required keys
per namespace/name. Optional references do not waive another required reference.
Only Secret name and key names are projected by kubectl; values are not emitted,
decoded or stored. The same check runs before Admin installation or Kubernetes
resource writes. Missing credentials/RBAC/API access or malformed inventory
fails closed; the controller never creates credentials as a fallback.

`externalSecretReferencesVerified` is false unless this manifest-specific check
ran successfully. It is not authentication, certificate validity, macaroon
scope or financial readiness evidence. Credentials must still be independently
provisioned with appropriate policy and contents. Another administrator can
rotate/delete them after preflight; normal workload readiness and the remaining
acceptance gates still apply. Initial Secret provisioning must happen after
namespace preparation outside this release executor; these checks do not adopt
an existing workload or bypass the first-install recovery rules.

`import-artifact` only integrity-checks and caches/extracts inert bytes. See
[release archive](release-archive.md) for offline import, size limits, mirror
policy and packaging. The unsigned candidate workflow does not publish an
authorized release or executable installation script.

`update`, `install` and `recover` share the evidence pipeline. `update` without
`--apply` produces a plan. `--cell-dir` supplies trust/state references and
`--bundle-dir` supplies inert evidence file locations; missing evidence never
becomes a bypass. Use `--help` for the exact required flags. Legacy v1/v2
signature receipts remain usable for inspection/dry-run, not real application.

First installation additionally rechecks absence of any deployment journal
under the canonical update lock. Manifest execution probes both bound Cell
namespaces: any existing Deployment, StatefulSet, Pod or PVC blocks initial
installation and requires explicit recovery. Missing namespaces are allowed;
invalid/failed inventory reads are not proof of absence. This protects persisted
identities/data from adoption after a lost journal, and prevents concurrent
first-install commands from both relying on an earlier empty-journal check.
No resources or volumes are deleted by these checks. The inventory is not a
distributed lock against other cluster administrators; admission and exclusive
operational control still need full-Cell qualification.

The first-install admission/recovery contract remains incomplete: the shared
pipeline still requires live KFE maintenance and snapshot/recovery evidence
designed for updates. These requirements are not waived merely because the
cluster inventory is empty. Initial installation must obtain a separately
qualified no-existing-financial-state bootstrap path before live apply is usable.

## Approved manifest and evidence

The new `--deployment-manifest` is JSON with schema
`kerosene.stack.deployment/v1`, environment `staging-cell`, Kubernetes resources,
and the pinned Admin image plus nonsecret Admin configuration. Its canonical
per-component configuration digests must match the release lock. Shared
resources are included in every component digest. All runtime/init images must
be approved immutable service images; namespaces are restricted to the two
staging Cell namespaces. Secrets, arbitrary jobs, RBAC, host namespaces,
hostPath, privileged containers and unqualified HPA policies are rejected.
Unsupported policies are checked before any Kubernetes write.

The executor now validates a complete runtime inventory and uses explicit
startup phases: PostgreSQL/Redis/Tor/Node, Bitcoin, LND, Vault, Core/KFE,
then web-page. Each phase is submitted before its workloads are awaited;
in particular Core/KFE reciprocal integration references must not serialize
their initial submission. Every preceding phase must become Kubernetes-ready
before the next is submitted. This is an ordering policy, not proof of RPC,
financial readiness or consensus health. Multiple Node/Vault workloads remain
in the inventory; this does not implement their replica-by-replica update.
Unidentified/ambiguously identified images, missing runtime components and
cross-phase colocated containers (including init containers) are unsupported
and block execution before resource writes, rather than silently choosing an
unsafe order. Such topologies need a separately implemented dependency policy.

The Admin image currently refers to the `kerosene-jctl` operator CLI, whose
container entrypoint exits after a command. It is not a permanent Deployment;
the planner rejects using it as one. The web client is a separate component.
Node/Tor colocation is supported in the first phase, matching both canonical
plane manifests and their shared onion identity volumes. No fallback to a
source build, mutable image, or automatic signer activation is introduced.

These phases are not a qualified live install or update. On interruption,
previously submitted resources may remain; the executor does not delete PVCs,
reset identities or automatically roll back data. Existing journal recovery
and capability blockers still apply. Kubernetes readiness tests are mocked
in the orchestration suite and cannot substitute for a complete Cell run.

Authorization requires full offline TUF metadata, ordered v3 consensus proof,
fresh independently signed Bank aggregate, independently approved snapshot
request and signed tested restore receipt. TUF state preserves delegated-role
high-water marks and rejects same-version signed-metadata equivocation.

Validator rosters require canonical Ed25519 SPKI keys and distinct key bytes
for each member ID. The signature counter also rejects duplicate trusted keys,
so aliases of one private key cannot satisfy a multi-member threshold. Distinct
keys are necessary, but do not prove independent operators or failure domains.
Existing duplicate-key rosters must be corrected through independent authority
provisioning; the installer never repairs or replaces trusted keys automatically.

Live application additionally requires an authenticated ADMIN KFE maintenance
status over pinned mTLS references, exact change/operator attribution and
quorum-approved tested recovery evidence bound to the migration digest. KFE
maintenance is rechecked before each runtime workload. The current KFE
implementation conservatively reports incomplete financial mutation/callback
coverage; no operator flag can turn that uncertainty into safe drain.

Interrupted updates require explicit same-target `--resume-update-id` and the
original change ID, followed by fresh validation. Locks are not automatically
deleted. Phase journals and success/failure checkpoints are fsynced into local
history. This is local operational evidence, not an external signed audit log.
Status explicitly identifies its source as `local-journal-not-live-attestation`.

## Installed operator artifact

The integrated executor extracts `/opt/kerosene-jctl` from an already cached,
approved OCI digest using a created-but-never-started container. It pins creation
to the inspected local image ID, disables networking, bounds transfer/extraction,
rejects links/devices/path traversal, verifies file hashes and atomically installs
an owner-only distribution under `CELL/admin/installations/RELEASE_DIGEST`.
It requires a provisioned Java 21+ runtime; it does not pull or build a fallback.
Temporary containers are removed by exact generated name without force. Cleanup
failure fails the update and requires investigation; it never prunes other data.

Approved nonsecret Admin config requires `apiBaseUrl` and permits optional
`kfeBaseUrl`, both credential-free HTTPS origins. Unknown fields are rejected.
Config is installed with the receipt, not silently ignored. Private TLS stores
and short-lived tokens are provided outside the package using existing jctl
operator mechanisms; the launcher does not persist them or auto-authenticate.

After a successfully committed installation, the stable controller interface is:

```sh
infra/kerosene-stack admin --cell-dir /protected/cell-a -- cell status
infra/kerosene-stack admin --cell-dir /protected/cell-a --target kfe -- kfe maintenance status
```

The wrapper selects the approved endpoint, verifies every installed file and
receipt, and holds the canonical Cell update lock throughout CLI execution.
Endpoint/profile overrides and argument-file expansion are forbidden.
Inherited local-mode settings cannot downgrade authentication: installed CLI
execution forces its production authentication policy, including dedicated
tokens and protected mTLS stores. Help/version do not authenticate or contact APIs.
Failed, incomplete, dry-run-only or foreign journals do not activate the new Admin.
An alternate state directory cannot bypass a Cell's canonical journal/lock.
Older installations are retained, but are not selected automatically after a
failed update. Recovery revalidates the original target; no data rollback or
signer activation is implied by restoring an operator executable.

Local tests exercise the actual built pinned jctl distribution for both Core
and KFE help, plus mocked OCI extraction/identity checks and failure journaling.
Actual Docker OCI extraction and complete Cell installation are still not
qualified; `admin-artifact-installation-not-qualified` remains a live-apply gate.

## Remaining live execution qualification

Real `--apply` is explicitly blocked before mutation until all of these
implementation capabilities exist and their integration tests pass:

1. Independent Vault compatibility/rebuild gate integrated into apply.
2. Migration execution and actual tested recovery integrated into the phases.
3. Node/Vault replica-by-replica rollout preserving real quorum and identities.
4. Live OCI qualification of the integrated Admin artifact installation.
5. Complete-Cell acceptance checks covering actual protocol/readiness, not just
   the legacy single-Vault health and Core login scripts.

The installed legacy smoke subprocesses now receive the bootstrap's explicit
kubectl executable, kubeconfig and context, after rechecking cluster UID at the
end of rollout. Bound probes reject inherited tool/namespace/port overrides.
The default unbound scripts remain available only for their legacy manual use.
These probes still use a loopback login and insecure TLS single-Vault health;
they are not authenticated full-quorum, financial readiness or data-recovery
qualification. Their checkpoint explicitly records `completeCellAcceptance=false`.
The dedicated acceptance capability blocker prevents them from being promoted
to complete-Cell success by removing an unrelated execution blocker.

Also required for final Cell acceptance: authoritative complete-Cell compatibility
checks behind the implemented signed Core Bank read producer,
complete KFE drain coverage, source-to-OCI provenance and signed publication,
real CSI/CNI/admission restore qualification, and a complete install/update/
interruption/recovery run against all actual services. These are distinct from
the already passing contract, archive, UI and governance tests. The snapshot
Kind experiment is deliberately disposable and currently incomplete; it does
not provide production backup evidence. No Vault signer is automatically
activated by this controller.

## KFE live evidence follow-up

The live maintenance gate requires the actual KFE `DRAINING` mode, this exact
operator change, a positive interoperable revision, a fresh observation and
explicit mutation/callback/read-side-effect coverage counts, all zero. Empty or
omitted coverage is not a safe drain. Duplicate/non-finite JSON is rejected.
The real KFE currently retains conservative unknown-coverage blockers; no
caller-provided status or this validation can remove them. Existing execution
capability blockers still prevent a real apply before its runtime mechanisms
are implemented and qualified.
