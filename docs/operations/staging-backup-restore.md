# Staging CSI backup and isolated offline restore check

These scripts create real CSI snapshots and restore real PVCs. The existing
collector remains the authority for the unsigned request's canonical ten-PVC
scope. Neither an unsigned request nor a successful shell invocation is a
provider receipt. The separate `attest` operation requires successful live
probes, exact evidence approval and an externally provisioned provider key.

Scope:

| Namespace | PVCs |
| --- | --- |
| `kerosene-staging` | `data-staging-{bitcoin,lnd,postgres,redis,tor}-0`, `vault-{1,2,3}-data` |
| `kerosene-staging-vault` | `data-vault-tor-0`, `vault-data` |

## Preconditions and authority

Use an explicitly named kube context. The scripts honor `KUBECTL` as an
executable path and require Bash and Python 3.9+; attestation also requires
OpenSSL with Ed25519 support. Store artifacts in a new private directory
outside every source checkout. Never commit the private key, backup contents,
shares, macaroons, secret values or actual operator evidence.

Obtain a maintenance change authorizing downtime of **all** Deployments and
StatefulSets in both staging namespaces. Disable GitOps reconciliation and
external writers for the entire maintenance window. HPA, DaemonSet, Job and
CronJob objects cause the backup to fail closed; remove them under that change
before proceeding, preserving their recovery configuration separately. This
implementation deliberately rejects even suspended Jobs/CronJobs rather than
trying to infer that a controller cannot start a writer. Ensure no storage
attachment outside these namespaces or out-of-cluster process can write these
volumes. Kubernetes inspection alone cannot prove absence of external writers.

The designated VolumeSnapshotClass must use `deletionPolicy: Retain`, and all
ten bound filesystem PVCs must use its CSI driver. Snapshot CRDs, snapshot
controller and driver snapshot/restore support must already be installed.
No default snapshot class is inferred. Block volumes and mixed drivers fail.

Approve a trusted offline-tools image by its immutable registry digest. It must
contain Python 3, PostgreSQL **16** `pg_controldata` and `pg_checksums`, Redis 7.4
`redis-check-rdb`, and a reviewed `bbolt check` implementation
that opens its database read-only. It must support the staging volume owners:
Postgres 70, Redis 999, Bitcoin/Tor 1000, Vault 65532 and LND 0. No fsGroup/chown
or permission repair is performed on restore. Any unreadable directory/file,
symlink, special file or unexpected layout fails. This includes unreadable
filesystem artifacts such as `lost+found`: qualify the actual storage layout
before approval instead of silently excluding data. Do not include Vault/LND
daemon entrypoints in the admission allowlist; the Pod command is literal
Python running offline tools only. The image's normal ENTRYPOINT is overridden.

An administrator must enforce the namespace's admission boundary before Pods
are admitted: only these exact offline probe Pod specs, no injected sidecars,
init containers, credentials, ephemeral containers, writable additional mounts,
RBAC, application controllers or exposed Services. Restrict namespace writes
to the maintenance principal. Baseline Pod Security is enabled by the script;
LND's root-owned data prevents requiring the Restricted non-root policy. LND's
probe drops every capability and has a read-only root filesystem and data mount.
The script rechecks resources and Pod specs but cannot undo execution by an
untrusted admission webhook. Admin enforcement is a mandatory approval, not a
claim inferred from a namespace label.

Verify CNI default-deny enforcement and reachable API, DNS TCP and Vault TCP
endpoints using a nonsecret maintenance canary before approval. The fresh
restore namespace receives a default-deny ingress **and** egress NetworkPolicy
before any probe. Each probe attempts connections to the approved numeric IPv4
endpoints and fails if one succeeds. Blocked/refused connections alone cannot
prove the CNI works: endpoints must first be known reachable, and the CNI and
admission boundary must be independently qualified. NetworkPolicy does not
isolate all host/node traffic. A private node pool or stronger CNI firewall is
needed where the threat model includes compromised nodes or host networking.
There are no DNS exceptions, token mounts, Secret copies, imagePullSecrets,
Services, ports, application boot, wallet unlocks or signer activation.

## Backup and quiesce

Synthetic command, using external paths and operator supplied identities:

```bash
bash infra/kubernetes/scripts/create-staging-snapshots.sh \
  --context staging-maintenance \
  --run-id cell-backup-change123-20261001 \
  --release-id example-release-123 \
  --release-lock-digest "sha256:${APPROVED_LOCK_DIGEST_HEX}" \
  --snapshot-class approved-retained-csi \
  --approve-stop-staging \
  --quiesce-hook /secure/operations/quiesce-staging \
  --timeout 900 \
  --output-dir /secure/evidence/change123-backup
```

