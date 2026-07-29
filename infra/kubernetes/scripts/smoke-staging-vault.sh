#!/usr/bin/env bash
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
NAMESPACE="${KEROSENE_STAGING_VAULT_NAMESPACE:-kerosene-staging-vault}"
PORT="${KEROSENE_STAGING_VAULT_SMOKE_PORT:-17801}"
LOG_FILE="$(mktemp "${TMPDIR:-/tmp}/kerosene-vault-port-forward.XXXXXX.log")"

cleanup() {
  if [[ -n "${PORT_FORWARD_PID:-}" ]]; then
    kill "$PORT_FORWARD_PID" 2>/dev/null || true
    wait "$PORT_FORWARD_PID" 2>/dev/null || true
  fi
  unlink "$LOG_FILE" 2>/dev/null || true
}
trap cleanup EXIT

command -v curl >/dev/null 2>&1 || {
  echo "[!] curl is required for the Vault smoke probe." >&2
  exit 127
}

"$KUBECTL" -n "$NAMESPACE" port-forward deployment/vault "${PORT}:7801" \
  >"$LOG_FILE" 2>&1 &
PORT_FORWARD_PID=$!

health=""
for _ in $(seq 1 30); do
  if ! kill -0 "$PORT_FORWARD_PID" 2>/dev/null; then
    cat "$LOG_FILE" >&2
    exit 1
  fi
  health="$(curl --silent --show-error --insecure \
    --connect-timeout 2 "https://127.0.0.1:${PORT}/v1/health" 2>/dev/null || true)"
  if grep -Eq '"status":"(starting|ready)"' <<<"$health"; then
    break
  fi
  sleep 2
done

grep -Eq '"local_ready":true' <<<"$health" || {
  echo "[!] Vault did not become locally ready: ${health}" >&2
  exit 1
}

# A single Vault is locally healthy but must not pretend a configured 2-of-3
# financial quorum exists.
if ! grep -Eq '"financial_ready":false' <<<"$health"; then
  echo "[!] Fresh single-Vault bootstrap unexpectedly reports financial readiness: ${health}" >&2
  exit 1
fi

echo "[+] Independent Vault is locally healthy and limited without quorum."
