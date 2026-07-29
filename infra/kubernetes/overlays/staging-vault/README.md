# Independent staging Vault

This overlay starts one Vault and its own Tor process. It has no dependency on
the Core namespace and can remain locally healthy with zero peers. Financial
signing remains unavailable until the configured constitution reaches quorum.

Required secrets are provisioned independently:

- `vault-identity`: `node-id`
- `vault-secrets`: `data-passphrase`, `attestation-root`
- `vault-mtls-certs`: `ca.crt`, `vault-server.crt`,
  `vault-server.key`, `vault-client.crt`, `vault-client.key`

The Tor data PVC owns the stable onion identity. Back it up as sensitive key
material. Core receives only the resulting onion endpoint and the trust
material; it never receives this private onion identity.
