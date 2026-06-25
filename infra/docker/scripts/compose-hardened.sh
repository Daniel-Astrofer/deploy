#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE_FILE="${KEROSENE_HARDENED_COMPOSE_FILE:-$ROOT/infra/docker/compose/hardened.compose.yaml}"
ENV_FILE="${KEROSENE_ENV_FILE:-$ROOT/backend/kerosene/.env}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-kerosene-hardened}"

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
elif command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(podman compose)
else
  echo "Docker Compose was not found. Install 'docker compose' or 'docker-compose'." >&2
  exit 1
fi

[[ -f "$COMPOSE_FILE" ]] || { echo "Compose file not found: $COMPOSE_FILE" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Env file not found: $ENV_FILE" >&2; exit 1; }

COMPOSE_PROJECT_NAME="$PROJECT_NAME" "${COMPOSE_CMD[@]}" \
  --project-name "$PROJECT_NAME" \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  "$@"
