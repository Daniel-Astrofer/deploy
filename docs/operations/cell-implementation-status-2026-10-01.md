# Complete Cell implementation checkpoint — 2026-10-01

Historical checkpoint. See the [October 2 checkpoint](cell-implementation-status-2026-10-02.md)
for restored full KFE compilation, integrated admission batches, real offline
publication and current CSI lab results. Statements below describe October 1 only.

**Not complete and not qualified for live installation/update.** This checkpoint
distinguishes implemented/tested interfaces from remaining runtime mechanisms.
Scope remains the entire Cell: Admin/jctl, Core, KFE, Node, Vault, web-page,
PostgreSQL, Redis, Bitcoin, LND and Tor; it is not a six-container-only project.

All service work uses isolated `feat/complete-cell-operations` branches. Deploy
continues its isolated `feat/staging-volumesnapshot-attestation-request` branch.
Dirty primary checkouts have not been staged/reset. No signer has been activated
and no real Cell or production cluster has been updated.

## Recorded implementation

| Repository | Commit | Implemented boundary |
| --- | --- | --- |
| Contracts | `d1de045` | 0.3.0 typed observation/approval contracts, generated schemas and signature vectors; published branch provides Node's immutable dependency |
| Node | `51b9717`, `23475d3` | Authenticated Bank reads, separate Bank/Node signature verification, durable anti-replay, negative observations visible but not authorizing; actual Core wire qualification probe |
| Core | `6e0a066`, `a21bfc1` | Authenticated/audited operator API and normative Node consumer; certificate-only signed Bank runtime producer, explicit missing-evidence blockers, bounded KFE credential reader, private durable plan intent |
| Admin | `e1f57b5` | jctl Cell reads, bounded local package integrity verification, target-bound plan requests; no shell deploy or database access |
| Clients | `113e94f` | Admin dashboard evidence/votes/blockers/history and intent; rejects unknown schema, invented/duplicate vote counts and interrupted readiness |
| Vault | `3a8a5c1`, `f8b3fc4` | Independently approved immutable Git histories per release/repository, real pinned Git plumbing, authenticated raw archive API |
| Shared | `125b984` | Restored existing derivation interface required by the isolated baseline; no financial derivation algorithm changes |
| Deploy | `4371a80`, `55a48b3`, `6908f55`, `b907d02` | Safe archives/candidate packaging, retained snapshot helpers, real ordered governance verifier, cluster-bound lifecycle, pre-mutation execution qualification gates |

KFE maintenance/admission changes and its focused verification project remain
in the isolated worktree. They are not claimed to compile as a complete KFE:
the full baseline still lacks pricing/quorum source dependencies. The focused
maintenance implementation does not invent those financial classes.

## Verification actually run

- Contracts: 55 Rust tests passed and generated schemas matched committed files.
- Node: 16 scoped tests passed, including real mTLS with separate signer keys,
  restart/concurrent replay, negative reads and private authority-state checks.
  Observer-only strict Clippy passed. Broader strict Clippy remains blocked by
  22 preexisting ledger warnings; no finance code was changed to mask them.
- Core: complete `:auth-service:compileJava` and `:auth-service:test` passed;
  425 tests, no failures or skips. Architecture and endpoint-policy guardrails
  also passed after moving protocol parser construction into configuration and
  declaring the new route `CERTIFICATE_ONLY`, not public/JWT authorized.
  Focused tests include real mandatory-mTLS Bank responses, no-certificate TLS
  rejection, trusted-but-unpinned denial, current-runtime signed mismatch,
  caller-status rejection, earliest independent evidence expiry, canonical
  distinct signing identities and bounded/rotating private KFE credentials.
- Node/Core wire: the separately selected integration task passed one test,
  no skips. A real disposable Tomcat Bank producer was queried over mTLS by
  Node's actual Rust `BankTransport`/`ReleaseObserver`; it verified the separate
  Bank signature and challenge/target binding, persisted an unknown observation,
  and refused aggregate signing. This is not successful Cell compatibility.
- Admin: 64 tests passed with no skips. Its package verifier checks a local
  signature and bytes, not TUF/BFT/provenance; output explicitly states
  `releaseAuthorized:false`. Signed package publication remains separate work.
- Clients: seven scoped Flutter tests passed; affected-surface analysis reported
  no issues. These are widget/transport tests, not a live browser-to-Cell run.
- Vault: ten focused Git-archive tests passed, including a real two-commit Git
  bundle, prerequisite/truncation rejection, ten repositories, persistence,
  authenticated API and corruption before 304 handling. Broader API/domain
  suites passed 17 and 50 tests respectively. The inherited Admin Unix-day test
  vector was corrected independently; timestamp behavior was not weakened.
- Deploy: 18 archive tests, 14 lifecycle tests, release-lock and signed-evidence
  suites and architecture guardrails passed. Snapshot workflow contract tests
  explicitly use fake kubectl/synthetic receipts, not real CSI recovery.
- Governance: Go race tests passed. Four real isolated CometBFT processes proved
  progress with one validator absent, no commit with two absent, restored
  progress after quorum recovery, and rejection of altered/minority/detached
  authorization proofs. Public lab evidence is retained in
  `/tmp/kerosene-governance-evidence.yYIf81` for this host session.
- KFE: 26 focused maintenance tests passed with no skips, seven against a real
  dedicated disposable PostgreSQL database. This is not full finance execution
  or complete business migration qualification.

## Remaining work before acceptance, in dependency order

1. Restore/reconcile the isolated KFE's real missing pricing/quorum dependencies
   with project source, then pass its complete build/tests without fake classes.
2. Complete maintenance coverage of all financial mutation starts, callbacks,
   side-effecting reads, queues and remote uncertainty. Remove conservative
   coverage blockers only after drain/admission/restart race tests prove safety.
3. Complete authoritative target compatibility checks behind Core's normative
   `/v1/releases/observation`. Its real signed mTLS producer now exists, but
   deliberately returns only `unknown` or verified runtime `incompatible`, never
   `compatible`. Its local digest-checked catalog identifies targets without
   authorizing them. Qualify a dedicated Bank listener/deployment where browser
   callers cannot satisfy mandatory client-certificate authentication; do not
   disable mTLS or substitute forwarded certificate headers.
4. Integrate Vault independent compatibility/rebuild and its threshold attestation
   into the target/apply path. Git archival receipts do not authorize releases.
5. Implement reproducible source-to-OCI builds, SBOM/provenance validation,
   authorized signed TUF publication and the common package descriptor consumed
   by jctl. The existing candidate workflow deliberately publishes no authority.
6. Implement Admin artifact installation, ordered migration execution, tested
   data recovery and Node/Vault replica-by-replica quorum-preserving rollout.
   These missing capabilities cannot be supplied as flags or signed assertions.
7. Finish real CSI/CNI/admission backup and restore qualification. The exact
   disposable Kind lab now has a running kube-proxy after cluster-local flag
   correction; Calico/CSI/restore acceptance has not been completed. The untracked
   `staging-snapshot-kind-test.sh` experiment is incomplete, not a passing test.
8. Run the entire actual Cell through install, observe update, plan, drain,
   update, process interruption, explicit recovery, restore, readiness and
   operator-approved resumption; verify the actual Admin/Node surfaces and
   retained evidence without any Vault signer auto-activation.

No item above is discharged by a healthy pod, compatible-looking operator
payload, detached signature set, unsigned candidate or locally edited journal.
See [lifecycle safety boundary](kerosene-stack-lifecycle.md),
[ordered governance](../architecture/release-governance-v3.md) and
[archive boundary](release-archive.md) for the implemented mechanisms.
