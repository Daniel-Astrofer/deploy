<!--
Kerosene documentation metadata
status: review-required
audience: internal
owner: deploy
source_of_truth: deploy
last_reviewed: 2026-09-03
-->

# Deploy environment contract

| Profile | Status in this repository | Source of truth | Mutation entrypoint |
|---|---|---|---|
| production on Bitcoin `testnet3` | supported | private operations overlay plus signed evidence | `infra/production/preflight.sh --start` |
| local, lab, staging, regtest, signet or mainnet | unsupported | none | none |

The public repository cannot make a production environment complete by itself.
It validates the private overlay, immutable images, security constraints and
evidence, but never manufactures credentials or activates a signer.
