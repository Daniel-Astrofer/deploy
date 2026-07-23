#!/usr/bin/env bash
set -euo pipefail

# Starts the lab vault mesh (testnet3 + static_token) used by local-full / deploy.sh.
# Canonical definition: infra/docker/compose/vault-mesh-lab.compose.yaml
# Staging/prod-like mTLS mesh is NOT the default here — see vault-mesh-staging.compose.yaml
# and set KEROSENE_VAULT_MESH_PROFILE=staging only when certs are available.
#
# Optional Tor private mesh (distributed_wire, no dealer_lab):
#   KEROSENE_VAULT_MESH_PROFILE=tor
#   → infra/docker/compose/vault-mesh-tor.compose.yaml
# Default remains clearnet vault-mesh-lab (dealer_lab visualize). Tor does not
# publish vault host ports; kfe Endpoints bridge (:7701) is lab/staging only.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

COMPOSE_FILE="${KEROSENE_VAULT_MESH_COMPOSE_FILE:-$REPO_ROOT/infra/docker/compose/vault-mesh-lab.compose.yaml}"
PROFILE="${KEROSENE_VAULT_MESH_PROFILE:-lab}"
COMPOSE_DIR="$(cd "$(dirname "$COMPOSE_FILE")" && pwd)"

usage() {
  cat <<'USAGE'
Usage: infra/kubernetes/scripts/ensure-vault-mesh-lab.sh [--print-host-ip]

Starts vault mesh via Docker Compose for deploy.sh / local-full.

Environment:
  KEROSENE_VAULT_MESH_COMPOSE_FILE  Override compose file
  KEROSENE_VAULT_MESH_PROFILE       lab (default) | staging | tor
  KEROSENE_VAULT_MESH_HOST_IP       Force host IP advertised to the cluster
  VAULT_API_TOKEN                   Must match kfe KFE_VAULTMESH_API_TOKEN (lab/token)

Profiles:
  lab      vault-mesh-lab.compose.yaml — clearnet :7701–7703, dealer_lab (default)
  staging  vault-mesh-staging.compose.yaml — mTLS if certs exist; else falls back to lab
  tor      vault-mesh-tor.compose.yaml — real Tor HS + distributed_wire (no clearnet vault ports)
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

case "$PROFILE" in
  lab|"")
    PROFILE=lab
    COMPOSE_FILE="${KEROSENE_VAULT_MESH_COMPOSE_FILE:-$REPO_ROOT/infra/docker/compose/vault-mesh-lab.compose.yaml}"
    ;;
  staging)
    COMPOSE_FILE="${KEROSENE_VAULT_MESH_COMPOSE_FILE:-$REPO_ROOT/infra/docker/compose/vault-mesh-staging.compose.yaml}"
    if [[ ! -d "${KEROSENE_VAULT_MESH_CERTS_DIR:-$REPO_ROOT/backend/kerosene-vault/certs}" ]]; then
      echo "[!] staging mesh profile needs mTLS certs; falling back to lab clear-token mesh." >&2
      echo "[!] Generate certs: bash backend/kerosene-vault/scripts/gen_lab_mtls_certs.sh" >&2
      COMPOSE_FILE="$REPO_ROOT/infra/docker/compose/vault-mesh-lab.compose.yaml"
      PROFILE=lab
    fi
    ;;
  tor)
    COMPOSE_FILE="${KEROSENE_VAULT_MESH_COMPOSE_FILE:-$REPO_ROOT/infra/docker/compose/vault-mesh-tor.compose.yaml}"
    ;;
  *)
    echo "[!] Unknown KEROSENE_VAULT_MESH_PROFILE=$PROFILE (expected lab|staging|tor)" >&2
    exit 2
    ;;
esac

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

read_onion() {
  local container="$1"
  local onion
  onion="$(docker exec "$container" cat /var/lib/tor/kerosene_service/hostname 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -z "$onion" || "$onion" != *.onion ]]; then
    return 1
  fi
  printf '%s\n' "$onion"
}

