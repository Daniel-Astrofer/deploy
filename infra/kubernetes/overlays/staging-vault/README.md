# Independent staging Vault

This overlay starts one Vault and its own Tor process. It has no dependency on
the Core namespace and can remain locally healthy with zero peers. Financial
signing remains unavailable until the configured constitution reaches quorum.

Required secrets are provisioned independently:

- `vault-identity`: `node-id`
- `vault-secrets`: `data-passphrase`, `attestation-root`
- `vault-mtls-certs`: `ca.crt`, `vault-server.crt`,
  `vault-server.key`, `vault-client.crt`, `vault-client.key`
- `kerosene-node-genesis`: `genesis-trust-bundle.json`
- `kerosene-node-identity`: pre-provisioned `identity.key`, `member-id`
- `kerosene-node-mtls`: `ca.crt`, `server.crt`, `server.key`,
  `client-identity.pem`, `client.crt`, `client.pkcs8.key`

The Tor data PVC owns the stable onion identity. Back it up as sensitive key
material. Core receives only the resulting onion endpoint and the trust
material; it never receives this private onion identity.

The Vault-plane Node shares the Tor pod and onion hostname but has a distinct
identity and mTLS boundary. The Vault reads its verified roster from that local
Node. An absent first manifest is allowed only for staging bootstrap; it leaves
the Vault without financial quorum and does not activate signing.
