# SPIRE staging identity

## Status

Staging foundation only. SPIRE issues short-lived X.509-SVIDs to Auth, KFE,
Vault and Kerosene Node through the CSI-mounted Workload API socket. Services do
not consume those SVIDs for mTLS yet; existing static certificates remain the
active transport identity.

## Layout

- `components/spire/bootstrap`: namespaces, pinned CRD and least-privilege RBAC.
- `components/spire/server`: restricted single-server staging control plane.
- `components/spire/agent`: isolated privileged agent and CSI DaemonSet.
- `components/spire/registration`: exact service/container registrations.
- `overlays/*-spiffe`: additive socket mounts and workload labels.

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
required by their non-SPIFFE staging profiles.

## Rollback

Redeploy `staging`/`staging-vault` first to remove workload CSI mounts. Delete
registrations, then agent, then server. Preserve the server PVC for diagnosis.
Remove RBAC/CRD only after all `ClusterSPIFFEID` objects are gone.

## Production blockers

- HA SPIRE Server datastore and recovery rehearsal.
- Cluster-specific API server and kubelet CIDRs.
- Service-side SVID rotation, peer-ID authorization and fail-closed mTLS.
- Admission policy restricting trusted ServiceAccounts/labels to GitOps.
- Controller admission webhook or equivalent semantic CR validation.
- Independent-host node attestation and multi-cluster federation policy.

Tracked gates: [SPIFFE ID impersonation control #36](https://github.com/Daniel-Astrofer/kerosene-deploy/issues/36)
and [SPIRE end-to-end/HA evidence #37](https://github.com/Daniel-Astrofer/kerosene-deploy/issues/37).
