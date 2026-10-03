# Release archive and unsigned candidate packaging

These tools transport and assemble release **data**. They do not authenticate
a release, sign artifacts, establish TUF trust, activate Vault signers, apply
Kubernetes resources, run downloaded scripts, build images, or update a Cell.
An authenticated digest and size must come from an independently approved
release decision before an operator treats a downloaded object as trusted.
The existing release-lock schemas and `infra/kerosene-stack` are unchanged.

## Archive API

`infra/stack/archive.py` is a Python 3.10+ standard-library module and CLI for
Linux. Import it by adding `infra/stack` to the Python module search path.
Errors raise `ArchiveError` (a `ValueError`) or `OSError`; the CLI exits 1.

```python
fetch(cache, digest, size, mirrors=(), *, offline=False,
      allow_file=False, limits=DEFAULT_LIMITS) -> pathlib.Path
extract(archive, destination, digest, size, *, limits=DEFAULT_LIMITS) -> pathlib.Path
install(cache, destination, digest, size, mirrors=(), *, offline=False,
        allow_file=False, limits=DEFAULT_LIMITS) -> pathlib.Path
```

`digest` is exactly `sha256:` followed by 64 lowercase hex digits; `size` is an
exact nonnegative integer byte count. Mirrors are **base URLs** selected by
the operator, in fallback order. Each object is read from
`BASE/sha256/HEX` and stored at `CACHE/sha256/HEX`. There is no mirror discovery
from archive contents. Each cached read checks the entire digest and size.
Missing, corrupt, oversized, or short mirror responses advance to the next
explicit mirror. A corrupt cache object is replaced only after a complete
replacement verifies. Failed temporary downloads are removed.

Network mirrors require HTTPS, standard TLS certificate validation, HTTP 200,
and identity content encoding. Credentials, query strings, fragments,
redirects (including HTTPS redirects), and environment HTTP proxies are
disabled. Configure the final mirror URL explicitly. Mirror diagnostics name
the mirror's ordinal and error type, without logging response bodies or URLs.

`offline=True` / `--offline` means **cache only**: a missing or corrupt object
fails without opening either network or local mirrors. Local-media import is
a separate explicit mode: `allow_file=True` / `--offline-file` accepts only
absolute local `file://` mirror bases (empty host or `localhost`), forbids
mixing HTTPS mirrors, and still verifies digest and exact size. The CLI makes
these two modes mutually exclusive. Supplying an unauthorized file mirror is
an error even if the cache already contains the object.

Synthetic usage (replace all paths, digests, and sizes with approved inputs):

```bash
python3 infra/stack/archive.py fetch \
  --cache /srv/operator/release-cache \
  --digest "sha256:$APPROVED_ARCHIVE_HEX" --size "$APPROVED_ARCHIVE_SIZE" \
  --mirror https://mirror.example.invalid/releases \
  --mirror https://backup.example.invalid/releases

python3 infra/stack/archive.py fetch \
  --cache /srv/operator/release-cache --offline-file \
  --digest "sha256:$APPROVED_ARCHIVE_HEX" --size "$APPROVED_ARCHIVE_SIZE" \
  --mirror file:///srv/operator/offline-media

python3 infra/stack/archive.py install \
  --cache /srv/operator/release-cache --offline \
  --digest "sha256:$APPROVED_ARCHIVE_HEX" --size "$APPROVED_ARCHIVE_SIZE" \
  --destination /srv/operator/unpacked/new-candidate
```

Installation is data extraction, **not deployment**. The destination must be
new. A verified private snapshot is parsed so cache changes cannot race
verification and extraction. Only basic tar/POSIX ustar and gzip-wrapped tar
are accepted. Extraction reads fixed 512-byte headers rather than allowing
PAX/GNU extension payloads to allocate unbounded metadata. It rejects links
(both symbolic and hard), devices, FIFOs, sparse files, PAX/GNU extensions,
duplicate paths, absolute paths, `..`, `.`, empty path components, backslashes,
colons, control characters, inconsistent/truncated headers or payloads,
nonzero padding, and nonzero trailing tar data. ZIP and executable installers
are unsupported. Owner/group, timestamps and executable bits from the archive
are ignored. Files become `0444`; directories are operator-private.

