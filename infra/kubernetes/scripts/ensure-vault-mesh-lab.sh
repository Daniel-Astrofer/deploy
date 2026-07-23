#!/usr/bin/env bash
set -euo pipefail

# Start the lab vault mesh (testnet3 + static_token) used by local-full / deploy.sh.
# Canonical definition: infra/docker/compose/vault-mesh-lab.compose.yaml
# Staging/prod-like mTLS mesh is NOT the default here — see vault-mesh-staging.compose.yaml
# and set KEROSENE_VAULT_MESH_PROFILE=staging only when certs are available.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

COMPOSE_FILE="${KEROSENE_VAULT_MESH_COMPOSE_FILE:-$REPO_ROOT/infra/docker/compose/vault-mesh-lab.compose.yaml}"
PROFILE="${KEROSENE_VAULT_MESH_PROFILE:-lab}"

usage() {
  cat <<'USAGE'
Usage: infra/kubernetes/scripts/ensure-vault-mesh-lab.sh [--print-host-ip]

Starts vault-mesh-lab (testnet3) via Docker Compose for deploy.sh / local-full.

Environment:
  KEROSENE_VAULT_MESH_COMPOSE_FILE  Override compose file (default: vault-mesh-lab)
  KEROSENE_VAULT_MESH_PROFILE       lab (default) | staging
  KEROSENE_VAULT_MESH_HOST_IP       Force host IP advertised to the cluster
  VAULT_API_TOKEN                   Must match kfe KFE_VAULTMESH_API_TOKEN
USAGE
}

PRINT_HOST_IP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --print-host-ip) PRINT_HOST_IP=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unsupported option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

if [[ "$PROFILE" == "staging" ]]; then
  COMPOSE_FILE="${KEROSENE_VAULT_MESH_COMPOSE_FILE:-$REPO_ROOT/infra/docker/compose/vault-mesh-staging.compose.yaml}"
  if [[ ! -d "${KEROSENE_VAULT_MESH_CERTS_DIR:-$REPO_ROOT/backend/kerosene-vault/certs}" ]]; then
    echo "[!] staging mesh profile needs mTLS certs; falling back to lab clear-token mesh." >&2
    echo "[!] Generate certs: bash backend/kerosene-vault/scripts/gen_lab_mtls_certs.sh" >&2
    COMPOSE_FILE="$REPO_ROOT/infra/docker/compose/vault-mesh-lab.compose.yaml"
    PROFILE=lab
  fi
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "[!] Vault mesh compose not found: $COMPOSE_FILE" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[!] Docker is required to start the vault mesh." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "[!] Docker daemon is not reachable." >&2
  exit 1
fi

resolve_host_ip() {
  if [[ -n "${KEROSENE_VAULT_MESH_HOST_IP:-}" ]]; then
    printf '%s\n' "$KEROSENE_VAULT_MESH_HOST_IP"
    return 0
  fi
  local gw
  for net in kind bridge; do
    gw="$(docker network inspect "$net" -f '{{range .IPAM.Config}}{{.Gateway}}{{println}}{{end}}' 2>/dev/null | head -n1 | tr -d '[:space:]' || true)"
    if [[ -n "$gw" ]]; then
      printf '%s\n' "$gw"
      return 0
    fi
  done
  # Last resort: host loopback is wrong from pods; fail closed with a clear error.
  echo "[!] Could not resolve a host gateway IP for vault mesh. Set KEROSENE_VAULT_MESH_HOST_IP." >&2
  return 1
}

HOST_IP="$(resolve_host_ip)"

if [[ "$PRINT_HOST_IP" -eq 1 ]]; then
  printf '%s\n' "$HOST_IP"
  exit 0
fi

echo "[*] Starting vault mesh ($PROFILE) from $COMPOSE_FILE" >&2
echo "[*] Host IP advertised to cluster for vault-1: $HOST_IP" >&2
docker compose -f "$COMPOSE_FILE" up -d --build >&2

# Wait briefly for vault-1 HTTP on the published lab port.
deadline=$(( $(date +%s) + 90 ))
while true; do
  if curl -fsS -o /dev/null "http://127.0.0.1:7701/v1/health" 2>/dev/null; then
    echo "[+] Vault mesh vault-1 is responding on :7701" >&2
    break
  fi
  if (( $(date +%s) >= deadline )); then
    echo "[!] vault-1 did not become ready on :7701 within 90s (continuing; kfe may retry)." >&2
    break
  fi
  sleep 2
done

printf '%s\n' "$HOST_IP"
