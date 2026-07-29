#!/usr/bin/env bash
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
NS="${KEROSENE_STAGING_NAMESPACE:-kerosene-staging}"
TMP_DIR="$(mktemp -d)"
PF_PIDS=()

cleanup() {
  local pid
  for pid in "${PF_PIDS[@]}"; do
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" >/dev/null 2>&1 || true
  done
  find "$TMP_DIR" -type f -exec unlink {} \;
  rmdir "$TMP_DIR"
}
trap cleanup EXIT

umask 077
for key in ca.crt vault-client.crt vault-client.pkcs8.key; do
  "$KUBECTL" -n "$NS" get secret kfe-vault-mtls-certs \
    -o "jsonpath={.data.${key//./\\.}}" | base64 --decode > "$TMP_DIR/$key"
  [[ -s "$TMP_DIR/$key" ]] || {
    echo "[!] kfe-vault-mtls-certs has an empty ${key}." >&2
    exit 1
  }
done

for vault_id in 1 2 3; do
  port=$((17800 + vault_id))
  "$KUBECTL" -n "$NS" port-forward "service/vault-${vault_id}" "${port}:7801" \
    >"$TMP_DIR/vault-${vault_id}.log" 2>&1 &
  PF_PIDS+=("$!")
done

for vault_id in 1 2 3; do
  port=$((17800 + vault_id))
  response=""
  for _ in $(seq 1 30); do
    response="$(
      curl --silent --show-error --max-time 3 \
        --noproxy '*' \
        --cacert "$TMP_DIR/ca.crt" \
        --cert "$TMP_DIR/vault-client.crt" \
        --key "$TMP_DIR/vault-client.pkcs8.key" \
        --resolve "vault-${vault_id}:${port}:127.0.0.1" \
        "https://vault-${vault_id}:${port}/v1/health" 2>/dev/null || true
    )"
    if [[ -n "$response" ]]; then
      break
    fi
    sleep 1
  done
  grep -Eq '"status":"ready"' <<<"$response" || {
    echo "[!] vault-${vault_id} is not ready: ${response:-no response}" >&2
    exit 1
  }
  peer_count="$(sed -n 's/.*"peer_count":\([0-9][0-9]*\).*/\1/p' <<<"$response")"
  if [[ -z "$peer_count" || "$peer_count" -lt 2 ]]; then
    echo "[!] vault-${vault_id} has ${peer_count:-0} peers; FROST quorum needs both peers." >&2
    exit 1
  fi
done

echo "[+] Three-vault FROST readiness smoke passed."
