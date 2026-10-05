<!--
Kerosene documentation metadata
status: review-required
audience: internal/restricted
owner: deploy
source_of_truth: deploy
last_reviewed: 2026-09-03
-->

# Administrative CLIs

`kerosene-rsctl` and `kerosene-jctl` are clients, not authority sources. Server
authentication, authorization and domain validation remain mandatory. Neither
CLI connects directly to PostgreSQL, Redis, a Vault data directory, signing
shares or nonce storage.

Build the standalone operator images with:

```bash
bash infra/docker/build-image.sh kerosene-rsctl
bash infra/docker/build-image.sh kerosene-jctl
```

Run them only from an operator workstation, hardened bastion or short-lived
administrative pod. Do not add either binary to the Core, Node or Vault runtime
images. Mount CA/operator identity files read-only from a secret manager or
systemd credentials. Do not put tokens, private keys or passphrases in profiles,
image layers, environment files committed to Git, shell history or GitHub
Actions.

Production Node and Vault access uses HTTPS with mTLS and Tor DNS through
`socks5h://`. For example:

```bash
kerosene-rsctl \
  --endpoint https://node.example.onion \
  --identity-pem /run/credentials/operator.pem \
  --ca /run/credentials/kerosene-ca.pem \
  --socks5h socks5h://tor-proxy.kerosene.svc:9050 \
  --output json node status
```

The initial command set is read-only, except the offline membership workflow.
`membership create`, `sign`, `assemble` and `verify` operate on local artifacts;
only `publish` changes Node state and requires the Node's own mTLS authorization.
Each signer must receive the unsigned proposal through an authenticated
out-of-band channel, independently verify its canonical hash and return only the
signed manifest. Identity files must be mode `0600`.

`kerosene-jctl` authenticates with its own short-lived mTLS workload identity.
JVM key and trust material are supplied by the hardened runtime using the
standard `javax.net.ssl.keyStore` and `javax.net.ssl.trustStore` properties.
Static bearer tokens are prohibited. Never give mutable production scopes to
automation agents.

Rollback is simply removal of the ephemeral operator container or binary; no
service rollout is involved. If an operator identity is suspected compromised,
revoke its certificate/token before reissuing the CLI.
