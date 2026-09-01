# Deployment index

## Responsibility

`kerosene-deploy` consumes independently released service images and defines
how they are composed, configured, validated, observed and promoted. Local
packaging may use external source checkouts. Service code, financial rules,
custody implementation and client code belong to their own repositories.

Detailed ownership: [Service boundaries](SERVICE_BOUNDARIES.md).

## Public commands

| Command | Scope | Status |
|---|---|---|
| `bash infra/start.sh` | Start integrated `local-full` | Canonical local entrypoint |
| `bash infra/stop.sh` | Stop local workloads, preserve data | Canonical local entrypoint |
| `bash infra/recreate.sh` | Recreate local runtime | Canonical local entrypoint |
| `bash infra/status.sh` | Show local resources and endpoints | Canonical local entrypoint |
| `bash infra/logs.sh` | Collect/show local logs | Canonical local entrypoint |
| `bash infra/test.sh` | Validate scripts and manifests | Canonical validation entrypoint |
| `bash infra/kubernetes/scripts/deploy.sh <environment>` | Environment executor | Internal/CI; use explicitly |

## Compatibility entrypoints

The following files remain executable to avoid breaking existing automation,
but must not be used by new workflows:

- `infra/start-complete.sh`
- `infra/deploy.sh`
- `infra/kubernetes/deploy.sh`
- `infra/kubernetes/scripts/apply.sh`
- `infra/kubernetes/scripts/deploy-local.sh`
- `infra/kubernetes/scripts/deploy-staging.sh`
- `infra/kubernetes/scripts/start-local.sh`
- `infra/kubernetes/scripts/stop-local.sh`
- `infra/kubernetes/scripts/status-local.sh`
- `infra/kubernetes/scripts/logs-local.sh`
- `infra/kubernetes/import-local-images.sh`
- `infra/kubernetes/validate-local-ha.sh`
- `infra/kubernetes/scripts/wait.sh`
- `infra/kubernetes/scripts/logs-vault.sh`
- `infra/scripts/local/{start,stop,recreate,restart,state}.sh`
- `infra/docker/scripts/compose-local.sh`
- `infra/docker/scripts/compose-local-kfe.sh`

No compatibility wrapper is removed in the first cleanup wave.

## Docker

| Path | Responsibility |
|---|---|
| `infra/docker/images.yaml` | Local integration image inventory |
| `infra/docker/images/` | Transitional packaging recipes; services own release images |
| `infra/docker/compose/vault-mesh-lab.compose.yaml` | Local-only clear-token Vault Mesh lab |
| `infra/docker/compose/vault-mesh-staging.compose.yaml` | Local staging-like mTLS exercise |
| `infra/docker/compose/vault-mesh-tor.compose.yaml` | Tor mesh exercise |
| `infra/docker/compose/vault-mesh-ceremony.compose.yaml` | Ceremony-specific composition |
| `infra/docker/compose/local.limits.compose.yaml` | Legacy override for the removed multi-service Compose stack; not standalone |
| `infra/docker/scripts/` | Compose/build helpers |

Compose files are development/integration assets. Production deployment uses
private Kubernetes overlays and immutable image digests.

`local.limits.compose.yaml` still names services from the removed local Compose
stack and does not validate alone or with the active Vault-only Compose files.
It remains only because legacy `infra/scripts/local/` still references it.

## Kubernetes

| Path | Responsibility |
|---|---|
| `infra/kubernetes/base/` | Shared Kubernetes resources |
| `infra/kubernetes/components/` | Reusable Kustomize components |
| `infra/kubernetes/components/spire/` | Staging-only workload identity control plane |
| `infra/kubernetes/overlays/local/` | Lightweight workstation overlay |
| `infra/kubernetes/overlays/local-full/` | Canonical integrated local runtime |
| `infra/kubernetes/overlays/local-ha/` | Local HA test topology |
| `infra/kubernetes/overlays/staging/` | Public staging core runtime |
| `infra/kubernetes/overlays/staging-vault/` | Independent staging Vault runtime |
| `infra/kubernetes/overlays/staging-spiffe/` | Core staging with additive Workload API mount |
| `infra/kubernetes/overlays/staging-vault-spiffe/` | Independent Vault staging with additive Workload API mount |
| `infra/kubernetes/scripts/` | Deploy, validation, smoke and operations helpers |
| `infra/kubernetes/tests/` | Shell regression tests for deploy behavior |

`infra/kubernetes/scripts/deploy-local-ha.sh` is a specialized environment
executor, not a compatibility wrapper. It is not the default local entrypoint.

Production overlays are intentionally not stored in this public tree.

## Script classes

| Path | Class |
|---|---|
| `infra/*.sh` | Public local entrypoints plus compatibility wrappers |
| `infra/scripts/quorum.sh` | Internal dispatcher for public local commands |
| `infra/scripts/common.sh`, `polyrepo-env.sh` | Shared libraries; source only |
| `infra/scripts/local/` | Legacy Compose operations; supported only for compatibility |
| `infra/scripts/beta/` | Experimental smoke tests; not production gates |
| `infra/kubernetes/scripts/deploy-local-ha.sh` | Specialized `local-ha` executor |
| `infra/kubernetes/scripts/validate-*.sh` | Read-only validation helpers |
| `infra/kubernetes/scripts/smoke-*.sh` | Post-deploy smoke checks |
| `infra/production/` | Fail-closed production evidence and validation gates |
| `infra/runtime/` | Container runtime entrypoints/configuration |

## Known legacy behavior

`KEROSENE_VAULT_MESH_PROFILE=staging` in the local helper currently attempts
certificate generation and falls back to the clear-token lab mesh if that
generation fails. This behavior is legacy, local-only and must never count as
staging or production readiness. It is preserved in this wave to avoid changing
runtime behavior without dedicated migration evidence.
