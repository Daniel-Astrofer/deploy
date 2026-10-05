<!--
Kerosene documentation metadata
status: review-required
audience: internal
owner: deploy
source_of_truth: deploy
last_reviewed: 2026-09-03
-->

# Deploy quickstart

The public checkout exposes one fail-closed command:

```bash
bash infra/production/preflight.sh --start
```

Before running it, provide the private operations checkout and evidence
directory through `KEROSENE_PRODUCTION_OPS_DIR` and
`KEROSENE_PRODUCTION_EVIDENCE_DIR`, pin every required image by SHA-256 digest,
configure the trusted Sigstore identity and issuer, and set an approved
`KEROSENE_PRODUCTION_CHANGE_ID`.

The rendered manifest must explicitly select Bitcoin `testnet3`. The gate
validates evidence, manifest policy and a client-side Kubernetes dry-run before
applying anything. Omit `--start` to validate without changing the cluster.
