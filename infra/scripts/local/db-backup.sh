#!/usr/bin/env bash
set -euo pipefail

LOCAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=infra/scripts/common.sh
source "$LOCAL_SCRIPT_DIR/../common.sh"

BACKUP_ROOT="${BACKUP_ROOT:-$REPO_ROOT/backups/local-db}"
TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
OUTPUT_DIR="$BACKUP_ROOT/$TIMESTAMP"
INCLUDE_POSTGRES=1
INCLUDE_REDIS=1
POSTGRES_SERVICES=(db-is db-ch db-sg)
REDIS_SERVICES=(redis-is redis-ch redis-sg)

usage() {
  cat <<'EOF'
Usage: infra/scripts/local/db-backup.sh [options]

Creates local backups from running Docker Compose data services:
  - PostgreSQL shards: db-is, db-ch, db-sg as custom-format pg_dump files.
  - Redis shards: redis-is, redis-ch, redis-sg as RDB snapshots.

Options:
  --output-dir DIR  Write this backup run to DIR instead of backups/local-db/<timestamp>.
  --postgres-only   Back up only PostgreSQL.
  --redis-only      Back up only Redis.
  -h, --help        Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      [[ $# -ge 2 ]] || fail "--output-dir requires a value."
      OUTPUT_DIR="$2"
      shift
      ;;
    --postgres-only)
      INCLUDE_POSTGRES=1
      INCLUDE_REDIS=0
      ;;
    --redis-only)
      INCLUDE_POSTGRES=0
      INCLUDE_REDIS=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
  shift
done

service_is_running() {
  local service="$1"
  compose ps --services --status running | grep -Fxq "$service"
}

record_checksum() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" >> "$OUTPUT_DIR/SHA256SUMS"
  fi
}

backup_postgres_service() {
  local service="$1"
  local file="$OUTPUT_DIR/postgres-${service}.dump"

  if ! service_is_running "$service"; then
    warn "Skipping $service because it is not running."
    return 1
  fi

  info "Backing up PostgreSQL service $service."
  compose exec -T "$service" sh -lc \
    'PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump -Fc --no-owner --no-acl -U "$POSTGRES_USER" -d "${POSTGRES_DB:-kerosene}"' \
    > "$file"
  [[ -s "$file" ]] || fail "Backup file is empty: $file"
  record_checksum "$file"
}

backup_redis_service() {
  local service="$1"
  local file="$OUTPUT_DIR/redis-${service}.rdb"

  if ! service_is_running "$service"; then
    warn "Skipping $service because it is not running."
    return 1
  fi

  info "Backing up Redis service $service."
  compose exec -T "$service" sh -lc \
    'redis-cli --no-auth-warning -a "${REDIS_PASSWORD:-}" SAVE >/dev/null && cat /data/dump.rdb' \
    > "$file"
  [[ -s "$file" ]] || fail "Backup file is empty: $file"
  record_checksum "$file"
}

write_manifest() {
  {
    printf 'created_at=%s\n' "$TIMESTAMP"
    printf 'repo_root=%s\n' "$REPO_ROOT"
    printf 'compose_project=%s\n' "$COMPOSE_PROJECT_NAME"
    printf 'postgres_services=%s\n' "${POSTGRES_SERVICES[*]}"
    printf 'redis_services=%s\n' "${REDIS_SERVICES[*]}"
  } > "$OUTPUT_DIR/MANIFEST.txt"
}

require_docker
mkdir -p "$OUTPUT_DIR"
chmod 700 "$OUTPUT_DIR" 2>/dev/null || true
: > "$OUTPUT_DIR/SHA256SUMS"
write_manifest

created=0

if [[ "$INCLUDE_POSTGRES" -eq 1 ]]; then
  for service in "${POSTGRES_SERVICES[@]}"; do
    backup_postgres_service "$service" && created=1 || true
  done
fi

if [[ "$INCLUDE_REDIS" -eq 1 ]]; then
  for service in "${REDIS_SERVICES[@]}"; do
    backup_redis_service "$service" && created=1 || true
  done
fi

if [[ "$created" -ne 1 ]]; then
  fail "No database services were running; no backups were created."
fi

info "Backup completed at $OUTPUT_DIR"
