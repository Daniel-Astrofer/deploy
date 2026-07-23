# Kerosene Kubernetes local-full runtime

This document describes the developer-only Kubernetes runtime that boots Kerosene inside the local cluster with the renamed workloads:

- `server` for the backend service.
- `kfe-service` for the separated Krinse Financial Engine runtime (vault mesh settlement).
- `web-page` for the web UI.
- `vault-mesh-lab` (Docker Compose) for kerosene-vault treasury mesh on testnet3.

The implementation lives in:

```text
infra/kubernetes/overlays/local-full
infra/docker/compose/vault-mesh-lab.compose.yaml
```

## What local-full includes

`local-full` is intentionally separate from `local`, `staging`, and `production`. It adds local-only runtime dependencies so a workstation cluster can run the system without requiring production infrastructure first.

It deploys:

- `Deployment/server`
- `Deployment/kfe-service` with vaultmesh enabled (`mesh-only=true`, mpc signing off)
- `Deployment/web-page`
- Host compose `vault-mesh-lab` (vault-1/2/3) + Service/Endpoints bridge `vault-1`
- `StatefulSet/local-postgres`
- `Deployment/local-redis`
- `Deployment/local-bitcoin`
- `Deployment/local-lnd`
- `Deployment/local-lnd-peer`

**Removed from deploy:** `mpc-sidecar`, HashiCorp `local-vault` (wallet-arming).

## Access

After deploy, public access is only through the Tor hidden service printed by
`infra/status.sh` or the deploy command. Kubernetes Services remain internal
`ClusterIP` objects, so local-full does not reserve or compete for fixed host
ports.

```text
onion port 80 -> web-page:8080
web-page     -> server:8080 for Core routes
web-page     -> kfe-service:8080 for KFE routes
kfe-service  -> vault-1:7701 (vault mesh lab on host via Endpoints bridge)
```

## Validate only

```bash
bash infra/kubernetes/scripts/validate-local-full.sh
bash infra/kubernetes/deploy.sh local-full --dry-run
```

## Deploy

```bash
bash infra/start.sh
bash infra/deploy.sh
bash infra/kubernetes/deploy.sh
bash infra/kubernetes/deploy.sh local-full --wait
```

Default (no args) is `local-full --wait`. Deploy starts `vault-mesh-lab.compose.yaml`
(testnet3 + static_token) before applying the overlay.

For staging mTLS mesh instead of lab (only when certs exist):

```bash
KEROSENE_VAULT_MESH_PROFILE=staging bash infra/deploy.sh --wait
```

If certs are missing, deploy falls back to lab with a clear warning.

## Production boundary

Local-full is a workstation lab. Do not treat `dealer_lab` / `static_token` as
go-live. Production mesh uses mTLS staging/go-live properties — see
`kfe-service-vaultmesh-go-live.properties` and `vault-mesh-staging.compose.yaml`.
