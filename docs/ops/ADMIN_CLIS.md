# Administrative CLIs

`kerosene-rsctl` and `kerosene-jctl` are clients, not authority sources. Server
authentication, authorization and domain validation remain mandatory. Neither
CLI connects directly to PostgreSQL, Redis, a Vault data directory, signing
shares or nonce storage.

The `admin` entry in a release lock means an immutable operator artifact. It is
not a Kubernetes workload: do not create a Deployment, StatefulSet, DaemonSet
or Service for it, and do not add it to the Core, Node or Vault runtime images.
Run the CLI from a hardened operator workstation/bastion or a short-lived
local administrative container outside Kubernetes, then remove the
binary/container and its temporary credentials. The CLI does not become a
source of truth or acquire authority by being present on a host; server
authorization and signed release evidence remain authoritative.

Build the standalone operator images with:

```bash
bash infra/docker/build-image.sh kerosene-rsctl
bash infra/docker/build-image.sh kerosene-jctl
```

Run them only from an operator workstation, hardened bastion or short-lived
local administrative container; never from a Kubernetes Pod that is kept as
part of the Cell. Do not add either binary to the Core, Node or Vault runtime
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
  --socks5h socks5h://127.0.0.1:9050 \
  --output json node status
```

The initial command set is read-only, except the offline membership workflow.
`membership create`, `sign`, `assemble` and `verify` operate on local artifacts;
only `publish` changes Node state and requires the Node's own mTLS authorization.
Each signer must receive the unsigned proposal through an authenticated
out-of-band channel, independently verify its canonical hash and return only the
signed manifest. Identity files must be mode `0600`.

`kerosene-jctl` reads a short-lived bearer token only from
`KEROSENE_ADMIN_TOKEN`. JVM mTLS material can be supplied through a hardened
runtime using the standard `javax.net.ssl.keyStore` and
`javax.net.ssl.trustStore` properties. Never give mutable production scopes to
automation agents.

Rollback is simply removal of the ephemeral operator container or binary; no
service rollout is involved. If an operator identity is suspected compromised,
revoke its certificate/token before reissuing the CLI.

## Safe Cell release workflow

`kerosene-stack` is also an ephemeral operator command. Keep the release lock,
public verification keys and signed evidence in an operator-controlled,
read-only staging directory. Obtain them through an authenticated distribution
channel and compare the intended release ID and sequence before running an
update. The lock and evidence must never contain bearer tokens, private keys,
macaroons or signing shares; operator credentials are mounted separately and
must not be written to shell history, images or Git.

The operator workflow is deliberately gated:

1. Ask the signed Bank observations whether this cell needs an update. The
   command is local and read-only; it does not contact Kubernetes, change the
   Cell or validate a snapshot.

   ```bash
   kerosene-stack check-update \
     --release release-lock.json \
     --environment staging-cell \
     --tuf-metadata-dir /srv/kerosene/releases/tuf \
     --tuf-trusted-root /etc/kerosene-stack/trusted-root.json \
     --tuf-state-dir /var/lib/kerosene-stack \
     --bft-receipt release-receipt.json \
     --validator-roster release-roster.json \
     --bank-observer-report bank-observer-report.json \
     --state-dir /var/lib/kerosene-stack \
     --json
   ```

   Continue only when the command succeeds, `updateRequired` is `true`,
   `manualRecoveryRequired` is `false`, and `nextAction` is
   `kerosene-stack update --apply`. `tufSignatureVerified` must be `true`,
   while `bftSignaturesVerified` and `bankObserversVerified` are counts that
   must meet the threshold declared by the lock. A false result, expired TUF
   metadata or Bank report, a sequence that is not newer, or any recovery flag is a stop
   condition. The Bank report is a short-lived signed observation supplied to the command;
   the current public adapter does not silently replace it with an
   unauthenticated live query.

2. Collect an unsigned `VolumeSnapshot` attestation request for the ten
   persistent Cell PVCs with
   `collect-staging-volumesnapshot-attestation-request.sh`. An independent
   attester must restore-test that exact set and issue the v2 receipt. `update
   --apply` verifies both digests, the provider signature, expiry and
   `restoreTested: true`. A locally authored JSON file is not a snapshot
   authorization.

3. Run the complete evidence gate in server-side dry-run mode. Use the same
   release, complete offline TUF bundle, BFT receipt, validator roster, Bank
   report, snapshot request and snapshot receipt that will be used for the real apply. Replace the example
   `--confirm-release` value with the exact `releaseId` from the lock:

   ```bash
   kerosene-stack update \
     --release release-lock.json \
     --apply \
     --environment staging-cell \
     --confirm-release bank-mainnet-2026.09.28.1 \
     --tuf-metadata-dir /srv/kerosene/releases/tuf \
     --tuf-trusted-root /etc/kerosene-stack/trusted-root.json \
     --tuf-state-dir /var/lib/kerosene-stack \
     --bft-receipt release-receipt.json \
     --validator-roster release-roster.json \
     --bank-observer-report bank-observer-report.json \
     --snapshot-attestation-request snapshot-attestation-request.json \
     --snapshot-receipt snapshot-receipt.json \
     --snapshot-provider-key snapshot-provider-key.b64 \
     --state-dir /var/lib/kerosene-stack \
     --dry-run --json
   ```

   A successful dry run records `dry-run-passed` but does not change
   Kubernetes resources. Review the release ID, target sequence, signed
   observer count and rendered image digests before continuing.

4. With the exact same evidence and release confirmation, repeat the command
   without `--dry-run`. The public adapter applies `staging-vault` first and
   then `staging`; production is not enabled by this command. Inspect the
   resulting `update-state.json`. A `committed` state is the only success
   signal. If the state is `failed` or `manualRecoveryRequired` is set, stop
   and follow the recovery runbook; do not retry or perform an automatic
   rollback of PostgreSQL, Bitcoin, LND or Vault.

   `update --apply` creates `update.lock` exclusively in `--state-dir` and
   holds it throughout the operation. An existing lock blocks the update before
   rollout: it can mean another operator is active or that a previous operator
   process ended unexpectedly. Treat both cases as manual-investigation
   conditions. Preserve the lock and state record, establish whether an update
   is still running and reconcile the Cell before an authorized operator clears
   anything. Never delete `update.lock` automatically or use a retry loop to
   bypass it.

   `--tuf-state-dir` must resolve to the same protected directory as
   `--state-dir`, so anti-rollback TUF state and the update lock are serialized.
