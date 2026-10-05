#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# These are the operator-facing surfaces. Test fixtures are intentionally not
# scanned here; they never ship in an image or a deployment artifact.
targets=(
  "$ROOT/README.md"
  "$ROOT/docs/quickstart"
  "$ROOT/docs/operations"
  "$ROOT/docs/reference"
  "$ROOT/infra/production/preflight.sh"
  "$ROOT/infra/start.sh"
)

for target in "${targets[@]}"; do
  if [[ ! -e "$target" ]]; then
    echo "Production source policy failed: required source is missing: $target" >&2
    exit 3
  fi
  if rg -n -i \
    'dealer_lab|static_token|x-vault-token|attestation[_ -]?mode[[:space:]]*[:=][[:space:]]*(sim|software)|http://|localhost|127\.0\.0\.1' \
    "$target"; then
    echo "Production source policy failed: legacy or insecure operator guidance found in $target." >&2
    exit 3
  fi
done

if ! rg -q 'testnet3' "$ROOT/README.md" "$ROOT/docs/quickstart" "$ROOT/docs/reference" "$ROOT/infra/production"; then
  echo "Production source policy failed: testnet3 must be documented explicitly." >&2
  exit 3
fi

echo "Production source policy passed."
