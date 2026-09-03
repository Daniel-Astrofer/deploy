<!--
Kerosene documentation metadata
status: active
audience: internal
owner: deploy
source_of_truth: deploy
last_reviewed: 2026-09-03
-->

# Repository maintenance scripts

This directory contains static checks and polyrepo maintenance utilities:

| Script | Purpose |
|---|---|
| `check-polyrepo-workspace.sh` | Validate all independent checkout locations |
| `check_architecture_guardrails.sh` | Enforce service boundaries in manifests |
| `validate-runtime-boundaries.sh` | Reject embedded secrets and retired names |
| `clean-polyrepo-workspace.sh` | Report or remove only reproducible caches |
| `sync-polyrepo-workspace.sh` | Inspect or fast-forward clean tracked branches |
| `polyrepo-env.sh` | Resolve flat and grouped workspace layouts |

The cleanup defaults to report-only mode. Use `--apply` explicitly; runtime
state, worktree metadata, wallets, certificates and secrets are always excluded.
