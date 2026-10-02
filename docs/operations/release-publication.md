# Offline release publication

`infra/stack/publish-release.py` signs and atomically publishes one new local
offline bundle from an immutable release lock, an operator-supplied package
descriptor, and explicitly selected artifact bytes. It creates real TUF
targets, snapshot, and timestamp signatures and a separate Ed25519 package
signature. It does not build, download, upload, execute archive scripts, invoke
Git, install services, activate Vault signers, or write a Cell's trusted state.

This is an implemented offline publication interface, not a qualification of
a real build or release. Successful publication does not establish ordered
consensus approval, Vault compatibility, Bank compatibility, provenance,
snapshot/recovery readiness, or permission to apply. Its result explicitly
reports `releaseAuthorized:false`, `deploymentExecuted:false`, and
`qualification:"offline-publication-only"`. TUF signs the supplied release
target; the other independent authorization and operational gates remain
necessary. See [Cell lifecycle](kerosene-stack-lifecycle.md),
[release governance](../architecture/release-governance-v3.md), and
[unsigned archive packaging](release-archive.md).

## Inputs and trust boundary

Run on Linux with Python 3.10+ and an independently installed OpenSSL supporting
Ed25519 `pkeyutl -sign -rawin`. Use the installed Deploy verifier/lifecycle
modules beside the publisher, never tools from an archive. No extra Python
packages are required.

The operator must independently provision and approve:

- A protected out-of-band **signed TUF root metadata file**, with its intended
  role/key IDs and thresholds. A package's embedded root is not a trust anchor.
- Each explicitly authorized TUF signer reference, for the named role and key
  ID. The publisher derives its public key and checks it against that exact
  root role. It never discovers keys, chooses a signer, infers authorization,
  generates a root, or weakens thresholds. The root's own signature threshold
  is checked before private signer files are read.
- A separate packaging private key and the corresponding out-of-band public
  key for jctl. Packaging establishes local integrity only. Its public key
  must differ from every TUF authority key, including root keys.
- One independently provisioned private local publication store for this
  operator workflow, and its protected latest publication or explicit initial
  bootstrap decision. All cooperating publishers must select this same store.
  Merely finding a bundle or creating a different store does not authorize a
  new release. The store provides local serialization, not global authority.

The only implemented private signer reference is an explicit local path to an
unencrypted PKCS#8 Ed25519 PEM file. Each key file must be operator-owned,
single-link, and have no group/world permissions (normally `0600` or `0400`).
Parent and final symlinks, FIFOs, devices, encrypted PEM, other algorithms,
agent references, Vault/KMS/HSM/secret-manager URIs and executable signer
commands fail closed. Provision keys outside repositories and artifacts using
the organization's independently authorized process. This tool does not
provision or activate signers. The checked key bytes are copied only into a
fresh private scratch directory; original paths/keys are never emitted in
output. Exact private signer bytes selected as an artifact are rejected.
Scratch removal is not secure erasure; select suitably protected storage.
Never use or retain the test harness's disposable laboratory keys as authority.

`--release-lock` must be an existing valid `kerosene.release-lock/v2` or `/v3`
document. All eleven services and ten repositories are validated by the
installed controller. v1's legacy embedded proof fields are unsupported here.
The release lock is preserved **byte for byte**, including whitespace; the
TUF entry binds both its raw SHA-256/length and the detached canonical digest.
No release decision or compatibility fields are synthesized or rewritten.

`--package-descriptor` supplies exactly the six jctl fields shown below. This
synthetic template requires real approved identities and actual file hashes
and sizes before use; the strings in capitals are placeholders:

```json
{
  "schema": "kerosene.cell-package/v1",
  "releaseId": "synthetic-lab-release-1",
  "targetSequence": 1,
  "releaseLockCanonicalDigest": "sha256:CANONICAL_LOCK_HEX",
  "deploymentManifestDigest": "sha256:EXACT_DEPLOYMENT_FILE_HEX",
  "artifacts": [
    {"path": "deployment.json", "size": 123, "sha256": "EXACT_DEPLOYMENT_FILE_HEX"},
    {"path": "candidate.tar", "size": 456, "sha256": "EXACT_CANDIDATE_TAR_HEX"}
  ]
}
```

