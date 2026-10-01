# Cell lifecycle: implemented interfaces and current safety boundary

The Cell comprises Admin/jctl, Core, KFE, Node, Vault, web-page, PostgreSQL,
Redis, Bitcoin, LND and Tor. A complete release records all eleven immutable
component images/configurations and ten source repositories. This controller
does not yet provide a qualified one-command live installer for the full Cell.
Do not describe a successful plan, dry-run, readiness probe or archive import
as a successful live installation.

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
infra/kerosene-stack status --cell-dir /protected/cell-a
```

Initialization refuses an existing directory and starts no service. Public
trust files and configuration use private local permissions. Preflight reports
infrastructure reachability separately from `financialReadinessVerified` and
`applyQualified`. An omitted Kubernetes binding prevents approved-manifest
execution. The saved explicit context is bound to the `kube-system` namespace
UID; commands do not use the currently selected kubectl context. A replaced
cluster requires independent bootstrap, not a journal reset.

`import-artifact` only integrity-checks and caches/extracts inert bytes. See
[release archive](release-archive.md) for offline import, size limits, mirror
policy and packaging. The unsigned candidate workflow does not publish an
authorized release or executable installation script.

`update`, `install` and `recover` share the evidence pipeline. `update` without
`--apply` produces a plan. `--cell-dir` supplies trust/state references and
`--bundle-dir` supplies inert evidence file locations; missing evidence never
becomes a bypass. Use `--help` for the exact required flags. Legacy v1/v2
signature receipts remain usable for inspection/dry-run, not real application.

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

Authorization requires full offline TUF metadata, ordered v3 consensus proof,
fresh independently signed Bank aggregate, independently approved snapshot
request and signed tested restore receipt. TUF state preserves delegated-role
high-water marks and rejects same-version signed-metadata equivocation.

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

## Remaining live execution qualification

Real `--apply` is explicitly blocked before mutation until all of these
implementation capabilities exist and their integration tests pass:

1. Independent Vault compatibility/rebuild gate integrated into apply.
2. Migration execution and actual tested recovery integrated into the phases.
3. Node/Vault replica-by-replica rollout preserving real quorum and identities.
4. Installation/upgrade of the approved Admin artifact, not merely its digest.

Also required for final Cell acceptance: authoritative Core Bank read producer,
complete KFE drain coverage, source-to-OCI provenance and signed publication,
real CSI/CNI/admission restore qualification, and a complete install/update/
interruption/recovery run against all actual services. These are distinct from
the already passing contract, archive, UI and governance tests. The snapshot
Kind experiment is deliberately disposable and currently incomplete; it does
not provide production backup evidence. No Vault signer is automatically
activated by this controller.
