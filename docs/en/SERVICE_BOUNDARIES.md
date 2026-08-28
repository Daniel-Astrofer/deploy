# Service boundaries

`kerosene-deploy` connects released workloads. It does not absorb their source
code or domain ownership.

| Repository | Owns | Deploy consumes |
|---|---|---|
| `kerosene-core` | Auth and KFE behavior | Server and KFE images, health and configuration contracts |
| `kerosene-clients` | Flutter/web application | Published web image |
| `kerosene-vault` | Custody, DKG, FROST and signing | Vault image and documented runtime contract |
| `kerosene-node` | Identity, discovery and membership | Node image and documented runtime contract |
| `kerosene-contracts` | Schemas and compatibility versions | Released contract identifiers |
| `kerosene-deploy` | Compose, Kubernetes, runtime helpers, observability and rollout validation | Immutable images and environment-owned secrets |

## Packaging versus ownership

Dockerfiles under `infra/docker/images/` are deployment packaging recipes.
During local development they may use an external repository as build context.
They are not copies of service source and do not make Deploy the owner of that
service.

Staging and production should receive image references by digest. They must not
compile service source from the Deploy checkout.

## Change routing

- API, domain or protocol changes go to the owning service repository.
- Schema and compatibility changes go to `kerosene-contracts`.
- Container wiring, manifests, policies, observability and rollout gates go here.
- Secrets are provisioned by the environment; only names and mounting contracts
  are versioned here.

mTLS and CometBFT implementation are intentionally outside this organization
wave. Future Deploy changes may wire released configuration only after their
service contracts are defined.