The ID, sequence, and canonical lock digest must match the supplied lock.
Artifacts are relative to `--artifacts` and each entry supplies an exact
nonnegative size and lowercase 64-hex SHA-256. Every selected file is streamed,
bounded, hashed, and copied into a private staging tree. Only listed files
are selected; no directory discovery or archive extraction occurs. Extra source
files are ignored. All selected bytes must be nonsecret. Hash checks cannot
detect every form of sensitive data; independent material review is required.

`deployment.json` must be one of the listed artifacts, at most 1 MiB, and its
raw byte digest must equal `deploymentManifestDigest`. On the staged bytes the
installed lifecycle validator checks the public `kerosene.stack.deployment/v1`
`staging-cell` contract, all service configuration digests and pinned runtime
images, supported resources, and its existing security restrictions. No
configuration digest formula is duplicated in the publisher. Deployment bytes
are preserved exactly. Production manifests outside this installed public
adapter's supported contract are unsupported.

The package descriptor is serialized into canonical UTF-8 JSON: sorted keys,
`ensure_ascii=False`, compact separators, no trailing newline, preserving
array order. Duplicate keys, nonfinite numbers and oversized documents are
rejected. This matches the existing Deploy encoder; it is not RFC 8785/JCS.
The packaging signature covers these exact output bytes, not the original
descriptor's whitespace and not a reserialized `signed` envelope.

## Invocation and output

The example uses protected operator-local placeholder paths and a synthetic
release ID. All versions, expiry values, root, key IDs and signer paths must
be supplied explicitly from the independently authorized publication decision.
For roots with thresholds above one, repeat `--tuf-signer` for each required
distinct key ID; the example alone would not satisfy such a root.

```sh
python3 -B infra/stack/publish-release.py \
  --release-lock /protected/operator/inputs/release-lock.json \
  --package-descriptor /protected/operator/inputs/package-descriptor.json \
  --artifacts /protected/operator/inputs/materials \
  --trusted-root /protected/operator/trust/root.json \
  --publication-store /protected/operator/publications \
  --packaging-key /protected/operator/keys/packaging.pem \
  --tuf-signer "targets:$TARGETS_KEY_ID=/protected/operator/keys/targets.pem" \
  --tuf-signer "snapshot:$SNAPSHOT_KEY_ID=/protected/operator/keys/snapshot.pem" \
  --tuf-signer "timestamp:$TIMESTAMP_KEY_ID=/protected/operator/keys/timestamp.pem" \
  --targets-version 1 --targets-expires "$APPROVED_TARGETS_EXPIRY" \
  --snapshot-version 1 --snapshot-expires "$APPROVED_SNAPSHOT_EXPIRY" \
  --timestamp-version 1 --timestamp-expires "$APPROVED_TIMESTAMP_EXPIRY" \
  --initial-publication \
  --output /protected/operator/publications/new-publication
```

`--publication-store` is required, including in the Python API as the keyword
`publication_store`. The directory must already exist, belong to the invoking
operator and have exactly mode `0700`; it is never automatically created.
Every parent is opened without following symlinks. Output and baseline paths
must be **direct children** of that selected directory, with simple lowercase
names of at most 128 characters. Nested destinations, paths into another store,
symlink aliases and the reserved ledger/reservation names are rejected.

`--initial-publication` is an explicit bootstrap, requires an actually empty
store, and requires all three role versions to equal 1. A nonempty store with
missing ledger is an error requiring manual investigation, not a bootstrap
opportunity. Initial replay in an established store is rejected. For subsequent
publication use
`--previous-publication /protected/operator/publications/previous-publication`
instead. Each role version and expiry must strictly increase over the supplied
authenticated baseline; the release sequence must increase, its ID must change,
and its network must match. Versions/sequences are positive integers at most
`9007199254740991`. Expiries are explicit UTC `YYYY-MM-DDTHH:MM:SSZ`, future
dated, and ordered `timestamp <= snapshot <= targets <= root`. There is no
clock override or automatic expiry extension.

