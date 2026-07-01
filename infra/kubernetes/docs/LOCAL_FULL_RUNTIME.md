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

The `web-page` deployment mounts `web-page-runtime-config` at
`/usr/share/nginx/html/kerosene-runtime-config.json`, pointing the Flutter web
runtime at `http://127.0.0.1:30082`. The image import script rebuilds the web
bundle for Kubernetes same-origin routing, so a stale `WEB_API_URL` from
another local build does not leak into the Kubernetes frontend.

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
