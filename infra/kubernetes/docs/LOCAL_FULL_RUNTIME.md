# Kerosene Kubernetes local-full runtime

This document describes the developer-only Kubernetes runtime that boots Kerosene inside the local cluster with the renamed workloads:

- `server` for the backend service.
- `kfe-service` for the separated Krinse Financial Engine runtime.
- `web-page` for the web UI.
- `mpc-sidecar` for the Go crypto sidecar.

The implementation lives in:

```text
infra/kubernetes/overlays/local-full
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

It also provides local-only Kubernetes Secret objects for development, relaxed namespace-internal NetworkPolicy rules, a Tor-only public entrypoint, and an `emptyDir` replacement for the MPC shard PVC. This avoids the `StorageClass` blocker on a single-node workstation cluster while keeping host ports out of the Kubernetes service contract.

## Access

After deploy, public access is only through the Tor hidden service printed by
`infra/status.sh` or the deploy command. Kubernetes Services remain internal
`ClusterIP` objects, so local-full does not reserve or compete for fixed host
ports.

```text
onion port 80 -> web-page:8080
web-page     -> server:8080 for Core routes
web-page     -> kfe-service:8080 for KFE routes
server       -> mpc-sidecar:50051 and mpc-sidecar:8081 internally
```

Use `web-page` for financial routes. The Kubernetes web proxy sends `/kfe/**`,
`/api/public/kfe/**` and `/api/admin/kfe/**` to `Service/kfe-service`; the
`server` service is not published directly through Tor.

The `web-page` deployment mounts `web-page-runtime-config` at
`/usr/share/nginx/html/kerosene-runtime-config.json`, marking this runtime as
`tor-hidden-service-only`. The image import script rebuilds the web bundle for
Kubernetes same-origin routing, so a stale `WEB_API_URL` from another local
build does not leak into the Kubernetes frontend.

## Validate only

```bash
bash infra/kubernetes/scripts/validate-local-full.sh
bash infra/kubernetes/deploy.sh local-full --dry-run
```

## Deploy

Use the Kubernetes entrypoint to start the complete local runtime:

```bash
bash infra/start.sh
bash infra/deploy.sh
bash infra/kubernetes/deploy.sh
bash infra/kubernetes/deploy.sh local-full --wait
```

Calling the entrypoint with no arguments is equivalent to `local-full --wait`.
This command validates the overlay, builds/imports local application images into
the Kubernetes container runtime, applies `overlays/local-full`, waits for
workloads, and prints the local access URLs.
If your shell is already in `infra/`, `./deploy.sh` is the equivalent shortcut.

If containerd image import cannot run because `sudo ctr` is unavailable, the
entrypoint continues with images already present in the cluster. Use
`--strict-image-import` when you want missing image import to abort the deploy.
In an interactive terminal, the importer asks for `sudo` credentials before
building/importing images. After a successful import, the deploy records each
local workload image ID in the pod template annotation
`kerosene.io/local-image-id`; Kubernetes rolls out only workloads whose image ID
changed.

Advanced helper commands remain available for focused troubleshooting:

```bash
bash infra/kubernetes/scripts/import-local-docker-images.sh
bash infra/kubernetes/scripts/deploy-local-full.sh --skip-image-import --wait
bash infra/kubernetes/scripts/wait-local-full.sh
```

## Production boundary

Do not promote `local-full` to production.

`local-full` uses:

- development-only static credentials;
- Vault dev mode;
- `emptyDir` storage;
- relaxed namespace-internal network rules;
- Bitcoin testnet4;
- Real LND (`local-lnd`) on testnet4 with KFE REST enabled (see below).

Production must continue using the hardened `production` overlay with real Secrets, real storage, immutable image digests, real Vault, real Bitcoin/LND, and production mTLS.

## Local LND (real node)

`local-full` now runs a real LND node (`Deployment/local-lnd`) against the in-cluster Bitcoin Core **testnet4** backend:

- image: `lightninglabs/lnd:v0.20.1-beta`
- data: PVC `local-lnd-data` → hostPath `.local/lnd-data`
- wallet bootstrap sidecar unlocks/init with `kerosene-lnd-secrets.wallet-password`
- KFE REST: `LIGHTNING_LND_REST_ENABLED=true`, `LIGHTNING_LND_BASE_URL=https://kerosene-lnd-headless:8080`, `LIGHTNING_LND_TLS_INSECURE=true`
- after first wallet bootstrap, sync the hex macaroon into the secret:

```bash
./infra/kubernetes/scripts/sync-local-lnd-macaroon.sh
```

### Liquidity (pay / settle)

Paying and settling BOLT11 needs **on-chain funds on LND + at least one channel**. Local-full deploys a second node `local-lnd-peer` as counterparty (namespace-internal) so you do not depend on public testnet liquidity.

```bash
# After LND is running, fund from platform bitcoind wallet "kerosene" and open a channel:
bash infra/kubernetes/scripts/bootstrap-local-lightning-liquidity.sh

# Re-sync hex macaroon into KFE secret after wallet re-init:
bash infra/kubernetes/scripts/sync-local-lnd-macaroon.sh
```

What the bootstrap does:

1. Sends testnet4 BTC from `bitcoin-core` wallet `kerosene` → platform LND + peer LND  
2. Connects the two nodes on port 9735  
3. Opens a channel with `push_amt` so **both outbound and inbound** liquidity exist  
4. Runs a small payinvoice smoke both directions  

Channel activation waits for **real testnet4 confirmations** (`confirmations_until_active`, usually 1 with `bitcoin.defaultchanconfs=1`). Seed backups live under each LND datadir — local-only, not production custody.
