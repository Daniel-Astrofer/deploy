# Kerosene Kubernetes local-full runtime

This document describes the developer-only Kubernetes runtime that boots Kerosene inside the local cluster with the renamed workloads:

- `server` for the backend service.
- `kfe-service` for the separated Krinse Financial Engine runtime.
- `web-page` for the web UI.
- `mpc-sidecar` for the Go crypto sidecar.

The implementation lives in:

```text
backend/kerosene-infrastructure/k8s/overlays/local-full
```

## What local-full includes

`local-full` is intentionally separate from `local`, `staging`, and `production`. It adds local-only runtime dependencies so a workstation cluster can run the system without requiring production infrastructure first.

It deploys:

- `Deployment/server`
- `Deployment/kfe-service`
- `Deployment/web-page`
- `StatefulSet/mpc-sidecar`
- `StatefulSet/local-postgres`
- `Deployment/local-redis`
- `Deployment/local-vault`
- `Deployment/local-bitcoin`
- `Deployment/local-lnd-placeholder`

It also provides local-only Kubernetes Secret objects for development, relaxed namespace-internal NetworkPolicy rules, NodePort access, and an `emptyDir` replacement for the MPC shard PVC. This avoids the `StorageClass` blocker on a single-node workstation cluster.

## Access

After deploy:

```text
server:   http://127.0.0.1:30080
mpc:      http://127.0.0.1:30081/version
web-page: http://127.0.0.1:30082
```

Use `web-page` for financial routes. The Kubernetes web proxy sends `/kfe/**`,
`/api/public/kfe/**` and `/api/admin/kfe/**` to `Service/kfe-service`; the
direct `server` NodePort is Core-only.

## Validate only

```bash
bash backend/kerosene-infrastructure/k8s/scripts/validate-local-full.sh
bash backend/kerosene-infrastructure/k8s/scripts/deploy-local-full.sh --dry-run
```

## Deploy

Build/import local application images first, then apply the overlay:

```bash
bash backend/kerosene-infrastructure/k8s/scripts/import-local-docker-images.sh
bash backend/kerosene-infrastructure/k8s/scripts/deploy-local-full.sh --skip-image-import --wait
```

Or let the deploy wrapper import images:

```bash
bash backend/kerosene-infrastructure/k8s/scripts/deploy-local-full.sh --wait
```

## Production boundary

Do not promote `local-full` to production.

`local-full` uses:

- development-only static credentials;
- Vault dev mode;
- `emptyDir` storage;
- relaxed namespace-internal network rules;
- Bitcoin regtest;
- LND disabled in the server and KFE configs with a TCP placeholder for service-contract completeness.

Production must continue using the hardened `production` overlay with real Secrets, real storage, immutable image digests, real Vault, real Bitcoin/LND, and production mTLS.

## Why LND is a placeholder locally

The backend can boot and the Kubernetes Service contract is satisfied locally, but a fully initialized LND node requires wallet bootstrap, macaroon distribution, TLS material, channel state, and Bitcoin funding workflow. That belongs in a later dedicated `local-lnd` phase. Until then, local-full disables LND in `server-config` via:

```text
LIGHTNING_LND_ENABLED=false
LIGHTNING_LND_TLS_ENABLED=false
```

This keeps the local Kubernetes runtime useful for application, KFE, database, Redis, Vault-dev, Bitcoin-regtest, MPC, service discovery, and network-policy validation without pretending the workstation stack is production-ready Lightning infrastructure.
