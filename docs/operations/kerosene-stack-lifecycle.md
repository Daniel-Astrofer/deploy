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

Provision public TUF root, governance roster, independent Vault compatibility
roster, snapshot-provider public key and real consensus anchor out of band.
Never trust anchors embedded in a downloaded
candidate. Independently install the governance and whole-Cell acceptance
verifiers and approve their binary digests. Private keys, sessions and Kubernetes credentials stay outside
the release archive and update journal.

After creating the owner-only bootstrap directory and placing its public trust
files there, the repository's reviewed controller sources can be installed with:

```sh
chmod 700 /protected/bootstrap-a
infra/install-stack-controllers --bootstrap-dir /protected/bootstrap-a
```

The command builds the Go consensus verifier with `-mod=readonly -trimpath`,
copies the whole-Cell verifier, refuses symlinks/shared permissions/existing
targets, and prints both SHA-256 digests. Review and approve those digests through
the independent release process before `kerosene-stack init`; the installer does
not create trust anchors, signatures, credentials or release authority.

```sh
chmod 700 /protected/bootstrap-a /protected/operation-a
infra/kerosene-stack init --cell-dir /protected/cell-a \
  --bootstrap-dir /protected/bootstrap-a
infra/kerosene-stack preflight --cell-dir /protected/cell-a \
  --release /media/release/deployment/release-lock.json \
  --deployment-manifest /media/release/deployment/deployment.json
infra/kerosene-stack install --cell-dir /protected/cell-a \
  --bundle-dir /media/release/deployment \
  --operation-dir /protected/operation-a \
  --apply
```

The protected bootstrap directory uses exact conventional names:

```text
cell-id                         kube-context
kubeconfig                      tuf-root.json
validator-roster.json           vault-roster.json
snapshot-provider.pub           consensus-anchor.json
kerosene-release-consensus      kerosene-cell-acceptance
```

The two verifier files must be executable. The directory is owner-only and is
never accepted together with individual bootstrap flags. `cell-id` and
`kube-context` each contain one ASCII token. This is an out-of-band trust and
cluster package, not part of the downloaded release bundle.

The owner-only operation directory uses:

```text
confirm-release                 change-id
operator-id                     admission-endpoint
admission-ca.pem                admission-cert.pem
admission-key.pem               maintenance-endpoint
maintenance-ca.pem              maintenance-cert.pem
maintenance-key.pem             maintenance-token
recovery-evidence.json
```

Only attribution and endpoint text are read into process arguments. Credential
files remain references and their contents are never copied to `cell.json`, the
release bundle or update journal. Individual operational flags remain available
for automation, but cannot be mixed with `--operation-dir`.

The equivalent expanded bootstrap command remains supported:

