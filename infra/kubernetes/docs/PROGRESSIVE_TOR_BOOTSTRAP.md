# Progressive Tor-only bootstrap

The production-like topology is asymmetric and progressive. Zero-configuration
enrollment is forbidden: onion reachability is routing, while signed membership
is authorization.

## Phase 0: trust material

Pre-generate each Kerosene Node identity. Put its public root and network-bound
member ID in `GenesisTrustBundleV1`, then provision the matching private
`identity.key`. Provision mTLS independently; no repository or CI receives
production private material.

The Node server certificate must cover the stable v3 onion hostname. The local
Vault-plane certificate also covers `tor.kerosene-staging-vault.svc`, because
the Vault reaches its colocated directory through that protected Service.

## Phase 1: Core only

`deploy.sh staging` starts one server, KFE, web gateway, PostgreSQL, Redis,
Bitcoin Core, LND, Tor and a Bank-plane Kerosene Node. Tor publishes the web
gateway and Node on the same stable onion with different ports.

Vault integration remains disabled. Core can serve local-safe functions but
cannot obtain a Vault quorum signature. Scaling application replicas does not
create Bitcoin nodes: all replicas use the same staging-owned Bitcoin service.

## Phase 2: first Vault

`deploy.sh staging-vault` starts one Vault, Tor and a Vault-plane Kerosene Node
in an independent namespace with separate storage and secrets. The Vault reads
membership from its local Node over mTLS.

The first controlled staging boot may have no membership manifest. It remains
locally alive but financially unready. Neither Kubernetes nor the Node turns
that single process into a valid constitution.

## Phase 3: signed discovery

Operators publish a signed Vault-plane `MembershipManifestV1` whose member
endpoints are `https://<v3-onion>:8800`. Nodes verify network, plane, signatures
and membership transitions before serving the roster.

Vaults derive peer URLs from those verified onion hosts and the fixed Vault
service port `7801`. The KFE keeps its explicit Vault URLs but rejects any host
absent from the verified Node roster. Both paths remain Tor + mTLS and reject
clearnet/Kubernetes service endpoints across the trust boundary.

Vault peer discovery is currently a startup snapshot, so restart a Vault after
an accepted roster transition. KFE refreshes its authorization roster on its
configured cache interval.

## Phase 4: progressive quorum

Additional Vaults repeat Phase 2 with independent identities and storage.
Admission follows the signed OLD → JOINT → NEW transition; seed endpoints never
authorize a member. Financial readiness becomes true only when the verified
live count reaches the constitution threshold, normally 2-of-3.

## Trust boundaries

- Core never receives a Vault share or Vault onion private key.
- Vault never receives Core database, Bitcoin RPC, LND macaroon or JWT secrets.
- Kerosene Node never receives FROST shares, nonces or signer activation power.
- No Vault API is exposed by LoadBalancer, NodePort or ingress.
- Cross-boundary application traffic is onion + mTLS only.
- Kubernetes readiness is not a cryptographic trust anchor.
