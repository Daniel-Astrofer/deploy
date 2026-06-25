# Kerosene Kubernetes workload rename and cohesion

The Kubernetes workload names were normalized to make the platform topology clearer:

- backend API workload: `server`
- frontend workload: `web-page`
- cryptographic sidecar workload: `mpc-sidecar`

This affects Deployments, Services, ServiceAccounts, HPA, PDBs, NetworkPolicies, image names, overlay patches and operational scripts under `backend/kerosene-infrastructure/k8s`.

## Rendered topology

Each overlay renders the same workload contract:

- `Deployment/server`
- `Deployment/web-page`
- `StatefulSet/mpc-sidecar`
- `Service/server`
- `Service/web-page`
- `Service/mpc-sidecar`
- `HorizontalPodAutoscaler/server`
- `PodDisruptionBudget/server`
- `PodDisruptionBudget/web-page`
- `PodDisruptionBudget/mpc-sidecar`

## Images

The Kustomize image contracts are now:

```bash
SERVER_IMAGE='registry.example.com/kerosene/server@sha256:REPLACE'
MPC_SIDECAR_IMAGE='registry.example.com/kerosene/mpc-sidecar@sha256:REPLACE'
WEB_PAGE_IMAGE='registry.example.com/kerosene/web-page@sha256:REPLACE'
```

Production still refuses `replace-me` tags.

## MPC TLS correction

The MPC container now uses the environment variable names expected by the Go sidecar:

- `MPC_TLS_CERT_FILE=/tls/tls.crt`
- `MPC_TLS_KEY_FILE=/tls/tls.key`
- `MPC_TLS_CA_FILE=/tls/ca.crt`

The local overlay keeps insecure gRPC enabled for development only.

## Validation

Run:

```bash
backend/kerosene-infrastructure/k8s/scripts/validate-k8s-cohesion.sh
backend/kerosene-infrastructure/k8s/scripts/deploy-local.sh --dry-run
```

Before a real local deploy, also check cluster prerequisites:

```bash
backend/kerosene-infrastructure/k8s/scripts/check-cluster-prereqs.sh
```

## Compatibility note

The local Docker Compose stack may still expose old service names while the Kubernetes image target is `kerosene/server:local`. The import script intentionally tags the Compose-built backend image into the new Kubernetes image name instead of forcing a Compose rename in the same migration.