The cache and destination parent must be owned by the invoking operator and
not group/world writable. Directory traversal opens each component without
following symlinks. Use a private operator tree with protected ancestors;
do not allow another same-UID process to mutate these namespaces. This is
not a sandbox against an adversary controlling the operator account.

Per-object advisory locking serializes cache writers with a bounded wait.
Files are written to exclusive temporary names on the destination filesystem,
flushed, fsynced, and renamed only after verification; their parent is fsynced.
Extracted files/directories are fsynced before Linux `renameat2(RENAME_NOREPLACE)`
publishes the complete new directory. Existing outputs are never overwritten.
Unsupported no-replace publication fails closed. A final parent-fsync failure
can report failure after the complete output is visible; inspect that output
before retrying. Power loss can leave an unreferenced staging directory, but
does not expose an incomplete final tree.

`Limits` provides positive integer bounds, customizable through the Python API:

| Limit | Default |
| --- | --- |
| Archive/download bytes | 512 MiB |
| Individual extracted file / Git bundle | 256 MiB |
| Decoded tar stream (headers and padding included) | 1 GiB |
| Archive members / filesystem nodes | 10,000 each |
| UTF-8 path length / depth | 240 bytes / 32 components |
| Decoded stream / compressed archive ratio | 100 |
| HTTPS socket timeout | 30 seconds |
| Transfer / extraction / cache-lock wait | 300 seconds each |

The stream bound includes trailing gzip members and tar padding. JSON package
inputs and captured Git output have a separate 16 MiB limit. Disk allocation
and aggregate cache retention remain operator responsibilities: provision
space for the cache object, verified snapshot, and extracted tree. Downloads
do not automatically prune old immutable objects.

## Candidate input and packaging API

`infra/stack/package-release.py` takes a JSON selection file and a **new**
output directory. `assemble(selection_path, output, *, input_root=None)` returns
the index dictionary; CLI stdout is its canonical JSON representation. Paths
in the selection are relative to the selection file, or explicit absolute
operator-local paths. `--input-root` confines all selected inputs and Git
object stores to that root; Git alternates are rejected in this mode.

```bash
python3 infra/stack/package-release.py \
  --selection /srv/operator/candidate-input.json \
  --output /srv/operator/candidates/new-candidate
```

The input contract is `kerosene.release-candidate-input.v1`. Unknown fields
are rejected. This synthetic example requires actual full commits and actual
material digests before use:

```json
{
  "schema": "kerosene.release-candidate-input.v1",
  "repositories": {
    "deploy": {
      "path": "repositories/deploy",
      "commit": "1111111111111111111111111111111111111111"
    },
    "core": {
      "commit": "2222222222222222222222222222222222222222",
      "bundle": {
        "path": "materials/core.bundle",
        "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "size": 1234
      }
    }
  },
  "services": {
    "admin": {
      "image": "registry.example.invalid/admin@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "configDigest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    }
  },
  "deployment": {
    "path": "materials/deployment.json",
    "digest": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  },
  "sbom": {
    "path": "materials/sbom.json",
    "digest": "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
  }
}
```

Select 1..32 named repositories and 1..64 named services including `admin`.
Complete Cell candidates should select all repositories/services required by
the release lock. Partial candidates are permitted for review/testing and do
not establish a complete or authorized release. Commits are full lowercase
40-hex SHA-1 or 64-hex SHA-256 commit IDs; branch names, abbreviated hashes,
tags, and `HEAD` are rejected. Images require an OCI reference pinned by
`@sha256:HEX`. The packager records image references and does not pull, build,
or claim to verify the contents of those images.

