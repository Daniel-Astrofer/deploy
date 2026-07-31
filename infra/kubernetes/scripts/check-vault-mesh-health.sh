#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
COMPOSE_FILE="${KEROSENE_VAULT_MESH_COMPOSE_FILE:-$REPO_ROOT/infra/docker/compose/vault-mesh-lab.compose.yaml}"
COMPOSE_DIR="$(cd "$(dirname "$COMPOSE_FILE")" && pwd)"
VAULT_TOKEN="${VAULT_API_TOKEN:-kerosene-vault-lab-only}"
AUTH_MODE="${VAULT_AUTH_MODE:-static_token}"

all_ok=0

echo "[*] Checking vault mesh containers via docker compose ps"
docker compose --project-directory "$COMPOSE_DIR" -f "$COMPOSE_FILE" ps || true

check_vault_running() {
  local name="$1"
  if docker inspect "$name" >/dev/null 2>&1; then
    local status
    status="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || true)"
    if [[ "$status" == "running" ]]; then
      echo "[+] $name is running"
      return 0
    fi
  fi
  echo "[!] $name is NOT running" >&2
  all_ok=1
  return 1
}

check_vault_health() {
  local port="$1"
  local label="$2"
  local url
  local health_ok=1

  if [[ "$AUTH_MODE" == "mtls" || "$AUTH_MODE" == "mutual_tls" ]]; then
    url="https://127.0.0.1:${port}/v1/health"
    curl -fsS --max-time 5 -o /dev/null \
      --cacert "${KEROSENE_VAULT_MESH_CERTS_DIR:-$VAULT_DIR/lab-certs}/ca.crt" \
      --cert "${KEROSENE_VAULT_MESH_CERTS_DIR:-$VAULT_DIR/lab-certs}/vault-client.crt" \
      --key "${KEROSENE_VAULT_MESH_CERTS_DIR:-$VAULT_DIR/lab-certs}/vault-client.key" \
      "$url" 2>/dev/null || health_ok=0
  else
    url="http://127.0.0.1:${port}/v1/health"
    curl -fsS --max-time 5 -o /dev/null "$url" 2>/dev/null || health_ok=0
  fi

  if [[ "$health_ok" -eq 1 ]]; then
    echo "[+] $label (:${port}) health OK"
  else
    echo "[!] $label (:${port}) health FAILED" >&2
    all_ok=1
  fi
}

check_vault_running kerosene-vault-1
check_vault_running kerosene-vault-2
check_vault_running kerosene-vault-3

check_vault_health 7701 "vault-1"
check_vault_health 7702 "vault-2"
check_vault_health 7703 "vault-3"

# Quorum: at least 2 of 3 must respond
responded=0
for port in 7701 7702 7703; do
  local url
  if [[ "$AUTH_MODE" == "mtls" || "$AUTH_MODE" == "mutual_tls" ]]; then
    url="https://127.0.0.1:${port}/v1/health"
    curl -fsS --max-time 5 --cacert "${KEROSENE_VAULT_MESH_CERTS_DIR:-$VAULT_DIR/lab-certs}/ca.crt" \
      --cert "${KEROSENE_VAULT_MESH_CERTS_DIR:-$VAULT_DIR/lab-certs}/vault-client.crt" \
      --key "${KEROSENE_VAULT_MESH_CERTS_DIR:-$VAULT_DIR/lab-certs}/vault-client.key" \
      "$url" >/dev/null 2>&1 && responded=$((responded + 1))
  else
    url="http://127.0.0.1:${port}/v1/health"
    curl -fsS --max-time 5 "$url" >/dev/null 2>&1 && responded=$((responded + 1))
  fi
done

if [[ "$responded" -ge 2 ]]; then
  echo "[+] Vault mesh quorum: $responded of 3 nodes responding"
else
  echo "[!] Vault mesh quorum FAILED: only $responded of 3 nodes responding" >&2
  all_ok=1
fi

if [[ "$all_ok" -eq 1 ]]; then
  echo "[!] Vault mesh health check completed with errors" >&2
  exit 1
fi
echo "[+] Vault mesh health check passed"
