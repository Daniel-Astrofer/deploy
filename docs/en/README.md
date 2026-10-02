# Kerosene Deploy documentation

This repository owns deployment and operations assets. It does not own product
business logic or service source code.

Start here:

- [Deployment index](DEPLOYMENT_INDEX.md): supported entrypoints and directory ownership.
- [Service boundaries](SERVICE_BOUNDARIES.md): ownership and consumed artifacts.
- [Repository boundary](../REPOSITORY_BOUNDARY.md): source and artifact boundaries.
- [Staging runbook](../ops/STAGING_DEPLOY.md): current staging procedure and gates.
- [SPIRE staging identity](SPIRE_STAGING.md): workload identity rollout and limits.
- [Complete Cell implementation status](../operations/cell-implementation-status-2026-10-02.md): tested mechanisms and remaining execution blockers.
- [Offline release publication](../operations/release-publication.md): TUF/package signatures and private publication-store recovery.

## Supported entrypoints

Local integrated runtime:

```bash
bash infra/start.sh
bash infra/status.sh
bash infra/logs.sh --follow
bash infra/stop.sh
```

Validation:

```bash
bash infra/test.sh
bash infra/start.sh --dry-run
bash infra/kubernetes/scripts/validate-staging-runtime.sh
bash infra/kubernetes/scripts/validate-staging-spire.sh
```

Production is private and fail-closed. Public manifests must not manufacture
secrets, activate signers, or bypass evidence gates.

Portuguese documentation: [`docs/pt-BR/README.md`](../pt-BR/README.md).
