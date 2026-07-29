# Canonical staging runtime plan

Tracking: [kerosene-deploy#1](https://github.com/Daniel-Astrofer/kerosene-deploy/issues/1)

## Outcome

`kerosene-staging` owns every dependency required by the production-trial
runtime. No Service, Secret, PVC, endpoint or operational command may target
`kerosene-local`.

## Delivery

- [x] Add staging-owned PostgreSQL, Redis, Bitcoin testnet3 and LND workloads.
- [x] Add a staging-owned Tor onion gateway with retained key storage.
- [x] Require immutable application images and all secret keys before apply.
- [x] Wait for dependency and application rollouts in dependency order.
- [x] Run authentication and three-vault FROST-readiness smoke tests.
- [x] Reject rendered manifests that reference another Kerosene namespace.
- [x] Validate client/server dry-runs and the repository infrastructure suite.

## Rollback

1. Keep the previous application image digests recorded in the release change.
2. Re-run `deploy.sh staging` with those digests; StatefulSet PVCs are retained.
3. If a dependency rollout is unhealthy, stop before application rollout and
   restore its PVC from the last verified snapshot.
4. Do not delete the namespace, PVCs, Tor key PVC or Vault data PVCs during
   rollback.
5. Re-run the staging preflight and smoke scripts before reopening traffic.

## Recovery

- PostgreSQL and Bitcoin data restore into replacement PVCs from independently
  verified snapshots.
- Redis stores ephemeral coordination state and may be recreated after the
  application is stopped.
- LND recovery requires the operator-controlled seed and channel backup; neither
  is stored in this repository.
- Tor onion identity is recovered from the retained `staging-tor-onion-keys`
  PVC snapshot.
- Vault recovery follows the Vault repository ceremony runbook; Deploy never
  creates, imports or activates signer shares.
