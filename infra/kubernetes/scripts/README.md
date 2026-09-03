<!--
Kerosene documentation metadata
status: active
audience: restricted
owner: deploy
source_of_truth: deploy
last_reviewed: 2026-09-03
-->

# Kubernetes operational helpers

These scripts are explicit operator tools, not rollout entrypoints:

| Script | Purpose |
|---|---|
| `check-cluster-prereqs.sh` | Read-only cluster capability inspection |
| `collect-diagnostics.sh` | Redacted production diagnostics bundle |
| `debug-pod.sh` | Targeted logs, describe, debug or port-forward |
| `cleanup-stale-pods.sh` | Dry-run-first cleanup of stale failed Pods |
| `reset-instance.sh` | Explicit component restart/recreation |
| `rollback.sh` | Kubernetes Deployment revision rollback |
| `verify-kfe-only.sh` | Cross-repository ownership guardrail |
| `build-web-admin-backend.sh` | Controlled frontend/backend packaging helper |

Commands that mutate a cluster require explicit arguments or an apply mode.
Production rollout itself is allowed only through `infra/production/preflight.sh`.
