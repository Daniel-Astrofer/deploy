# Complete Cell implementation checkpoint — 2026-10-03

**Not complete. Installation/update remains fail-closed.** Scope is the full
service: Admin/jctl/operator UI, Core, KFE, Node, Vault, web-page, PostgreSQL,
Redis, Bitcoin, LND and Tor. This checkpoint updates the KFE participant gaps in
the [October 2 checkpoint](cell-implementation-status-2026-10-02.md); unchanged
component evidence there is historical, not a newly rerun full-Cell test.

KFE work remains on isolated `feat/complete-cell-operations`, current commit
`e45e3f2` (continues `d866416`, `0c46542` and `074e08a`). Deploy remains on
`feat/staging-volumesnapshot-attestation-request`; this Deploy wave changes
documentation only. Dirty primary checkouts were untouched. No production update,
real custody/provider action, release authority provisioning or signer activation.

## Implemented and verified

Three disjoint workers implemented bounded leaves; the coordinator reviewed and
integrated them, owned all builds, and repaired the shared transaction guard.
All workers are closed. Static handoff alone is not verification evidence.

- Balance genesis, reservation/settlement/release, available/reorg/observed writes,
  defensive zeroing and locked mutable balance access now admit before effects.
- Derivation cursor issuance admits before locking/writing; original algorithm
  and REQUIRED propagation remain. Direct no-transaction calls stay uncertain.
- Fee credit/reversal/restoration and movement recording admit before existence
  checks, profit lookup and writes; original idempotency/financial ordering remain.
- Lightning reservation, consumption/release and enabled circuit-breaker
  evaluation admit before locks, probes or latch changes. Pure capacity observations
  and disabled-breaker noops stay available; reserve/probe outcomes stay uncertain.
- Audit hashing/appender lock/persistence/logging have independent admission.
  REQUIRED and forensic REQUIRES_NEW propagation are preserved, not replaced.
- State transitions/audit, idempotency reserve/complete, external outbox production,
  internal payment-request lock/markPaid now admit before managed state or effects.
  Pure lookups/hashing and existing noops retain their contracts.

Every new boundary has an unavailable constructor default and mandatory real
guard injection. Returned IDs/managed entities are not continuation credentials.
Financial algorithms, schema and transaction propagation were not rewritten.

### Reproduced and repaired nested-transaction bug

A real PostgreSQL regression demonstrated that a caught REQUIRES_NEW commit
failure could previously leave a successfully committing outer admission
COMPLETED, although its inner cursor write rolled back. Nested completion now
preserves failure/unknown uncertainty before root resolution. Nested transactions
without observable synchronization reject before effects. A root admitted without
an observed transaction cannot obtain completion proof merely by starting one later.
Joined committed local work still follows its original transaction contract.

The reproduced database case now leaves UNCERTAIN. This is maintenance bookkeeping
repair, not financial-policy replacement or permission to resume/update.

### Earlier participant-wave verification

The earlier `./gradlew --no-daemon check bootJar`: **1,503 tests; zero failures, errors
or skips**. All new source/test files were included. Local success is not a claim
that hosted CI ran or that the complete service was installed.

The four new bounded participant unit suites total 277 cases (63 balance/cursor,
53 fee/movement, 73 liquidity/audit, 88 transaction participants). The real
PostgreSQL suites ran 14 generic/publisher cases and 12 full-schema cases.
Full-schema verification uses the entire actual Flyway chain through V58,
Hibernate schema validation, exact balance/cursor/audit JPA entities/repositories,
real transactional proxies and JpaTransactionManager. New cases cover:

- Balance/cursor commit, rollback, drain rejection and deferred PostgreSQL commit
  rejection after service return, with actual stored buckets/hash/index assertions.
- Two already admitted existing-cursor writers finishing with distinct indices
  across drain while fresh issuance is rejected. Absent-cursor creation races are
  not thereby qualified.
- REQUIRED audit rollback and REQUIRES_NEW forensic survival across outer rollback.
  The real append-only trigger rejects deletion; synthetic forensic rows remain
  in the exclusive disposable database. No trigger bypass or Flyway clean.
- The caught inner commit-failure/outer commit regression and unchanged nonzero
  unknown coverage blockers.

Metadata/event/structured-log ports in these JPA fixtures are mocked. Real
delivery, uncommitted financial foreign-key scenarios, concurrent audit chaining,
liquidity advisory-lock/terminal concurrency and the complete submit context are
not inferred from these results. KFE STATUS and the indexed bounded runbooks
record those limits.

## Earlier upstream/provider continuation

Two disjoint agents added direct Vault and remote-adapter boundaries while the
coordinator protected settlement/quorum, integrated fixtures and added actual
PostgreSQL evidence. Both agents are closed; primary checkouts remain untouched.

- Settlement evaluation, require-pass and both audit entrypoints now admit before
  flags, balance locks, provider probes, consensus, audit and signals.
- Quorum gateway and direct Vault threshold/legacy consensus admit before
  transport, including the legacy constitution-context GET. Direct MPC key
  provisioning admits before provider resolution and deposit-key retrieval.
- Four typed approval and fourteen reachable notification effect roots admit
  before POST. Best-effort notification catches cannot swallow admission refusal.
  Pure validation/unsupported legacy inputs remain local; payloads are unchanged.
- Every remote/gate outcome remains uncertain, including accepted replies and
  local financial commit. Already admitted synchronous work can finish across
  drain, but fresh calls are rejected. No reconciliation/force-clear API is added.