The optional external hook is invoked as:

```text
quiesce-staging enter  <context> <absolute-evidence-directory>
quiesce-staging verify <context> <absolute-evidence-directory>
```

`enter` should close incoming application writers and perform any needed
service-specific drain/checkpoint operations. The script records original
replicas and PVC/PV identities in `maintenance.json`, scales all Deployments
and StatefulSets to zero, and waits for **all** Pods in both namespaces to
disappear. `verify` runs after this shutdown and can collect trusted source
filesystem digests through an approved offline storage mechanism. If the hook
uses temporary source-PVC reader Pods, it must remove and await deletion of
every reader before returning. Hooks are local trusted operator executables;
their exit status does not establish application correctness. No hook output
is promoted into a transactional consistency assertion.

Capture the complete source-file baseline for each PVC while writers are
stopped, before any writer is restarted, and approve it independently. A
storage-side offline mount or qualified temporary read-only source reader can
be used. The source read must not start a service or unlock wallets/shares. The
restore probe's `inventory` function defines the exact baseline algorithm:
recursively read every regular file, reject symlinks/special files/unreadable
directories, and form records with relative POSIX `path`, integer `size` and
hex SHA-256 `sha256`. Sort records by `path`; hash their UTF-8 JSON with
`ensure_ascii=False`, `sort_keys=True`, `separators=(',', ':')`. Prefix the hash
with `sha256:`. Directory ownership/metadata is not included. Review the
literal function in `restore-staging-snapshots.sh` when implementing the
external reader. Do not derive the approved source digest from the restored
PVC being tested: that would make the comparison circular.

Independent snapshots are created in their source PVC namespaces, with the
explicit class, unique run label and release/lock annotations. There is a
bounded wait for `readyToUse`, no CSI errors, and valid retained content
bindings. Pods and replica counts are repeatedly checked throughout the
snapshot window. Success produces `quiesced.json`, the unmodified collector's
`request.json`, and `completed.json`. Snapshot failure/timeout never emits a
request. Resource names are `<run-id>-<sorted-target-index>`; the collector uses
`backup.kerosene.io/run=<run-id>` to exclude historical backups.

This is a stopped-writer backup using independent snapshots. Graceful
termination can still time out and kill an application. There is **no atomic
cross-volume transaction, SQL logical verification, Bitcoin consensus replay,
LND payment recovery or Vault share decryption claim**. PostgreSQL's subsequent
offline probe requires a clean shutdown. The output explicitly records
`transactionalConsistency: false`.

All workloads remain stopped on success, error, interrupt or hook failure.
There is no automatic resume hook or signer activation. Review original
replicas and service-specific shutdown evidence before any separately approved
resume; Vault resume/arming always follows its own custody procedure. If the
stop fails halfway, `maintenance.json` identifies original replicas, but the
operator must inspect actual cluster state before recovery. For a retry use a
new run-id and evidence directory; retained partial snapshots need review.

## Restore across namespace boundaries

A PVC `dataSource` references a VolumeSnapshot in its own namespace, and a
VolumeSnapshotContent binds one namespace/name/UID. The workflow never points
an isolated PVC at a staging snapshot name, moves the original content binding,
or assumes the optional cross-namespace data-source feature is enabled.

Instead it reads and validates each original snapshot/content pair, and imports
the backend snapshot handle into a **separate** pre-provisioned retained
VolumeSnapshotContent bound to `import-<index>` in the fresh namespace. A local
PVC `restore-<index>` restores from that local snapshot, using the approved CSI
StorageClass and source restore size. The consumer Pod is created before
waiting for its PVC, supporting `WaitForFirstConsumer`. Source and alias
content must remain Retain, use the same driver and filesystem mode, and bind
the precise live snapshot UIDs. Backend handles are never included in evidence;
only their digests are included.

Original content must explicitly record `sourceVolumeMode: Filesystem` and
the dynamic source volume handle. Unknown modes fail closed. Restored backend
handles must differ from every original volume and from each other.

The CSI/storage administrator must explicitly approve **multiple retained
content representations of the same handle and restore from such an import**
for this particular backend/driver/version. This is not universally supported.
The scripts refuse to proceed without `retainedHandleImportSupported: true`;
CSI errors/readiness timeouts still fail closed. If the provider cannot safely
import a handle concurrently, do not approve: use a provider-supported
snapshot copy/export to a dedicated recovery backend in a separately designed
workflow. Never patch original content refs or switch aliases to Delete.

Create an external approval JSON with these fields (values below are synthetic;
replace every identity, endpoint and digest with reviewed values):

