# SPIRE upstream provenance

This staging component vendors one CRD and pins every runtime image by OCI
index digest. Updates require release-note review, digest verification, rendered
manifest tests and a staging rollback rehearsal.

| Artifact | Version | Immutable reference |
|---|---:|---|
| SPIRE Server | 1.15.2 | `ghcr.io/spiffe/spire-server@sha256:aa74ef1be86bc8e0684007d84a4d9859d294384d842c30425048d73429f3216e` |
| SPIRE Agent | 1.15.2 | `ghcr.io/spiffe/spire-agent@sha256:1d042e4040466686e0ee46f74981ff2167c86adfadca19b3835946f4d6047536` |
| SPIRE Controller Manager | 0.7.0 | `ghcr.io/spiffe/spire-controller-manager@sha256:d7b9e710f5428ea25daa35a07083fe517ed70c23df902d21c54a0f444455a84b` |
| SPIFFE CSI Driver | 0.2.13 | `ghcr.io/spiffe/spiffe-csi-driver@sha256:6f1ba5d635ba9bf55ae69ba11d71bf169c8fa3e0139a5063738cbc227c8b5546` |
| CSI Node Driver Registrar | 2.15.0 | `registry.k8s.io/sig-storage/csi-node-driver-registrar@sha256:11f199f6bec47403b03cb49c79a41f445884b213b382582a60710b8c6fdc316a` |

Vendored CRD source:

`https://raw.githubusercontent.com/spiffe/spire-controller-manager/v0.7.0/config/crd/bases/spire.spiffe.io_clusterspiffeids.yaml`

Expected SHA-256 for `bootstrap/clusterspiffeid-crd.yaml`:

`f35a66a3b1dedb2793c465f03502df05035d4fb243a7d1313c5cf7dfbe3af2fd`

The public component is staging-only: one SPIRE Server, SQLite storage and
portable wide CIDRs for Kubernetes API/kubelet access are not production HA or
production network policy.
