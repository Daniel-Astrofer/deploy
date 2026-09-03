<!--
Kerosene documentation metadata
status: active
audience: internal
owner: deploy
source_of_truth: deploy
last_reviewed: 2026-09-03
-->

# Deployment infrastructure

`infra/` contains the public, non-secret portion of Kerosene production
operations on Bitcoin `testnet3`.

| Path | Responsibility |
|---|---|
| `production/` | Fail-closed preflight, evidence and manifest validation |
| `kubernetes/base/` | Reusable public Kubernetes resources for a private overlay |
| `kubernetes/scripts/` | Restricted diagnostics, rollback and packaging helpers |
| `docker/images/` | Deploy-owned container packaging recipes |
| `runtime/` | Non-secret runtime configuration and observability assets |
| `scripts/` | Polyrepo, architecture and workspace-maintenance checks |
| `mcp/` | Project inspection tools; the read-only launcher rejects mutations |

The only rollout entrypoint is:

```bash
bash infra/production/preflight.sh --start
```

Local, lab and staging orchestrators were removed. Runtime credentials, private
overlays, signer state and live data remain outside version control.