wait_tor_onions() {
  local deadline=$(( $(date +%s) + 180 ))
  echo "[*] Waiting for Tor onion hostnames (vault-mesh-tor)" >&2
  while true; do
    if VAULT1_ONION="$(read_onion kerosene-vault-tor-1)" \
      && VAULT2_ONION="$(read_onion kerosene-vault-tor-2)" \
      && VAULT3_ONION="$(read_onion kerosene-vault-tor-3)"; then
      export VAULT1_ONION VAULT2_ONION VAULT3_ONION
      echo "[+] vault-1 onion: $VAULT1_ONION" >&2
      echo "[+] vault-2 onion: $VAULT2_ONION" >&2
      echo "[+] vault-3 onion: $VAULT3_ONION" >&2
      return 0
    fi
    if (( $(date +%s) >= deadline )); then
      echo "[!] Tor onions not ready within 180s" >&2
      docker compose -f "$COMPOSE_FILE" logs --tail=80 tor-1 tor-2 tor-3 >&2 || true
      return 1
    fi
    sleep 3
  done
}

start_tor_mesh() {
  echo "[*] Starting Tor sidecars from $COMPOSE_FILE" >&2
  pushd "$COMPOSE_DIR" >/dev/null
  docker compose -f "$COMPOSE_FILE" up -d --build tor-1 tor-2 tor-3 >&2
  wait_tor_onions
  echo "[*] Starting vaults with onion peer seeds (distributed_wire, no clearnet vault ports)" >&2
  docker compose -f "$COMPOSE_FILE" up -d --build vault-1 vault-2 vault-3 >&2
  popd >/dev/null

  # Best-effort health via host SOCKS (published 127.0.0.1:19051).
  local socks="127.0.0.1:19051"
  local token="${VAULT_API_TOKEN:-kerosene-vault-lab-only}"
  local auth_mode="${VAULT_AUTH_MODE:-static_token}"
  local deadline=$(( $(date +%s) + 180 ))
  local url
  if [[ "$auth_mode" == "mtls" || "$auth_mode" == "mutual_tls" ]]; then
    echo "[*] Tor mesh auth=mtls — skipping curl health (use lab_dkg_wire_tor.sh for full check)" >&2
    return 0
  fi
  url="http://${VAULT1_ONION}:7701/v1/health"
  echo "[*] Waiting for vault-1 health via Tor SOCKS $socks" >&2
  while true; do
    if curl -fsS --socks5-hostname "$socks" -H "X-Vault-Token: ${token}" -o /dev/null "$url" 2>/dev/null; then
      echo "[+] vault-1 responding via Tor onion" >&2
      break
    fi
    if (( $(date +%s) >= deadline )); then
      echo "[!] vault-1 Tor health not ready within 180s (continuing; DKG/scripts may retry)." >&2
      break
    fi
    sleep 5
  done
}

HOST_IP="$(resolve_host_ip)"

if [[ "$PRINT_HOST_IP" -eq 1 ]]; then
  printf '%s\n' "$HOST_IP"
  exit 0
fi

echo "[*] Starting vault mesh ($PROFILE) from $COMPOSE_FILE" >&2
echo "[*] Host IP advertised to cluster for vault-1: $HOST_IP" >&2

if [[ "$PROFILE" == "tor" ]]; then
  echo "[!] Tor profile: vault APIs are NOT published on host :7701–7703." >&2
  echo "[!] local-full kfe Endpoints bridge remains clearnet-lab; use this profile for Tor mesh / distributed_wire." >&2
  echo "[!] Docs: backend/kerosene-vault/docs/CEREMONY_TOR.md" >&2
  start_tor_mesh
else
  pushd "$COMPOSE_DIR" >/dev/null
  docker compose -f "$COMPOSE_FILE" up -d --build >&2
  popd >/dev/null

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
fi

printf '%s\n' "$HOST_IP"
