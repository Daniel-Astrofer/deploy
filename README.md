# Kerosene Deploy

Private deployment templates, orchestration and operational runbooks for
Kerosene.

This repository contains generic Docker/Kubernetes/Tor/observability
configuration extracted from the monorepo. It must not contain runtime secrets,
shares, seed phrases, macaroons, private Onion keys, TPM keys, user data or real
logs. Private visibility is an additional control, not a secret manager.

## Polyrepo workspace

Deploy never reads the archived monorepo. Source builds resolve independent
repositories with `KEROSENE_CORE_DIR`, `KEROSENE_CLIENTS_DIR`,
`KEROSENE_VAULT_DIR`, `KEROSENE_NODE_DIR` and `KEROSENE_CONTRACTS_DIR`.

The resolver supports both flat sibling clones and the canonical
`workspaces/kerosene/{services,platform}` layout. Validate a local checkout with:

```bash
bash scripts/check-polyrepo-workspace.sh
```

Start the complete local integration environment with:

```bash
bash infra/start-complete.sh local
```

The closest production-like topology deploys Core and the first Vault into
independent staging namespaces and requires immutable image digests:

```bash
SERVER_IMAGE=registry.example/server@sha256:... \
KFE_SERVICE_IMAGE=registry.example/kfe@sha256:... \
WEB_PAGE_IMAGE=registry.example/web@sha256:... \
VAULT_IMAGE=registry.example/vault@sha256:... \
NODE_IMAGE=registry.example/node@sha256:... \
TOR_IMAGE=registry.example/tor@sha256:... \
  bash infra/start-complete.sh staging
```

`infra/start-complete.sh production` is fail-closed. It requires a private
operations overlay, immutable images, external audit/recovery evidence and an
approved change identifier. This public repository never manufactures secrets
or activates Vault signers.
