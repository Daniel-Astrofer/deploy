#!/usr/bin/env bash
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
NS="${KEROSENE_STAGING_NAMESPACE:-kerosene-staging}"
PORT="${KEROSENE_STAGING_LOGIN_PORT:-18080}"
TMP_DIR="$(mktemp -d)"
PF_LOG="$TMP_DIR/server-port-forward.log"
PF_PID=""

cleanup() {
  if [[ -n "$PF_PID" ]]; then
    kill "$PF_PID" >/dev/null 2>&1 || true
    wait "$PF_PID" >/dev/null 2>&1 || true
  fi
  find "$TMP_DIR" -type f -exec unlink {} \;
  rmdir "$TMP_DIR"
}
trap cleanup EXIT

decode_secret() {
  local key="$1"
  "$KUBECTL" -n "$NS" get secret staging-smoke-credentials \
    -o "jsonpath={.data.${key}}" | base64 --decode
}

username="$(decode_secret username)"
password="$(decode_secret password)"
[[ -n "$username" && -n "$password" ]] || {
  echo "[!] staging-smoke-credentials must contain non-empty username and password." >&2
  exit 1
}

"$KUBECTL" -n "$NS" port-forward service/server "${PORT}:8080" >"$PF_LOG" 2>&1 &
PF_PID=$!

for _ in $(seq 1 30); do
  if curl --silent --fail --max-time 2 "http://127.0.0.1:${PORT}/health/ready" >/dev/null; then
    break
  fi
  kill -0 "$PF_PID" >/dev/null 2>&1 || {
    echo "[!] Server port-forward stopped before login smoke." >&2
    exit 1
  }
  sleep 1
done

payload="$(
  USERNAME="$username" PASSWORD="$password" python3 - <<'PY'
import json
import os
print(json.dumps({"username": os.environ["USERNAME"], "password": os.environ["PASSWORD"]}))
PY
)"
response="$(
  curl --silent --show-error --fail-with-body --max-time 15 \
    -H 'Content-Type: application/json' \
    --data "$payload" \
    "http://127.0.0.1:${PORT}/auth/login"
)"

grep -Eq '"success"[[:space:]]*:[[:space:]]*true' <<<"$response" || {
  echo "[!] Staging login smoke did not return success=true." >&2
  exit 1
}

echo "[+] Staging login smoke passed."