```json
{
  "context": "staging-maintenance",
  "namespace": "kerosene-restore-change123-one",
  "requestDigest": "sha256:<canonical-request-sha256>",
  "approvedBy": "storage-security-operator",
  "changeId": "change123",
  "driver": "qualified.csi.example",
  "storageClass": "approved-isolated-restore",
  "probeImage": "registry.example/offline-tools@sha256:<64-lowercase-hex>",
  "networkIsolationVerified": true,
  "retainedHandleImportSupported": true,
  "admissionAllowsOfflineProbeOnly": true,
  "egressTargets": [
    {"ip": "192.0.2.10", "port": 443},
    {"ip": "192.0.2.53", "port": 53},
    {"ip": "192.0.2.20", "port": 8443}
  ]
}
```

Canonical digests use the JSON encoding described above, for the entire object.
An external profiles JSON maps **all ten** `namespace/PVC` strings to exactly
`{"expectedTreeDigest": "sha256:<approved-source-tree-sha256>"}`. The request
and profiles are tied to the same stopped-writer snapshot window. No probe
command, expected success marker, or receipt is accepted from an operator file.

```bash
bash infra/kubernetes/scripts/restore-staging-snapshots.sh check \
  --context staging-maintenance \
  --request /secure/evidence/change123-backup/request.json \
  --namespace kerosene-restore-change123-one \
  --isolation-approval /secure/operations/change123-isolation.json \
  --profiles /secure/operations/change123-source-profiles.json \
  --timeout 1800 \
  --output-dir /secure/evidence/change123-restore
```

Existing namespace or output-directory reuse is refused, including partially
completed runs. The namespace must start `kerosene-restore-`; designate a new
name for each attempt. Successful probes read every file twice, compare the
first inventory with the approved source digest and the second with the first,
confirm the mount is read-only from kernel state, and perform these probes:

| Volume | Additional offline probe | Limits |
| --- | --- | --- |
| PostgreSQL | Version 16, `pg_controldata` clean shutdown; `pg_checksums --check` when enabled | Disabled checksums are explicitly recorded; no SQL replay |
| Redis | `redis-check-rdb` plus streaming read-only RESP/AOF and active manifest references, lengths and MULTI/EXEC validation | No live Redis or business-state validation; old single-file RDB-preamble AOF unsupported |
| LND | Read-only `bbolt check` for testnet wallet and channel databases | No unlock, peer connections or payment/channel recovery |
| Bitcoin | Testnet3 chainstate CURRENT/MANIFEST, `.ldb` files and blocks/index manifests plus approved complete-file hashes | Layout and byte integrity only; no LevelDB semantic or consensus validation |
| Tor | V3 onion checksum, public-key/address binding and secret-key envelope header/size | No Tor startup or onion publication; no secret bytes in output |
| Vault | Encrypted `shares/share-<hash>.bin` envelope minimum size and optional economy JSON object parsing, plus approved file hashes | No AEAD authenticity/decryption, TPM/TEE recovery or signer startup; TEE-only layouts unsupported |

Empty or uninitialized volumes fail. Actual paths that differ from these
staging layouts need a separately reviewed script change, not an arbitrary
operator-supplied command. Probe utility diagnostics are captured inside the
Pod and withheld from logs; logs expose only check names, counts and aggregate
tree digests. Evidence includes source/import/PVC/PV/Pod UIDs, backend-handle
digests, full Pod-spec digest, runtime image ID, successful completion and exact
log digest. Per-probe results may exist after failure; `evidence.json` with
`restoreTested: true` is emitted only after all ten live probes pass and the
namespace boundary is revalidated. Failed runs produce no receipt.

## Provider attestation after review

The provider/operator reviews the exact request, source baselines, isolation
approval, Pod commands/image IDs, per-volume probes, limitations and final
evidence. Use the printed **canonical** evidence digest (not `sha256sum` of
pretty-printed `evidence.json`) to approve the run. The provider private key
must be an explicit absolute external Ed25519 PEM file, mode 0600 or stricter,
outside the checkout and evidence directory. It is never generated or sent to
Kubernetes. The independently trusted public-key file is base64 DER, compatible
with `infra/kerosene-stack --snapshot-provider-key`.

```bash
bash infra/kubernetes/scripts/restore-staging-snapshots.sh attest \
  --context staging-maintenance \
  --request /secure/evidence/change123-backup/request.json \
  --namespace kerosene-restore-change123-one \
  --isolation-approval /secure/operations/change123-isolation.json \
  --profiles /secure/operations/change123-source-profiles.json \
  --timeout 1800 \
  --output-dir /secure/evidence/change123-restore \
  --approve-evidence-digest "sha256:${REVIEWED_EVIDENCE_DIGEST_HEX}" \
  --operator custody-operator-example \
  --provider snapshot-provider-example \
  --provider-key-file /secure/provider/snapshot-signing.pem \
  --provider-public-key-file /secure/trust/snapshot-provider.der.b64 \
  --expires-at 2026-10-01T18:00:00Z
```

