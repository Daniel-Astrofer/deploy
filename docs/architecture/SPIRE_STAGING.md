# SPIRE staging identity

## Status

The explicit `staging-spiffe` profile activates SPIFFE mTLS for Auth-to-KFE and
KFE-to-Auth calls. Both processes consume rotating X.509-SVIDs directly from
the CSI-mounted Workload API socket, require the exact peer SPIFFE ID and use a
dedicated TLS 1.3 connector on port `8443`. Their former shared transport secret
is not mounted in this profile and NetworkPolicies remove the cross-service
path on public port `8080`.

All browser-facing KFE namespaces (`/kfe`, `/api/public/kfe` and
`/api/admin/kfe`) enter through the Auth gateway. The web workload has no direct
KFE egress; Auth preserves the route and forwards only an allowlisted set of
headers through the authenticated internal connector.

The API server admission boundary prevents workload identity impersonation. It
reserves the trusted namespace labels, service accounts and ClusterSPIFFEID
objects; admits workload controllers only from the dedicated GitOps principal;
allows the exact built-in owner-controller chain; gives the CSI socket only to
the registered container; and requires reviewed image repositories plus
immutable digests. The negative suite runs against a disposable Kind cluster.

This activation requires images built from the matching Shared, Auth and KFE
workload-identity changes. Vault and Kerosene Node receive SVIDs but do not
consume them as their active transport identity yet. Cluster end-to-end
handshake, SVID rotation and HA evidence remain open.

## Layout

- `components/spire/bootstrap`: namespaces, pinned CRD and least-privilege RBAC.
- `components/spire/admission`: fail-closed CEL policies and the least-privilege
  `kerosene-deployer` identity.
- `components/spire/server`: restricted single-server staging control plane.
- `components/spire/agent`: isolated privileged agent and CSI DaemonSet.
- `components/spire/registration`: exact service/container registrations.
- `overlays/staging-spiffe`: socket mounts, strict Auth/KFE identities, mTLS
  endpoints and transport-isolating NetworkPolicies.
- `overlays/staging-vault-spiffe`: additive Vault/Node socket mounts and labels;
  no Vault transport migration yet.

Trust domain: `staging.kerosene.internal`.

## Validate and roll out

```bash
bash infra/kubernetes/scripts/validate-staging-spire.sh
SERVER_IMAGE=registry.example/kerosene-core@sha256:... \
KFE_SERVICE_IMAGE=registry.example/kerosene-kfe@sha256:... \
VAULT_IMAGE=registry.example/kerosene-vault@sha256:... \
NODE_IMAGE=registry.example/kerosene-node@sha256:... \
TOR_IMAGE=registry.example/kerosene-tor@sha256:... \
  bash infra/kubernetes/scripts/install-staging-spire.sh
```

The installer composes native `kubectl apply -k` operations in the only safe
order: bootstrap, both reviewed namespaces and immutable repository allow-list,
admission/RBAC, registrations through the GitOps identity, server and agent.
The invoking cluster operator or CI identity must be allowed to impersonate
only `system:serviceaccount:kerosene-gitops:kerosene-deployer`; this repository
does not grant that cluster-level permission.

Then deploy `staging-spiffe` and `staging-vault-spiffe` through
`infra/kubernetes/scripts/deploy.sh` with the same immutable image variables
required by their non-SPIFFE staging profiles. Before `staging-spiffe`, provision
`kfe-service-secrets/fee-quote-signing-secret` independently; it is application
signing material and must not reuse the retired transport credential.

## Rollback

After admission activation, rollback means redeploying the previous reviewed
`staging-spiffe`/`staging-vault-spiffe` digests. The admission boundary correctly
rejects legacy workloads that reuse reserved service accounts without their
reviewed identity contract. Disabling the boundary or returning to the shared
transport secret is a separate break-glass migration requiring explicit
security approval; it is not an automated rollback path.

## Production blockers

- HA SPIRE Server datastore and recovery rehearsal.
- Cluster-specific API server and kubelet CIDRs.
- Cluster evidence for Auth/KFE fail-closed handshakes, SVID rotation and
  negative peer-ID tests.
- Vault/Node migration from static certificate files to workload identities.
- Independent-host node attestation and multi-cluster federation policy.

Admission implementation and evidence are tracked by
[SPIFFE ID impersonation control #36](https://github.com/Daniel-Astrofer/kerosene-deploy/issues/36).
The remaining production evidence is tracked by
[SPIRE end-to-end/HA evidence #37](https://github.com/Daniel-Astrofer/kerosene-deploy/issues/37).