For each repository choose exactly one `path` or `bundle`. Local repositories
are read without network fetching, with source hooks/configuration excluded
from the isolated bundle-building repository and replacements/fsmonitor
disabled. Bundles include the selected commit's **entire ancestor history**,
one `refs/heads/candidate`, and no prerequisites. Dirty/untracked files,
other branch tips, working-tree filters, and repository scripts are never
packaged or executed. Full Git history can contain old sensitive data;
review the selected histories before using this pipeline. Missing ancestor
objects (including incomplete shallow/partial clones) fail. Existing bundles
must meet the same single-head/header/full-history contract, match digest and
size, and pass isolated Git verification, unbundling, commit-type checks, and
strict object connectivity checks. No bundle worktree is checked out.

An optional repository `bundleDigest` pins the generated/imported bundle hash
and fails on mismatch. Bundles use a fixed candidate ref and deterministic
single-threaded non-delta packing. Reproducibility is checked with the same
tool bytes and Git version; cross-version Git byte reproducibility is not
promised. The provenance records both Python tool hashes and the Git version.

`deployment.json` uses the coordinator/executor contract:

```json
{
  "schema": "kerosene.stack.deployment/v1",
  "environment": "staging-cell",
  "resources": [],
  "admin": {
    "image": "registry.example.invalid/admin@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "config": {"apiBaseUrl": "https://synthetic-core.invalid"}
  }
}
```

The packager checks this envelope and that Admin's image matches the selected
Admin service. It preserves **exact deployment.json bytes**, including
whitespace and resource array order. It does not render templates, reorder
the packaged resources, or execute manifest commands. When `lifecycle.py` is
installed beside the packager, it calls that module's `component_config()`
and `digest()` to compute each `configDigest`; a supplied selection digest
must match, or it fails. Omitting `configDigest` requests computation.
It then calls the installed controller's `verify_deployment()` on a private
snapshot of the exact bytes. The deployment executor owns the formula: each digest binds
`{resources: sorted non-workload resources plus workloads whose container/init
image equals the service image, admin: the admin object only for admin}` using
its canonical JSON rules. No copy of that formula is defined in the packager.
The shared verification also enforces the runtime-image allowlist, inline
Secret prohibition and dangerous cluster-RBAC rejection. Installed controller
and lifecycle hashes are recorded in provenance, together with the installed
`admin_install.py` dependency and both installed PostgreSQL primitives
(`create-service-databases.sql`, `service-runtime-grants.sql`). Dependency files must be regular non-symlink
files from the controller installation; cached Python modules from another
checkout are rejected before controller loading. Their file digests are read
before and after configuration validation; a change rejects candidate assembly.
These are local tool-material records, not signed build attestations or a
sandbox against another process controlling the operator account. If lifecycle is absent,
every service must provide `configDigest`; assembly remains possible but
`configurationVerification` explicitly records `unverified-lifecycle-unavailable`.
That output still requires the executor's full validation before use.
All packaged inputs must be nonsecret; do not put secrets into Admin config,
resources, SBOMs, selection files or source history.

The SBOM is operator-supplied, selected by the digest of its **exact bytes**,
and preserved as `sbom.json`. Its JSON must identify SPDX (`spdxVersion`) or
CycloneDX (`bomFormat`). This is an envelope check, not a dependency inventory
scan or a full SBOM schema validation. Supply separately generated and reviewed
SBOM evidence for the explicitly selected source/image materials.

## Output contract: kerosene.deployment-bundle.v1

The output directory contains `candidate.tar` and `index.json`. The index is
outside the archive to avoid a self-referential archive hash. Its fields are:

| Field | Contract |
| --- | --- |
| `schema` | Exactly `kerosene.deployment-bundle.v1` |
| `status` | Exactly `unsigned-candidate` |
| `candidate`, `trusted` | Always `true`, `false` respectively |
| `archive` | Typed file descriptor: `{type:"file", path:"candidate.tar", digest:"sha256:HEX", size:INTEGER, format:"tar", mediaType:"application/x-tar"}` |
| `configurationVerification` | `verified-installed-lifecycle` or `unverified-lifecycle-unavailable` |
| `canonicalRenderedManifestsDigest` | SHA-256 of canonical JSON of the **entire deployment object**, including Admin |
| `entries` | Sorted array of `{path, digest, size}` for every archive file |
| `repositories` | Name map of `{commit, bundleDigest}` |
| `services` | Explicitly selected name map of `{image, configDigest}` |