The baseline's pinned root, signatures, timestamp/snapshot length/hash/version
references, release target bytes and canonical lock binding are checked by
the actual Deploy offline verifier. Its metadata must still be unexpired.
Delegations, extra metadata, modified roots or missing signatures fail before
publication. This is a single-release offline bundle interface. A new bundle
contains only its selected release/materials, not a cumulative mirror or prior
artifact history. Keep earlier immutable bundles for audit and recovery.
The publisher checks immutability of any overlapping target paths and holds
an exclusive `flock` on the pinned store **directory inode** throughout baseline
checking, signing, visibility, ledger commit and scratch cleanup. There is no
replaceable lock file. Lock acquisition is bounded. Competing publishers to
different destinations within this store serialize: after the winner commits,
the loser must select the new last baseline and advance; its stale baseline
fails. A byte-identical copy at another path is not the recorded last baseline.

The private canonical `publication-ledger.json` binds schema
`kerosene.publication-ledger/v1`, a positive monotonic `generation`, the exact
raw trusted-root SHA-256, network ID, and `lastPublication`. The latter records
its direct-child name, release ID, sequence, canonical lock digest, all four
metadata versions (including root), role expiries and `bytesDigest`.
`bytesDigest` is SHA-256 of canonical JSON containing the sorted relative
directory inventory and sorted file records `{path,length,sha256}`. Every file
is streamed and hashed, including artifact bytes, raw metadata, both descriptor
copies, and both package signature encodings. Directory names also enter the
digest; filesystem timestamps/ownership/modes do not. Inventory traversal is
bounded and rejects symlinks/special files.

The exact last baseline is reverified by the real offline TUF verifier and its
full byte inventory must match the ledger before private signer files are read.
Its release/sequence/versions/expiries must agree with the recorded high-water
state. Root or network-domain changes, stale baselines, modified package or
artifact bytes, initial replay and a deleted ledger with retained publications
fail closed. The ledger is private state, not a signed release authority or
tamper-proof history. This guarantee is for cooperating publishers selecting
one independently provisioned store; separate stores do not coordinate, and
malicious deletion/replacement by the operator UID is outside this boundary.
Do not erase/recreate a store to bypass its history. Protect/back up the whole
store and independently distribute the selected workflow's store identity.

For release ID `synthetic-lab-release-1`, the new output contains:

```text
manifest.json                         canonical jctl package descriptor
manifest.sig                          base64 of the raw 64-byte Ed25519 signature
manifest.sig.raw                      the same signature as 64 binary bytes
metadata/ROOT_VERSION.root.json        exact public root bytes; not a new anchor
metadata/targets.json                  signed release, descriptor and artifact hashes
metadata/snapshot.json                 signed targets version, exact length/hash
metadata/timestamp.json                signed snapshot version, exact length/hash
targets/releases/synthetic-lab-release-1.json          exact release lock
targets/releases/synthetic-lab-release-1.package.json  same bytes as manifest.json
targets/artifacts/synthetic-lab-release-1/             exact selected artifact bytes
```

All generated metadata is canonical JSON. The copied root retains the exact
operator-provided bytes. TUF signatures are standard lowercase hex Ed25519
signatures, keyed by SHA-256 of the canonical TUF key object. Thresholds count
distinct authorized key IDs; duplicate CLI references, incorrect private keys,
wrong-role keys and undersized signer sets fail. No root private key is needed.
The complete generated chain is checked with the installed offline verifier
before the output becomes visible. CLI stdout is a public canonical JSON
result; failures exit nonzero with no successful-publication result.

Verify the local package with an independently installed jctl and separately
provisioned base64 X509 DER packaging public key:

