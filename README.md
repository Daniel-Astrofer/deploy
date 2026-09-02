# Kerosene Deploy

Deployment templates, orchestration and operational runbooks for Kerosene.

Documentation entrypoints:

- [English operator documentation](docs/en/README.md)
- [Documentação para operadores em português](docs/pt-BR/README.md)
- [Docker, Kubernetes and scripts index](docs/en/DEPLOYMENT_INDEX.md)

This repository contains Docker/Kubernetes/Tor/observability configuration and
operational validation for independently released services. It must not contain runtime secrets,
shares, seed phrases, macaroons, private Onion keys, TPM keys, user data or real
logs. Private visibility is an additional control, not a secret manager.

## Polyrepo workspace

Deploy never reads an archived source tree. Optional local image builds resolve
independent repositories with `KEROSENE_CORE_DIR`, `KEROSENE_CLIENTS_DIR`,
`KEROSENE_VAULT_DIR`, `KEROSENE_NODE_DIR` and `KEROSENE_CONTRACTS_DIR`.

The supported default is a flat workspace of sibling repositories. Explicit
environment variables may point to another checkout layout. Validate a local
checkout with:

```bash
bash infra/scripts/check-polyrepo-workspace.sh
```

Service source remains owned by its repository; Deploy only supplies packaging
recipes and runtime composition. See the [service boundary](docs/en/SERVICE_BOUNDARIES.md).

Start the local integration environment through the canonical public entrypoint:

```bash
bash infra/start.sh
```

`infra/start-complete.sh` is a legacy compatibility wrapper. It remains
available during the transition, but new automation must use the environment
entrypoints documented in `docs/en/DEPLOYMENT_INDEX.md`.

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

Before enabling the SPIFFE profiles, provision the native admission boundary
with `infra/kubernetes/scripts/install-staging-spire.sh`. It pins the approved
image repositories, installs fail-closed CEL policies and limits workload
writes to the dedicated `kerosene-deployer` principal. The CI negative suite
proves that direct Pods, copied service accounts/labels, CSI socket sidecars and
arbitrary ClusterSPIFFEID resources are denied by a real Kubernetes API server.

`infra/start-complete.sh production` is fail-closed. It requires a private
operations overlay, immutable images, external audit/recovery evidence and an
approved change identifier. This public repository never manufactures secrets
or activates Vault signers.

Every production gate document must conform to
`infra/production/evidence.schema.json`. The referenced report is checked by
SHA-256 and its Sigstore bundle is verified against an explicitly trusted
certificate identity and OIDC issuer. Evidence must be current and carry two
distinct operator approvals. Configure the trust policy at runtime:

```bash
export KEROSENE_EVIDENCE_CERTIFICATE_IDENTITY_REGEXP='^release-security@example\.com$'
export KEROSENE_EVIDENCE_OIDC_ISSUER_REGEXP='^https://token\.actions\.githubusercontent\.com$'
```

Production requires immutable digest references for `SERVER_IMAGE`,
`KFE_SERVICE_IMAGE`, `WEB_PAGE_IMAGE`, `VAULT_IMAGE`, `NODE_IMAGE` and
`TOR_IMAGE`. Missing, expired, unsigned or failed evidence blocks deployment
before the private manifest is sent to Kubernetes.
