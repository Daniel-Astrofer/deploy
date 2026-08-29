# Repository boundary

This repository owns runtime composition, deployment policy and operational
validation. It does not own service behavior, APIs, financial rules,
cryptographic protocols or application source.

## Inputs and outputs

The normal release input is an immutable service image. Local development may
build an image from an independent checkout; the Dockerfile remains a packaging
recipe and does not transfer ownership of that source to Deploy.

| Input | Owner | Deploy responsibility |
|---|---|---|
| Auth image | `kerosene-core` | Configure and run the public gateway workload |
| KFE image | `kerosene-kfe` | Configure and run the financial workload |
| Shared Java artifact | `kerosene-shared` | Pin the version used by Auth and KFE builds |
| Client image | `kerosene-clients` | Serve and connect the client |
| Vault image | `kerosene-vault` | Configure and run custody workloads |
| Node image | `kerosene-node` | Configure and run discovery workloads |
| Contract versions | `kerosene-contracts` | Pin compatible released versions |
| Admin CLI image | `kerosene-admin` | Package the operator client without owning its source |
| Rail adapter images | `kerosene-rails` | Configure and run adapters after image contracts are defined |
| Tor/runtime helper images | `kerosene-deploy` | Build infrastructure-only helpers |

Deploy outputs rendered manifests, runtime configuration, validation evidence
and rollout instructions. It must not copy source modules from another
repository.

## Local checkout resolution

Local source builds resolve independent repositories through:

- `KEROSENE_CORE_DIR`;
- `KEROSENE_CLIENTS_DIR`;
- `KEROSENE_VAULT_DIR`;
- `KEROSENE_NODE_DIR`;
- `KEROSENE_CONTRACTS_DIR`;
- `KEROSENE_ADMIN_DIR`;
- `KEROSENE_RAILS_DIR`;
- `KEROSENE_KFE_DIR`;
- `KEROSENE_SHARED_DIR`.

The default checkout layout is a flat set of sibling repositories. Alternative
layouts are supported only through explicit directory variables. Production
deployment consumes immutable images and artifacts and never reads a source
workspace.

## Explicit exclusions

This repository must not implement mTLS semantics, CometBFT consensus, Vault
signing, Node membership, Auth policy or KFE financial behavior. It may declare
the runtime configuration those owners expose, but the corresponding protocol
and validation logic remain with the service repository.
