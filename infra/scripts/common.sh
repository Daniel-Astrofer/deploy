#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "Source this helper from another Deploy script." >&2
  exit 1
fi

set -euo pipefail

KEROSENE_INFRA_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$KEROSENE_INFRA_SCRIPTS_DIR/../.." && pwd)"
# shellcheck source=infra/scripts/polyrepo-env.sh
source "$REPO_ROOT/infra/scripts/polyrepo-env.sh"

ENV_FILE="${KEROSENE_ENV_FILE:-$BACKEND_DIR/.env}"

info() { echo "[infra] $*"; }
warn() { echo "[infra][warn] $*" >&2; }
fail() { echo "[infra][error] $*" >&2; exit 1; }

load_backend_env() {
  local line key value first last
  [[ -f "$ENV_FILE" ]] || fail "Environment file not found: $ENV_FILE"

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || fail "Invalid .env line: ${line%%=*}"

    key="${line%%=*}"
    value="${line#*=}"
    if [[ "${#value}" -ge 2 ]]; then
      first="${value:0:1}"
      last="${value: -1}"
      if { [[ "$first" == "'" && "$last" == "'" ]]; } ||
         { [[ "$first" == '"' && "$last" == '"' ]]; }; then
        value="${value:1:${#value}-2}"
      fi
    fi
    export "$key=$value"
  done < "$ENV_FILE"
}
