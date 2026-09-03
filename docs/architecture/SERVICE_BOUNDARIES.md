<!--
Kerosene documentation metadata
status: review-required
audience: internal
owner: deploy
source_of_truth: deploy
last_reviewed: 2026-09-03
-->

# Service boundaries

`kerosene-deploy` connects released workloads. It does not absorb their source
code or domain ownership.

| Repository | Owns | Deploy consumes |
|---|---|---|
| `kerosene-core` | Auth, session and application gateway | Server image, health and configuration contracts |
| `kerosene-kfe` | Financial execution and settlement orchestration | KFE image and configuration contract |
| `kerosene-clients` | Flutter/web application | Published web image |
| `kerosene-vault` | Custody, DKG, FROST and signing | Vault image and documented runtime contract |
| `kerosene-node` | Identity, discovery and membership | Node image and documented runtime contract |
| `kerosene-rails` | Bitcoin Core and Lightning adapters | Adapter endpoints and health contracts |
| `kerosene-contracts` | Schemas and compatibility versions | Released contract identifiers |
| `kerosene-shared` | Approved Java primitives shared across services | Released library version |
| `kerosene-admin` | Administrative CLI | Published operator image |
| `kerosene-deploy` | Kubernetes, packaging, observability and rollout validation | Immutable images and environment-owned secret references |

## Packaging versus ownership

Dockerfiles under `infra/docker/images/` are deployment packaging recipes.
During packaging they use the owning repository as an external build context.
They are not copies of service source and do not make Deploy the owner of that
service.

Production receives image references by digest. It does not compile service
source from the Deploy checkout.

## Change routing

- API, domain or protocol changes go to the owning service repository.
- Schema and compatibility changes go to `kerosene-contracts`.
- Container wiring, manifests, policies, observability and rollout gates go here.
- Secrets are provisioned by the environment; only names and mounting contracts
  are versioned here.

mTLS and CometBFT implementation are intentionally outside this organization
wave. Future Deploy changes may wire released configuration only after their
service contracts are defined.
