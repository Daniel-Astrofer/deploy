# Agent guide — Kerosene Deploy

## Scope

Deploy owns profiles, images, manifests, rollout, rollback and environment
operations. It never owns service implementations or secret values.

## Documentation

- Start at `docs/README.md`.
- Place boundaries in `architecture/`, environment facts in `reference/`, and
  runbooks/recovery in `operations/`.
- Keep source-local infrastructure READMEs beside their components and link them
  from the portal when they describe local runtime details.

## Safety and integration

- Never commit secrets, shares, macaroons, private keys or production data.
- Reference secret-manager objects from templates; examples must be synthetic.
- Deployment automation must never activate Vault signers automatically.

## Verification

Validate Compose/Kubernetes configuration and document rollback and recovery
for each production-impacting change.
