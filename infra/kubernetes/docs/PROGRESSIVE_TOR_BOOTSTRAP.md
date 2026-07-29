# Progressive Tor-only bootstrap

The production-like topology is intentionally asymmetric and progressive.

## Phase 1: Core only

`deploy.sh staging` starts one `server`, one `kfe-service`, the web gateway,
PostgreSQL, Redis, Bitcoin Core, LND, and a Core-owned Tor process.

Vault integration is disabled. The Core may serve non-custodial/local-safe
functions, but it cannot obtain a Vault quorum signature. Scaling Core replicas
does not create additional Bitcoin nodes: every Core replica uses the same
environment-owned `staging-bitcoin` service.

## Phase 2: first Vault

`deploy.sh staging-vault` starts one Vault in its own namespace, with separate
storage, secrets, service account, network policy, and Tor process. Its onion
identity lives only in the Tor PVC.

The Vault reports:

- `local_ready=true`: its local process and storage are usable;
- `financial_ready=false`: the configured constitution threshold is not live.

It must not sign as if a one-node installation were a complete constitution.

## Phase 3: authenticated discovery

Zero-configuration discovery is deliberately forbidden: a malicious onion
service must not be able to enroll itself. The discovery service in
`kerosene-node` must validate a signed membership manifest and attestation,
then publish:

- `coordinator-url`: one stable `https://…onion` URL;
- `vault-urls`: the currently verified onion roster;
- an mTLS trust bundle in `kfe-vault-mtls-certs`.

KFE accepts Vault traffic only through its SOCKS proxy. Its transport validation
rejects clearnet hosts, Kubernetes Service DNS, plaintext HTTP, missing mTLS,
and local DNS resolution of onion names.

Until the discovery controller is implemented, leave
`KFE_VAULTMESH_ENABLED=false`. Do not replace discovery with a direct
`https://vault:7801` connection.

## Phase 4: progressive quorum

Additional Vaults repeat Phase 2 with independent identities and storage.
Discovery adds them only after authentication. Financial readiness becomes true
only when the live, verified count reaches the constitution threshold (normally
2-of-3). Core and Vault liveness remain independent from that threshold.

## Trust boundaries

- Core never receives a Vault share or the Vault onion private key.
- Vault never receives Core database, Bitcoin RPC, LND macaroon, or JWT secrets.
- No Vault API is exposed with a LoadBalancer, NodePort, or ingress.
- Cross-boundary application traffic is onion + mTLS only.
- Kubernetes orchestration may observe readiness, but it is not a cryptographic
  trust anchor and cannot approve membership.
