<!--
Kerosene documentation metadata
status: active
audience: internal
owner: deploy
source_of_truth: deploy
last_reviewed: 2026-09-03
-->

# Kubernetes resources

`base/` is the public, secret-free Kubernetes base consumed by an operations-
owned private production overlay. It deliberately contains placeholder image
references; the private overlay must replace every workload image with an
immutable digest and set `BITCOIN_NETWORK=testnet3`.

The public checkout does not contain an environment overlay and cannot be
applied directly. Render and validate the private overlay through:

```bash
bash infra/production/preflight.sh
```

Add `--start` only for an approved rollout. The preflight validates signed
evidence, rejects public service exposure and embedded secrets, performs a
client-side Kubernetes dry-run and only then applies the manifest.

`examples/secrets/` documents object shape with placeholders only. Generic
diagnostic and recovery helpers live in `scripts/`; none is an alternative
deployment entrypoint.
