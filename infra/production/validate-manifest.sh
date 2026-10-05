#!/usr/bin/env bash
set -euo pipefail

[[ "$#" -eq 1 ]] || { echo "Usage: $0 <rendered-manifest>" >&2; exit 2; }
MANIFEST="$1"
[[ -f "$MANIFEST" ]] || {
  echo "Manifest not found: $MANIFEST" >&2
  exit 2
}

if grep -Eiq 'dealer_lab|static_token|x-vault-token|attestation_mode:[[:space:]]*(sim|software)|mainnet|regtest|signet|kerosene-staging|localhost|127\.0\.0\.1|http://' "$MANIFEST"; then
  echo "Production manifest contains a prohibited legacy, simulated or insecure setting." >&2
  exit 3
fi

has_testnet3_network() {
  grep -Eiq '(bitcoin[_.-]network|BITCOIN_NETWORK)[^[:alnum:]]*[:=][[:space:]\"]*testnet3' "$MANIFEST" ||
    awk '
      /^[[:space:]]*-[[:space:]]*name:[[:space:]]*BITCOIN_NETWORK[[:space:]]*$/ {
        awaiting_value = 1
        next
      }
      awaiting_value && /^[[:space:]]*value:[[:space:]]*/ {
        value = $0
        sub(/^[[:space:]]*value:[[:space:]]*/, "", value)
        gsub(/\047/, "", value)
        gsub(/"/, "", value)
        found = (tolower(value) == "testnet3")
        exit
      }
      awaiting_value && /^[[:space:]]*-[[:space:]]*name:/ {
        exit 1
      }
      END {
        exit(found ? 0 : 1)
      }
    ' "$MANIFEST"
}

if ! has_testnet3_network; then
  echo "Production manifest must explicitly pin Bitcoin to testnet3." >&2
  exit 3
fi

if grep -Eq 'type:[[:space:]]*(NodePort|LoadBalancer)' "$MANIFEST"; then
  echo "Production manifest exposes a public Kubernetes Service; Tor ingress is required." >&2
  exit 3
fi

if grep -Eq '^kind:[[:space:]]*Secret$|^[[:space:]]+(data|stringData):[[:space:]]*$' "$MANIFEST"; then
  echo "Production manifest must reference external secrets, not embed Secret data." >&2
  exit 3
fi

while IFS= read -r image; do
  if [[ ! "$image" =~ @sha256:[0-9a-f]{64}$ ]]; then
    echo "Production image is not fixed by digest: $image" >&2
    exit 3
  fi
done < <(awk '$1 == "image:" {gsub(/"/, "", $2); print $2}' "$MANIFEST")

echo "Production manifest guardrails passed."
