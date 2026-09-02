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

This activation requires images built from the matching Shared, Auth and KFE
workload-identity changes. It has passed source and manifest tests, but has not
yet produced cluster end-to-end or rotation evidence. Vault and Kerosene Node
receive SVIDs but do not consume them as their active transport identity yet.

## Layout

- `components/spire/bootstrap`: namespaces, pinned CRD and least-privilege RBAC.
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
kubectl apply -k infra/kubernetes/components/spire/bootstrap
kubectl apply -k infra/kubernetes/components/spire/server
kubectl -n spire-server rollout status statefulset/spire-server --timeout=5m
test -n "$(kubectl -n spire-system get configmap/spire-bundle -o jsonpath='{.data.bundle\.crt}')"
kubectl apply -k infra/kubernetes/components/spire/agent
kubectl -n spire-system rollout status daemonset/spire-agent --timeout=10m
kubectl apply -f infra/kubernetes/overlays/staging/namespace.yaml
kubectl apply -f infra/kubernetes/overlays/staging-vault/namespace.yaml
kubectl apply -k infra/kubernetes/components/spire/registration
bash infra/kubernetes/scripts/preflight-staging-spire.sh core
bash infra/kubernetes/scripts/preflight-staging-spire.sh vault
```

Then deploy `staging-spiffe` and `staging-vault-spiffe` through
`infra/kubernetes/scripts/deploy.sh` with the same immutable image variables
required by their non-SPIFFE staging profiles. Before `staging-spiffe`, provision
`kfe-service-secrets/fee-quote-signing-secret` independently; it is application
signing material and must not reuse the retired transport credential.

## Rollback

Redeploy `staging`/`staging-vault` first to restore the previous transport and
remove workload CSI mounts. The non-SPIFFE profile still requires the legacy
`server-secrets/kfe-internal-shared-secret` during this rollback window. Delete
registrations, then agent, then server. Preserve the server PVC for diagnosis.
Remove RBAC/CRD only after all `ClusterSPIFFEID` objects are gone.

## Production blockers

- HA SPIRE Server datastore and recovery rehearsal.
- Cluster-specific API server and kubelet CIDRs.
- Cluster evidence for Auth/KFE fail-closed handshakes, SVID rotation and
  negative peer-ID tests.
- Vault/Node migration from static certificate files to workload identities.
- Admission policy restricting trusted ServiceAccounts/labels to GitOps.
- Controller admission webhook or equivalent semantic CR validation.
- Independent-host node attestation and multi-cluster federation policy.

Tracked gates: [SPIFFE ID impersonation control #36](https://github.com/Daniel-Astrofer/kerosene-deploy/issues/36)
and [SPIRE end-to-end/HA evidence #37](https://github.com/Daniel-Astrofer/kerosene-deploy/issues/37).
