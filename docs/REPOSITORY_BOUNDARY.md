# Repository boundary

This repository owns Compose, Kubernetes, observability and release
orchestration. It does not own Core, Clients or Vault source.

Local source builds resolve independent sibling repositories through:

- `KEROSENE_CORE_DIR`;
- `KEROSENE_CLIENTS_DIR`;
- `KEROSENE_VAULT_DIR`;
- `KEROSENE_NODE_DIR`;
- `KEROSENE_CONTRACTS_DIR`.

Production deployment should consume immutable images and artifacts. The
archived monorepo is never a build input.