```sh
kerosene-jctl --output json cell package verify \
  --manifest /protected/operator/publications/new-publication/manifest.json \
  --signature /protected/operator/publications/new-publication/manifest.sig \
  --trusted-key /protected/operator/trust/packaging-public-key.b64 \
  --artifacts /protected/operator/publications/new-publication/targets/artifacts/synthetic-lab-release-1 \
  --deployment-manifest /protected/operator/publications/new-publication/targets/artifacts/synthetic-lab-release-1/deployment.json
```

jctl consumes `manifest.sig` (base64), not `manifest.sig.raw`. Its existing
`PackageVerifier` reports local package signature/bytes only and
`releaseAuthorized:false`. It does not validate TUF or order a release.
Deploy's `verify_tuf_metadata_bundle()` consumes `metadata/`, the selected
release-lock file, and the independent root. The public `verify-release` CLI
requires additional governance/Bank evidence when TUF flags are supplied;
this publisher does not manufacture those files to make that command pass.

## Bounds, atomicity, recovery and limitations

Default limits in the Python `Limits` API are:

| Resource | Bound |
| --- | --- |
| Each JSON input/output | 1 MiB |
| Each private signer PEM | 16 KiB |
| Artifacts | 1..1024 |
| Each artifact / total selected artifact bytes | 512 MiB / 1 GiB |
| Key IDs and signatures per role / total root keys | 32 / 128 |
| Artifact UTF-8 path bytes / depth | 240 / 32 |
| Publication deadline / each OpenSSL call | 300 s / 10 s |
| Store-lock acquisition / ledger or reservation JSON | 10 s / 64 KiB |
| Complete publication inventory files / filesystem nodes | artifacts + 9 / 36 × (artifacts + 9) |
| Inventory relative UTF-8 path bytes / directory depth | 512 / 36 |

The API may tighten limits or raise artifact byte limits up to jctl's 2 GiB
per file; metadata/count limits cannot exceed jctl's bounds. Ordinary regular
file I/O/fsync relies on the local filesystem making progress; the deadline
is checked between streamed reads/phases and bounds subprocess waits. It does
not interrupt a stalled kernel filesystem call. Provision space for input,
staging, retained publications, and private scratch. Retention/pruning is
manual. Paths reject symlinks in every component, traversal, URL syntax,
duplicate names, file/directory collisions and special files. Reads pin parent
directory descriptors rather than follow links after a path check.

The output parent is the pinned operator-owned `0700` publication store. Protect its
ancestors and all inputs against mutation by other accounts; the tool is not
a sandbox against another process controlling the operator UID. Selected
bytes are verified as they are copied, then all signing/validation uses the
pinned copies. Files are exclusive, fsynced and mode `0444`; directories are
private. Staging and destination reside on the same filesystem.
Linux `renameat2(RENAME_NOREPLACE)`, relative to the pinned store descriptor,
publishes the complete directory and fsyncs its parent. Unsupported atomic publication fails closed, and existing output
files, directories or symlinks are never replaced, including races.

Before output visibility, the publisher exclusively and atomically writes and
fsyncs `publication-reservation.json` and the store directory. Its canonical
`kerosene.publication-reservation/v1` record contains `baseLedgerDigest` (null
only for bootstrap) and the complete proposed next ledger as `candidate`.
Only then does the output rename/fsync occur. The next ledger is written to a
fresh exclusive `0600` file, fsynced, atomically renamed over the prior state
(or published with no replacement for the first state), and the pinned store
directory is fsynced. The reservation is removed and its directory fsynced
only after the new ledger is durable. Existing publications are never replaced.
CLI success is reported only after ledger commit; the public result adds
`publicationStoreGeneration` and `publicationBytesDigest`.

On failure, leave the prior published bundle and original keys/trust untouched.
Failures before reservation leave the ledger unchanged and permit a corrected
retry. Once a reservation exists, **every subsequent invocation refuses the
store**, even when output is absent or the ledger already matches its candidate.
A crash after output visibility but before ledger commit therefore leaves an
unresolved reservation, rather than silently adopting the output, retrying the
old high-water mark or allowing another initial publication. Rename failures
after reservation also require manual investigation. Failure after committed
state may leave a visible output with an already advanced ledger; investigate
the ledger and reservation before retrying. No automatic reset/recovery exists.