Choose a future expiry within 24 hours of signing, and attest within 24 hours
of verification. Reuse the original timeout because it is part of the Pod
command/spec evidence. Attestation re-reads the live source and imported
snapshots, retained content handles, restored PVC/PV bindings, isolation
resources, exact probe specs, runtime statuses and logs. Changed/replaced
resources, input files, output logs, probe code or approval digest fail before
signing. No arbitrary receipt input is accepted.

The schema-compatible v2 receipt binds release ID, exact canonical request and
snapshot set. Its `snapshotId` is `restore-<canonical-evidence-sha256>`, binding
the exact resources and run outputs without changing the receipt schema. A
separate signed `approval.json` binds approving operator identity, evidence,
request and final receipt digests. The v2 controller validates the request and
receipt signature; it does not itself interpret the evidence digest encoded in
snapshotId or enforce the separate audit signature. Retain and verify that
audit bundle under the provider's independent review policy. The CLI operator
string is an audit declaration, not authentication of a human; custody of the
external provider key and the change process establish approval authority.

## Recovery, cleanup and real-cluster qualification

No cleanup is automatic. Keep resources for attestation and failure analysis.
Record the approved namespace and verify its run label/UID and all ten imported
content names before deletion. Delete only that isolated namespace after review;
then remove its ten cluster-scoped content aliases after confirming each is
Retain and the original snapshots/content still exist. Retain aliases do not
delete the backend snapshots; Kubernetes will also leave dynamic original
content/backend snapshots retained when original snapshots are deleted.
Provider-side deletion of the originals requires a separate retention/custody
decision. Restored PVC deletion follows the restore StorageClass/PV reclaim
policy. Preserve evidence securely outside Git before removing anything.
Do not delete or rebind original staging PVCs as part of a restore check.

Run local contract tests:

```bash
bash -n infra/kubernetes/scripts/create-staging-snapshots.sh
bash -n infra/kubernetes/scripts/restore-staging-snapshots.sh
bash infra/tests/staging-snapshot-workflow-test.sh
bash infra/kubernetes/tests/staging-volumesnapshot-attestation-request-test.sh
```

The new test explicitly uses **fake kubectl**, synthetic logs/storage metadata,
ordinary synthetic files and a disposable test-only Ed25519 key. It proves
orchestration contracts and cryptographic binding, **not CSI restoration,
filesystem utility correctness, network isolation or application recovery**.
No fake test evidence may be used as a release gate.

Before operational use, run the complete flow in an authorized disposable
staging Cell with real snapshot controller/CSI, ten populated volumes, a reviewed
offline-tools image, the actual admission policy and enforcing CNI. Independently
capture stopped-source inventories and use actual public provider trust policy.
Inspect all original content refs to ensure they never change; confirm restored
PV identities differ from source PVs and backend data can actually be read.
Verify read-only mount flags, zero credentials/Services/ports/sidecars, no Vault
or other application process and denied ingress/egress, including qualified
positive-control endpoints. Inspect actual `redis-check-rdb`, `bbolt` and
PostgreSQL checker behavior on the pinned utility versions; do not use latest
tools opportunistically during a run.

Exercise real CSI errors, readiness timeout, unsupported import, existing
namespace, a failed read/digest mismatch, corrupted Redis/LND data, unclean
Postgres shutdown, invalid Tor key binding and malformed Vault envelope. A
modified restored file must be rejected against its independently captured
source digest. Exercise Pod/spec/log/resource replacement before attestation,
wrong operator digest and wrong provider key. Each must leave no receipt.
Verify both Immediate and WaitForFirstConsumer storage modes where supported,
then follow the isolated cleanup and separately approved staging resume.
Keep the resulting real evidence and limitations with the change record.

Primary references: [Kubernetes volume snapshots](https://kubernetes.io/docs/concepts/storage/volume-snapshots/),
[Kubernetes NetworkPolicy semantics](https://kubernetes.io/docs/concepts/services-networking/network-policies/),
[PostgreSQL 16 offline checksums](https://www.postgresql.org/docs/16/app-pgchecksums.html),
and [bbolt read-only transactions](https://github.com/etcd-io/bbolt/blob/main/README.md).

The [Redis 7.4 AOF checker source](https://github.com/redis/redis/blob/7.4/src/redis-check-aof.c)
opens AOFs with `r+` even without repair flags. This workflow's own streaming
parser opens `rb` and does not copy the restored files to writable storage.