That wave's final `check bootJar`: **1,776 tests, zero failures/errors/skips**, including
all current source/tests. New suites contain 47 settlement, 20 Vault provider and
204 remote effect cases, plus two new full-schema PostgreSQL cases. The actual
PostgreSQL suites now total 28 cases (14 generic/publisher and 14 full-schema).
Quorum persistence tests prove uncertainty after actual commit/store recreation
and actual drain refusal before fresh provider invocation. Their financial port
is mocked; adapter accepted-proof fixtures also mock the cryptographic verifier.
No real BFT/provider, release attestation or complete-Cell acceptance is claimed.

Tests exposed a pre-existing conflict-notification defect: the client constructs
confirmations=-1, rejected by the shared request contract before transport or
admission. Four cases assert the exact local failure; they do not count as
delivery evidence. The payload/contract mismatch remains separately unqualified,
alongside the previously recorded conflict/refund concern. Successful test
classification is not a financial-policy or notification-delivery repair.

## Current admission diagnostic continuation

KFE now provides exact authenticated ADMIN GET/HEAD
`/api/admin/kfe/maintenance/admissions`, available during drain without new
admission or resolution. It returns unresolved metadata and parent provenance,
with read-only REPEATABLE_READ snapshots, timestamp/UUID keyset pagination,
default 50/max 100 entries, canonical bounded position cursors and no-store
responses. Errors are fixed 400/503 without SQL/connection details. IDs and
cursors are not completion credentials; diagnosticOnly remains true.

The independent query never locks maintenance control for writing or modifies
financial/admission state. No schema migration, proof inference, force-clear,
resume or replay runner is introduced. Output is bounded and transaction timeout
is five seconds; large-backlog index/sort performance is not load-qualified.
Snapshots are coherent per page, not across requests; empty pages do not prove
that all pending effects were inspected or resolved.

Current pinned Gradle `check bootJar`: **1,828 tests, zero failures/errors/skips**,
including 25 query, 14 controller/filter+MVC and ten extra perimeter cases. Three
additional real full-schema PostgreSQL cases prove timestamp-tie pagination and
parent lineage, no admission/clearance during actual drain and one MVCC snapshot
under interleaved independently committed resolution. The real PostgreSQL suites
now total 31 cases: 14 generic/publisher and 17 full-schema. This is diagnostics,
not operator UI/jctl integration, provider finality or complete-service recovery.

One bounded agent implemented the controller; another performed a read-only
consumer audit. Both are closed, coordinator owns integration/verification.
The audit found no conflict-notification route or adapter override in inspected
isolated Core sources. The producer's -1 DTO defect is therefore not the only
delivery gap. Changing it to 0 alone would not provide an authenticated receiver,
event semantics or proof of refund; no financial policy was rewritten.

## Remaining work for the complete service

1. **Complete KFE drain/recovery:** complete remaining provider/embedded inventory
   and alternate host security chains; the bounded settlement/quorum and direct
   MPC/approval/notification starts above are now guarded, not remotely finalized.
   Qualify real reconnect/durable cursors/hints/source
   reconciliation; and audited completion/reconciliation of uncertain admissions.
   Protected leaves do not certify all callers. Preserve the three unknown counts
   until complete inventory/race/restart evidence. Independently qualify the
   previously documented conflict/refund concern; no financial-policy fix claimed.
   Resolve the separately documented outbound-conflicted producer/consumer
   mismatch. Admission diagnostics now help inspection, but cannot substitute
   for exact financial correlation and authenticated completion/recovery proofs.
2. **Authoritative Bank compatibility:** implement target build/runtime/provenance,
   migration/recovery decisions and mandatory-mTLS deployment qualification. The
   current real producer's unknown/incompatible result and signed Node transport
   are not successful compatibility proof.
3. **Independent Vault release verification:** independent source/build checks and
   threshold attestation integrated with apply. Git/archive storage is not release
   execution authority.
4. **Release material and availability:** all pinned-source reproducible OCI/SBOM/
   provenance, independently authorized signatures and durable Vault/mirror fetching
   with Byzantine-safe availability. The prior offline publisher is not a build or
   distribution/reconciliation service.
5. **Installation/update execution:** Admin artifact installation, ordered migrations
   with tested data recovery and replica-by-replica Node/Vault rollout preserving
   quorum. All four explicit `infra/stack/lifecycle.py` execution blockers remain
   unchanged; do not remove them as flags.
6. **Real restore qualification:** resolve retained WFFC pilot binding failure,
   obtain authorized lab capacity, run the actual ten-volume restore CLI and
   qualify failure/recovery matrices including genuine LND state. No live final
   qualification or qualification.json exists.
7. **Whole-Cell acceptance:** actual Admin/Node/UI and all services through install,
   release notice, plan, drain, update, interruption, recovery, restore and explicit
   operator-approved resume, retaining bound evidence and never auto-activating
   Vault signers.

KFE `mutationCoverageUnknown`, `callbackCoverageUnknown` and
`readSideEffectsUnknown` remain 1; safeToUpdate remains false. HTTP/remote and many
participant outcomes intentionally accumulate UNCERTAIN with no force-clear or
automatic replay/resume. This is not yet an operationally complete safe updater.

The read-only Docker capacity check in this wave reports 0 MiB available and 100%
use on the named 40 GiB loop filesystem, below the required 256 MiB lab margin.
No resize, prune, data deletion, cluster recreation or new live CSI qualification
was attempted. Capacity is an external test constraint, not the only remaining
implementation requirement. The dedicated PostgreSQL fixtures use the separate
workspace-backed disposable lab; they are not production recovery evidence.
