# Complete Cell implementation checkpoint — 2026-10-02

**Not complete; installation/update remains fail-closed.** Scope is the entire
Cell: Admin/jctl and operator UI, Core, KFE, Node, Vault, web-page, PostgreSQL,
Redis, Bitcoin, LND and Tor. This checkpoint supersedes the October 1 statements
about missing KFE compilation and missing offline publication. Passing component
tests does not establish complete-Cell readiness or release authority.

Service work remains on isolated `feat/complete-cell-operations` branches.
Deploy remains on `feat/staging-volumesnapshot-attestation-request`. Dirty primary
checkouts are untouched. No production update, custody operation, trusted release
signer provisioning or Vault signer activation was performed.

## Integrated and verified in this wave

### KFE: `3cea91a`

- Restored the exact historically committed pricing/quorum facades from
  `6b287b1`, before the incomplete extraction. No substitute financial algorithm.
- Real standalone security-chain integration, authenticated ADMIN maintenance
  endpoints, durable ACTIVE/DRAINING control and audit/revision checks.
- V57/V58 independent persistent admissions and durable child provenance;
  commit/rollback gating, once-only child claim, restart/concurrent claim tests.
- Guarded submit/outbox claims, channel execution/producers/queue state writes,
  PSBT, Vault day rotation, wallets/UTXO scans, payment requests and capability-
  checked expiry-on-read, cancellation, notifications and statement retention.
- Webhook child capture before commit/enqueue; parent commit never proves remote
  delivery. JWT verifier errors no longer swallow downstream business failures.
- Full `./gradlew --no-daemon check bootJar`: **747 tests, zero failures/skips**.
  The actual Flyway V1–V58 chain and PostgreSQL concurrency/restart suites ran in
  two separate explicitly disposable databases, not only a minimal schema fixture.
- CI references real Contracts/Shared repositories at immutable revisions and
  explicitly enables the two PostgreSQL suites. Hosted CI has not been claimed.

HTTP roots and remote best-effort workflows conservatively retain UNCERTAIN.
All three unknown coverage blockers remain 1. This can accumulate unresolved
admissions; there is no force-clear or automatic recovery/resume. **Do not ship
this partial boundary assuming completed HTTP requests will permit an update.**
See KFE's STATUS, entrypoint inventory and bounded maintenance runbooks.

### Offline publisher and actual operator consumer

`infra/stack/publish-release.py` now produces real signed TUF targets/snapshot/
timestamp and the exact package descriptor/signature accepted by installed jctl.
It preserves release-lock/artifact bytes, checks the installed deployment validator,
enforces explicit protected signer references and distinct thresholds, and never
executes artifacts or provisions authority. A mandatory private publication store
provides directory-inode locking, monotonic high-water state, exact last-bundle
byte binding and a durable reservation before visibility. An interrupted
reservation blocks further publication until separately authorized reconciliation.

**45 tests passed**, including the actual Deploy TUF verifier and installed jctl,
artifact/signature tampering, competing publishers, stale/reset/domain rejection
and process exit between publication visibility and ledger commit. Successful
local publication still reports `releaseAuthorized:false`; it is not independent
build/provenance, Bank/Vault compatibility, BFT approval or deployment permission.
No real release keys were used. [Publication runbook](release-publication.md).

### Snapshot contracts and disposable CSI lab

The production collector accepts the exact real kubectl generic `v1/List` or
typed snapshot list, validates every item and the complete ten-volume scope,
and rejects duplicate/nonfinite/oversized/paginated responses. The actual stock
CLI passed in the existing isolated Kind lab without the response adapter and
produced byte-identical request bytes.

Restore validation now accepts only Kubernetes' omitted default-false host flags
and omitted empty NetworkPolicy rule arrays. Token/mount/security fields remain
required; enabled host networking, null/nonboolean flags and traffic allowances
remain rejected. Fake kubectl contracts, negatives and literal synthetic-file
checks passed; these are **not** real restore qualification.

The retained lab has ten real ready Retain snapshots, real utility corruption
checks and real TCP ingress/egress controls. It has **no qualification.json**:
the WFFC pilot is Pending after VolumeBinding timeout. The final harness no longer
substitutes a derived executor for the ten-probe production restore CLI. Its
preflight passed, but qualification exited 78 before new fixture mutations because
Docker had only 142 MiB available, below the 256 MiB working margin. Earlier
failure bytes were preserved in blocker-history. No broad cleanup or filesystem
resize was performed. [Lab status and recovery scope](staging-csi-lab-qualification.md).

### Regression verification

Deploy archive suite: 18 passed. Lifecycle suite: 14 passed. Release-lock and
signed-evidence suites, snapshot collector/workflow contracts and deployment
architecture guardrails passed. Lab Python and Bash syntax checks passed; its
unfinished live qualification is not counted as a passing test. Prior Core,
Node, Contracts, Admin, Clients, Vault and four-validator CometBFT results remain
recorded in the [October 1 checkpoint](cell-implementation-status-2026-10-01.md);
they were not falsely rerun or elevated to full-Cell proof in this wave.

## Work still required for the requested complete service

1. **Complete KFE drain/recovery.** Guard the remaining bootstrap/address/key/tax,
   inbound/confirmation/reorg/stream/ZMQ, embedded execution and publisher roots.
   Integrate durable provenance across remaining callbacks and prove actual
   completion/reconciliation without clearing uncertainty by TTL, labels or IDs.
   Replace coverage blockers only after complete inventory/race/restart evidence.
2. **Authoritative Bank compatibility.** Implement target build/runtime/provenance/
   migration/recovery checks behind Core's certificate-only observation producer
   and qualify the dedicated mandatory-mTLS deployment. Current real producer
   reports unknown/incompatible, never successful compatibility. Node's real
   signed transport is not the missing compatible decision.
3. **Independent Vault release verification.** Integrate independent source/build
   compatibility verification and threshold attestation with the apply path.
   Existing immutable Git histories and archive APIs are storage/verification
   boundaries, not authorization to execute a new release.
4. **Real release material and availability.** Build all Cell artifacts from
   pinned sources, verify reproducible OCI/SBOM/provenance, qualify independent
   signatures, and integrate published bytes with durable Vault/mirror fetching.
   The new offline publisher packages explicit files; it is not a release build
   pipeline, network distribution service or global Byzantine-safe storage ledger.
5. **Complete installation/update executors.** Implement Admin artifact installation,
   ordered migration execution with tested data recovery, and replica-by-replica
   Node/Vault rollout that preserves quorum. These are still explicit pre-mutation
   execution blockers in `infra/stack/lifecycle.py`; never remove them as flags.
6. **Real restore qualification.** Resolve the retained pilot binding failure,
   obtain sufficient authorized lab capacity, run the actual ten-probe CLI and
   qualify recovery/failure matrices. Synthetic bbolt buckets are not recovered
   LND state; Bitcoin/Tor/Vault layout checks are not financial/custody recovery.
7. **Whole-Cell acceptance.** Execute installation, release observation/operator
   notice, plan, drain, update, interruption, explicit recovery, restore and
   operator-approved resumption using actual Admin/Node/UI and all services.
   Retain bound evidence; never auto-activate Vault signers.

The next live lab run needs an explicit capacity decision. Expanding the named
40 GiB Docker loop image to 60 GiB was requested but not authorized. Space is
one external test constraint, **not** the only missing implementation above.