Manual reconciliation must be independently authorized and hold the same
directory lock. Preserve the current ledger, reservation and any visible output
as evidence. Verify the reservation's schema/domain and `baseLedgerDigest`
against the protected prior ledger (or establish why the candidate ledger was
already committed), reverify the reserved output with the independent trust
root, real TUF verifier, independently pinned packaging public key and actual
jctl, and match the full `bytesDigest`, release, sequence, versions and expiries
to the candidate. Inspect whether the output rename happened and whether the
ledger write is durable. An authorized recovery procedure must either finish
that exact reserved publication monotonically or document resolution without
reusing/resetting its proposed release/version history. This tool deliberately
does not implement such policy or clear the reservation. Do not merely delete
the marker, point to an older baseline or bootstrap a replacement store.

Normal exceptions remove invocation-owned staging/key scratch. After interruption/power loss,
inspect only identified `.publication-*` and `.publication-keys-*` directories
when no process uses them; key scratch may contain private keys. Clean those
specific paths under the organization's secure-storage policy, after preserving
reservation evidence and reconciling the store. No deployment rollback is
needed because the tool changes only local publication/store state.
Do not delete Cell journals, reset high-water marks, replace trusted roots,
or activate Vault signers to recover publication failures.

This version supports only TUF `1.0.0`, four top-level root roles and
`consistent_snapshot:false`. Root rotation, delegated targets, extra roles,
consistent-snapshot filenames and metadata-only refresh/recovery from expired
prior metadata are unsupported and fail closed. Handle those policy changes
through a separately designed and independently authorized mechanism; do not
rewrite a root or use bootstrap to bypass them.

The publisher checks actual artifact bytes, immutable lock/descriptor bindings,
and installed deployment configuration policy. It does not unpack archives,
check Git history or SBOM completeness, prove an OCI export matches the lock's
registry image digest, establish source-to-image provenance, or create signed
build/compatibility attestations. OCI manifest digests and tar-file hashes have
different meanings. It emits no purported build provenance, Bank approval,
Vault compatibility, consensus receipt, production readiness or live test
result. Real build/publication qualification, key custody/distribution,
registry/mirror promotion, operational approval and complete Cell integration
remain separate work.

## Tests

```sh
python3 -B infra/tests/stack-publication-test.py -v

# Optional explicit reference to an already installed jctl; no Gradle/build:
JCTL_PUBLICATION_TEST_BIN=/absolute/path/to/kerosene-jctl \
  python3 -B infra/tests/stack-publication-test.py -v
```

The tests generate disposable synthetic laboratory keys outside the repository
and use real OpenSSL signatures and the actual Deploy offline verifier. They
exercise two-of-two thresholds for every root/publication role, incorrect
key/role references, packaging-key separation, canonical/raw byte bindings,
size/hash/count/path bounds, symlinks/FIFOs, duplicate/deep/nonfinite JSON,
immutable output and races, baseline rollback/expiry/rotation/delegation
rejection, pinned artifact growth, signer failure cleanup, reproducibility,
and nonexecution of selected scripts. Store tests exercise ledger byte/domain
binding, stale/aliased baselines, initial replay and missing-ledger reset,
private directory/state permissions, malformed/oversized/high-water state,
bounded lock contention, real concurrent processes publishing to different
destinations, and an injected process exit after visibility before ledger
commit. Reservation tests also cover first-publication commit failure and
interruption after durable state write. The optional installed-jctl test checks
the produced manifest and signature and separately rejects tampered artifact
bytes and signature bytes. `JCTL_PUBLICATION_TEST_BIN` must be an absolute path
to an existing executable; no shared Gradle/build directory is written.
No tests sign a real release, build a service, access production signers,
contact a service API or modify an existing Cell/Admin/Core/deploy file.