```sh
infra/kerosene-stack init --cell-dir /protected/cell-a --cell-id cell-a \
  --tuf-trusted-root /protected/root.json \
  --validator-roster /protected/roster.json \
  --vault-roster /protected/vault-roster.json \
  --snapshot-provider-key /protected/snapshot-provider.pub \
  --consensus-anchor /protected/consensus-anchor.json \
  --consensus-verifier /protected/kerosene-release-consensus \
  --acceptance-verifier /protected/kerosene-cell-acceptance \
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

The Vault compatibility attestation is separate release evidence. Its complete
document digest must equal `authorization.vaultCompatibility.attestationDigest`.
Threshold signatures are checked against the protected bootstrap roster, whose
members must have distinct canonical Ed25519 keys. The signed payload binds the
release ID/sequence/network, every repository commit, every service image and
configuration digest, source bundle, migration recovery evidence, plus external
rebuild, SBOM and provenance-set digests. It expires within 30 days. Because it
binds release contents rather than the canonical release-lock digest, the
release can safely bind the completed signed attestation without a circular
digest dependency. A downloaded release cannot replace the trusted roster.

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

The approved manifest must also match the Vault binary's current hardened
runtime contract. Every one of the three independent Vault controllers must use
`production` environment and ceremony modes, Tor transport, mTLS,
`distributed_wire` DKG, domestic/software attestation with a 64-hex measurement
pin, loopback-only `127.0.0.1:7801`, and `onion_or_spiffe` peer verification.
Each Vault controller must contain exactly one approved Tor sidecar and use its
loopback SOCKS endpoint; Vault and Tor are one rollout unit.
Each replica binds the Vault state directory and Tor hidden-service directory
to the same replica-local persistent volume. Its pre-authorized v3 onion key is
loaded from a read-only, member-specific Secret, while Tor publishes only the
derived hostname through an ephemeral shared volume. The sidecar must retain
the approved image entrypoint and consume the exact immutable, member-specific
`torrc`; command overrides, mutable configuration, shared identity Secrets or
foreign member names fail closed. Vault server/client certificates and CA are
likewise projected read-only from that member's mTLS Secret, and the configured
certificate paths are part of the approved runtime contract.
The two peer onion endpoints and mesh-audit public-key allowlist are mandatory
external Secret references, never inline manifest values. The attestation root
and AEAD share passphrase are external Secret references as well. Readiness must invoke
the Vault binary's authenticated `--health-probe` against the non-recursive
`/v1/local-health` endpoint; its URL remains bound to an approved ConfigMap and
resolves locally. Financial/quorum health remains a distinct acceptance gate.
This rejects the removed
staging/clearnet profile before Admin installation or any Kubernetes write.

The contract does not generate Tor identities, peer rosters, audit keys,
measurement pins or mTLS credentials. Those remain independently provisioned
bootstrap inputs. The Tor sidecar/onion identity layout and both Node planes now
pass the real integrated sequential protocol qualification described below.

`import-artifact` only integrity-checks and caches/extracts inert bytes. See
[release archive](release-archive.md) for offline import, size limits, mirror
policy and packaging. The unsigned candidate workflow does not publish an
authorized release or executable installation script.

`update`, `install` and `recover` share the evidence pipeline. `update` without
`--apply` produces a plan. `--cell-dir` supplies trust/state references and
`--bundle-dir` supplies inert evidence file locations; missing evidence never
becomes a bypass. Use `--help` for the exact required flags. Legacy v1/v2
signature receipts remain usable for inspection/dry-run, not real application.
When present, `BUNDLE/admin-image.oci.tar` is selected automatically; an
explicit `--admin-oci-archive` may be used when the archive is stored elsewhere.

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

First install no longer requires the live KFE drain endpoint, because no KFE
exists yet. It still requires the release-bound snapshot/recovery evidence and a
separately quorum-signed initial admission. Immediately before the first Admin or
Kubernetes write, the controller sends the exact consensus proof, release digest,
sequence and admission envelope to the fixed Bank consume route over mTLS. The
admission must bind the configured Cell ID, independently observed kube-system
UID, operator, change, Bank network and epoch. Missing inventory, database-plan
or external-Secret prerequisites fail before the nonce is consumed.

The client refuses proxies and redirects, bounds the response, rejects duplicate
JSON fields, and validates the returned admission digest plus consensus domain.
The Bank deliberately returns `installAuthorized:false`: its role is durable
quorum verification and one-time nonce consumption. The local controller may
continue only after proving that exact result; a transport failure is uncertain
and is never retried as a new consumption.

For that exact pre-write failure, rerun the same evidence with `recover`, the
original `--change-id`, `--operator-id`, exact `--resume-update-id`, and
`--recover-initial-install`. The controller accepts this mode only when the
retained journal contains exactly `snapshot-accepted`, `rollout-started`, then
the known uncertain-admission failure. It rechecks empty cluster inventory,
database plan and external Secrets, then calls the Bank's read-only
`inspect-recovery` route. Any admission/Admin/workload checkpoint refuses this
special recovery mode and requires the broader recovery procedure. Live
full-Cell qualification remains incomplete.

If failure occurs after a successful Bank admission checkpoint, use ordinary
`recover` with the same change/operator and exact `--resume-update-id`; do not
pass `--recover-initial-install`. Under the update lock, the controller proves
that the failed journal belongs to that exact initial installation and contains
one admission checkpoint. It then records `initial-admission-retained` without
calling Bank or consuming a nonce again. Repeated recovery attempts apply the
same rule.

For initial installation, PostgreSQL must become Ready before application
schemas are touched. Immediately before the Core/KFE phase, the controller
creates deterministic migration-credential Jobs in `kerosene-staging`: first
`migrate` for Core and KFE, then `validate` for both. A fresh installation
refuses pre-existing Job names. Post-admission recovery accepts only an exact,
successfully completed prefix in that order, revalidates every retained Job and
Pod, and creates only the missing suffix. A gap or changed identity fails closed.
Execution uses no retries, waits within the Job deadline, accepts exactly one
successful owned Pod, and rechecks Job/Pod UIDs, command, Secret references,
security context, runtime image ID and terminal state. Jobs are retained for
diagnosis and never automatically deleted or repaired. Core/KFE workloads are
not submitted unless all four observations pass. Real Kubernetes execution with
the current Core/KFE JAR images was qualified on 2026-10-04 in the bound Kind
audit cluster against PostgreSQL 17. Both exact built JARs first passed the
bounded `kerosene.cell.migration-capabilities/v1` contract. The first Core
migration Job completed, execution was interrupted, and recovery then
revalidated that retained Job by Job/Pod UID and immutable runtime image ID
before running KFE migrate plus both validation Jobs. All four Jobs completed
against separate databases and the migration blocker was removed. This lab
qualification does not authorize production credentials or existing-database
adoption.

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
startup phases: PostgreSQL/Redis/Tor/Node, Bitcoin, LND, Vault with its Tor
sidecar, Core/KFE,
then web-page. Each phase is submitted before its workloads are awaited;
in particular Core/KFE reciprocal integration references must not serialize
their initial submission. Every preceding phase must become Kubernetes-ready
before the next is submitted. This is an ordering policy, not proof of RPC,
financial readiness or consensus health. The approved topology contains six
independent Node controllers (three Bank and three Vault plane) and three
independent Vault controllers. Critical controllers are updated one at a time,
with the complete group rechecked before and after every mutation.
Each Node also receives the plane's immutable `node-<plane>-membership`
ConfigMap. Its `manifest.json` key is mounted read-only with `subPath` at
`/etc/kerosene/node-membership/manifest.json`, so the runtime sees a bounded
regular file instead of Kubernetes' ConfigMap symlink. The Node validates the
signed manifest against its genesis trust bundle and plane, then persists it
before discovery and readiness. A mutable source, wrong network/plane, missing
environment binding or directory mount blocks the deployment before apply.
Every Node member additionally mounts exactly one external Secret named after
its workload (`<workload>-identity`) at `/var/lib/kerosene/identity.key`. Only
the `identity.key` item is projected, mode `0400`, read-only and with `subPath`.
This keeps private root keys outside release artifacts while ensuring a fresh
Cell starts with the identities already authorized by the signed roster;
random first-boot identities cannot silently replace them. Secret preflight
checks the six names and required keys without reading their values.
The co-located Tor sidecar likewise requires a unique external
`<workload>-onion-identity` Secret containing `hostname`,
`hs_ed25519_public_key` and `hs_ed25519_secret_key`. The approved Tor image
validates their v3 formats, installs them atomically into the member's shared
persistent volume, accepts an identical restart and refuses any mismatch. Its
entrypoint cannot be overridden by the deployment. Consequently the onion
endpoint signed into membership is reproducible across install, restart and
recovery without embedding its private key in Git or the release archive.
The runtime contract also binds each member to an immutable network-matching
`node-genesis` ConfigMap, a member-specific `<workload>-mtls` Secret and an
immutable `<workload>-tor` ConfigMap with the exact loopback-only Node hidden
service. All referenced key names and file modes are checked during preflight;
missing files cannot be deferred until a failed rollout.
Each approved Node plane now also receives an immutable, read-only
`node-<plane>-state` ConfigMap at `/etc/kerosene/node-state`. It contains the
threshold-signed `StateSnapshotAttestationV1` and the exact payload bound by its
SHA-256 state root. The Node binary verifies network, plane, current membership
manifest, threshold, epoch and payload before financial readiness. A membership
change clears the prior binding; snapshot epoch regression or conflicting reuse
fails closed. The ConfigMap is release-digest input, but its presence alone is
not acceptance evidence: the live Node protocol qualification must observe the
binary reporting authenticated quorum and financial readiness.
Runtime checks additionally require a non-deleting workload UID and pod ownership
by that StatefulSet, or by a ReplicaSet controlled by that Deployment UID.
A ready pod with matching labels/images but foreign ownership is not accepted.
Receipts include workload/pod UIDs and rollout revision. Deployment pods must be
owned by ReplicaSets with the current Deployment revision; StatefulSets must
have equal current/update revisions and matching pod revision labels. Ready
pods from an older revision are rejected even when their image is unchanged.
Live desired replica counts must still equal the approved positive integer;
ready/updated counters must be actual integers, not JSON booleans. A scale change
after approval cannot be masked by a smaller set of ready pods.
After collecting pods, the controller re-reads the workload and rejects changes
to UID, generation, spec, revision, readiness counters or deletion state during
collection. Unrelated metadata/resourceVersion changes are not rollout changes.
This bounds cross-generation observations, not an atomic Kubernetes snapshot or
guarantee of continued health after the read; complete acceptance remains required.
Both the live workload template and the actual pod must retain the approved
named containers' commands, arguments, explicit environment and envFrom sources.
Injected startup inputs or changed Secret references are rejected without echoing
their values. Empty omitted startup lists are equivalent to explicit empty lists.
This does not yet verify every admission-added PodSpec field, volume projection,
mounted configuration byte or external Secret value.
Managed ConfigMap `data`/`binaryData` are additionally compared against approved
content after prerequisite apply, before each runtime phase and after rollout. Receipts
contain only namespace/name, UID and content digest; replacement or mutation
between checks blocks progress before the next phase is submitted. No Secret values are fetched. ConfigMap API
content equality does not prove kubelet projection freshness or application
reload; full mounted-file/application acceptance remains required. Failure does
not trigger automatic resource deletion or database rollback.
This verifies ownership/revision, not full runtime configuration or financial/quorum readiness;
those broader acceptance gates remain mandatory and unqualified.
Unidentified/ambiguously identified images, missing runtime components and
cross-phase colocated containers (including init containers) are unsupported
and block execution before resource writes, rather than silently choosing an
unsafe order. Such topologies need a separately implemented dependency policy.

The Admin image currently refers to the `kerosene-jctl` operator CLI, whose
container entrypoint exits after a command. It is not a permanent Deployment;
the planner rejects using it as one. The web client is a separate component.
Node/Tor colocation is supported in the first phase, matching both canonical
plane manifests and their shared onion identity volumes. The approved manifest
must contain exactly three independently persisted Nodes in each of the `bank`
and `vault` planes, in their respective namespaces, with loopback-only Node and SOCKS
listeners plus externally referenced discovery bootstrap. Vault/Tor colocation
is required in the Vault phase so its production-only loopback listener is not
exposed over cluster networking. No fallback to a
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

The integrated executor extracts `/opt/kerosene-jctl` from `admin-image.oci.tar`
in the offline bundle, or from an already cached approved OCI digest using a
created-but-never-started container. The offline path verifies the exact release
manifest digest, config and layer digests, platform and uncompressed `diff_ids`,
applies bounded OCI whiteout semantics and accepts only the launcher/JAR
distribution. The cached-image fallback pins creation to the inspected local
image ID, disables networking, bounds transfer/extraction,
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
and KFE help, plus OCI extraction/identity checks and failure journaling. On
2026-10-04 the built `installDist` was packaged as a compressed offline OCI,
validated and installed by the production installer without Docker, revalidated
from its receipt, and executed through the protected Cell wrapper for Core and
KFE help. This qualifies the Admin OCI installation capability and its dedicated
execution blocker was removed. It does not qualify real API authentication or
complete Cell execution.

## Remaining live execution qualification

Real `--apply` is explicitly blocked before mutation until all of these
implementation capabilities exist and their integration tests pass:

1. Live independent Vault rebuild/SBOM/provenance qualification is complete.
   On 2026-10-06, `infra/tests/stack-vault-rebuild-provenance-test.py`
   created two isolated contexts from Vault commit
   `87a91c122b495d4f4c205d76d65d538804c3f68d`, rebuilt both without layer
   reuse using digest-pinned Rust and Debian bases, and verified the source,
   Git tree and Dockerfile bindings in each OCI image. Both builds produced
   executable digest
   `sha256:70f60cfed984acc0df5e1fa4aa5d281da2b7189a29ea65f044cafdcfea498db0`
   and the same deterministic 89-package SPDX inventory digest
   `sha256:eb7f2747533e92598c270fda23bc915a2c491a71c27a40ff2a1fdb416dde1a23`.
   The evidence explicitly sets `releaseAuthorization=false`; it qualifies the
   rebuild mechanism and provenance gate without activating a signer or
   approving a particular deployment. This removed
   `vault-live-rebuild-provenance-not-qualified`.
2. Live qualification of the integrated Node/Vault replica-by-replica protocol
   is complete.
   The controller now requires one replica per workload, distinct approved
   persistent identities, all peers ready before and after every mutation, the
   Vault release threshold, and UID/resourceVersion update preconditions. The
   opt-in Kind qualification exercises real Deployments, PVC identities,
   controller revisions, one-at-a-time sequencing, unavailable-member refusal
   and recovery.

   On 2026-10-04, `infra/tests/stack-vault-protocol-kind-test.py` additionally
   qualified the actual production Vault binary in three fresh Deployments with
   independent PVC-backed onion identities, synthetic per-member mTLS material,
   non-recursive authenticated peer liveness and a 2-of-3 constitution. Every
   member reported `local_ready=true`, `financial_ready=true`,
   `configured_members=3` and `required_threshold=2`; a distributed-wire DKG
   then completed with each round authenticated as its originating Vault. This
   closed the standalone Vault protocol portion.

   The same date, `infra/tests/stack-node-protocol-kind-test.py` qualified the
   production Node binary in six fresh Deployments: three Bank and three Vault
   members, each with a distinct PVC-backed identity, onion and mTLS leaf. Both
   planes accepted independent 2-of-3 signed membership manifests and
   threshold-signed state snapshots. All six reported member, quorum and
   financial readiness; the Vault plane rejected a Bank manifest. This closed
   the standalone two-plane Node protocol portion.

   On 2026-10-06 the same qualification was extended to run both production
   binaries together. Six Nodes retained distinct PVC-backed onion and
   cryptographic identities while all three Vault-plane pods also ran the real
   Vault binary. Operational certificate names were kept separate from the full
   Node member hashes authenticated in SPIFFE URI SANs. The run proved both
   2-of-3 Node planes, all three financially ready Vaults, cross-plane manifest
   rejection, authenticated distributed-wire DKG, continued Bank availability
   and Vault quorum after removing one Vault member, and recovery with its onion
   identity unchanged. The emitted
   `kerosene.node-vault-protocol-kind-qualification/v1` evidence therefore
   qualified the integrated protocol and removed
   `node-vault-live-protocol-quorum-not-qualified`.
3. Live qualification of the integrated complete-Cell verifier contract. Its
   bootstrap-pinned executable must bind the Cell, cluster UID, release,
   sequence, change and operator; prove every release component plus rollout,
   interruption, restore, Node/Vault quorum and resume-guard scenarios; and
   report fresh financial readiness without activating signers or resuming
   maintenance. `complete-cell-live-acceptance-not-qualified` remains until a
   real full-service run produces that evidence.

The installed legacy smoke subprocesses now receive the bootstrap's explicit
kubectl executable, kubeconfig and context, after rechecking cluster UID at the
end of rollout. Bound probes reject inherited tool/namespace/port overrides.
The default unbound scripts remain available only for their legacy manual use.
These probes still use a loopback login and insecure TLS single-Vault health;
they are not authenticated full-quorum, financial readiness or data-recovery
qualification. Their checkpoint explicitly records `completeCellAcceptance=false`.
The pinned verifier runs after these probes and emits a separate
`complete-cell-acceptance-passed` checkpoint. The dedicated live-qualification
blocker prevents a mocked or synthetic verifier from authorizing apply.

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
