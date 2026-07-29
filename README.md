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
