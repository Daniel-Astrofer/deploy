# Kerosene Kubernetes local-full runtime

This document describes the developer-only Kubernetes runtime that boots Kerosene inside the local cluster with the renamed workloads:

- `server` for the backend service.
- `kfe-service` for the separated Kerosene Financial Engine runtime (vault mesh settlement).
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
web-page     -> server:8080 for Auth and public KFE gateway routes
server       -> kfe-service:8080 with the local compatibility credential
kfe-service  -> vault-1:7701 (vault mesh lab on host via Endpoints bridge)
```

The browser-facing Nginx never connects directly to KFE. This keeps Auth as the
single public policy boundary. The local runtime uses the explicit compatibility
credential; the `staging-spiffe` profile replaces that hop with SPIFFE mTLS.

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
(testnet3 + static_token + `dealer_lab`) before applying the overlay.

Optional mesh profiles (`KEROSENE_VAULT_MESH_PROFILE`):

```bash
# LEGACY compatibility behavior: requests staging mTLS, but currently falls
# back to the insecure lab profile when certificate generation fails.
# Do not use this path as evidence of staging or production readiness.
KEROSENE_VAULT_MESH_PROFILE=staging bash infra/deploy.sh --wait

# Real Tor private mesh + distributed_wire (no host :7701–7703; not for kfe bridge)
KEROSENE_VAULT_MESH_PROFILE=tor bash infra/deploy.sh --wait
```

Tor profile starts `vault-mesh-tor.compose.yaml` via `ensure-vault-mesh-lab.sh`.
Lab remains the default for local-full KFE visualization. See
`docs/CEREMONY_TOR.md` in the independent `kerosene-vault` repository.

## Production boundary

Local-full is a workstation lab. Do not treat `dealer_lab` / `static_token` as
go-live. Production mesh uses mTLS staging/go-live properties — see
`kfe-service-vaultmesh-go-live.properties` and `vault-mesh-staging.compose.yaml`.