Archive files are `deployment.json`, `sbom.json`, `selection.json`,
`provenance.json`, and `sources/NAME.bundle`. `selection.json` records only
portable commits/digests/services, excluding local machine paths.
`provenance.json` is an **unsigned** in-toto Statement v1 with custom predicate
`urn:kerosene:release-candidate-assembly:v1`, the selected inputs, tool versions/
digests, and subjects covering all payloads except itself. It is assembly
evidence, not a signed SLSA attestation or proof of image build provenance.
Its own hash is bound by the outer index/archive. No timestamp, branch tip,
absolute source path, signing identity, or generated trust root enters output.

Canonical JSON is UTF-8, sorted object keys, `ensure_ascii=False`, separators
`,`/`:`, and no trailing newline, as in the existing stack canonical encoder.
Array order is preserved. Duplicate keys and nonfinite numbers are rejected;
finite numbers use Python's JSON serialization. This is not RFC 8785/JCS.
The raw `deployment.json` entry digest is intentionally separate from
`canonicalRenderedManifestsDigest`. Consumers must authenticate the index/
archive digest externally, check exact entry bytes/size, then apply their
own release-lock/trust/policy checks. Merely having a matching SHA-256 is not
release authorization. No circular binding to `source.bundle.digest` is
manufactured here: authorized release metadata is assembled separately after
the candidate's archive and entry digests are known.

The tar is sorted POSIX ustar, uncompressed, with UID/GID/mtime zero, empty
owner/group names, and file mode `0444`. Both candidate assembly and extraction
publish into new directories atomically and never replace a prior candidate.

## Manual CI, validation and recovery

`.github/workflows/release-candidate.yml` runs only on manual dispatch. Provide
a full Deploy `selection_commit` and a relative `selection_path` at that commit.
Tools come from the workflow revision; selected material scripts never run.
The checkout is complete (no shallow history), credentials are not persisted,
and input paths/object stores are confined to the materials checkout. The
selection file's local paths are relative to its own directory. Other polyrepo
sources must be supplied as digest-pinned full-history bundles in that checkout;
the workflow does not discover repositories or fetch arbitrary URLs.
The workflow runs the security tests, assembles twice, compares exact tar/index
bytes, and uploads an artifact named `unsigned-candidate-COMMIT` for seven days.
It has read-only repository permissions and no signing, OIDC, registry publish,
trusted release promotion, or deployment step. Recording a CI artifact does
not make it trusted. Pin the runner/Git runtime externally if long-term
cross-run reproducibility is required.

Run local verification:

```bash
python3 infra/tests/stack-archive-test.py
```

Tests use disposable synthetic Git repositories and real local offline mirror
files. They exercise cache tampering and repair, corrupt/missing mirror
fallback, exact-size/digest errors, missing offline installs, symlinks,
traversal, duplicate/special members, decompression limits, malformed/truncated
tar/gzip, preservation of exact manifest bytes, full-history bundle imports,
hook suppression, shared lifecycle digest verification/policy rejection,
immutable output, and repeatable packaging despite a moved
branch tip or dirty worktree.

No deployment rollback is necessary because these commands change only local
candidate/cache data. On failure, keep the prior verified candidate, repair
the operator mirror/input selection, and retry into a new destination. Rehash
objects before reuse. After an interrupted run, inspect and remove only the
identified `.download-*`, `.extract-*`, `.candidate-*`, or `.pack-work-*`
temporary paths when no process is using them. Do not replace trusted metadata
or activate signers to recover a failed download. Candidate promotion and any
staging/production rollout must pass the separate release authorization,
manifest/configDigest, snapshot and rollback gates.
