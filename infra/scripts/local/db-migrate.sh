#!/usr/bin/env bash
set -euo pipefail

LOCAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=infra/scripts/common.sh
source "$LOCAL_SCRIPT_DIR/../common.sh"

MIGRATION_FILE="$BACKEND_DIR/src/main/resources/db/migration.sql"
VERSIONED_MIGRATIONS_DIR="$BACKEND_DIR/src/main/resources/db/migration"
DB_WAIT_TIMEOUT_SECONDS=90
DB_SERVICES=()
FORCE_MIGRATIONS="${KEROSENE_FORCE_MIGRATIONS:-0}"
VERBOSE_MIGRATIONS="${KEROSENE_MIGRATION_VERBOSE:-0}"
MIGRATION_STATE_TABLE="kerosene_local_schema_migrations"

usage() {
  cat <<'EOF'
Usage: infra/scripts/local/db-migrate.sh [options] [db-service...]

Applies backend/kerosene/src/main/resources/db/migration.sql and incremental
versioned migrations to running local PostgreSQL services. If no services are
provided, it targets db-wvo, db-iw5, db-ltv.

Options:
  --force       Re-run every migration and update the local checksum cache.
  --verbose     Print successful psql output.
  -h, --help    Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      FORCE_MIGRATIONS=1
      ;;
    --verbose)
      VERBOSE_MIGRATIONS=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      DB_SERVICES+=("$1")
      ;;
  esac
  shift
done

if [[ ${#DB_SERVICES[@]} -eq 0 ]]; then
  DB_SERVICES=(db-wvo db-iw5 db-ltv)
fi

require_file "$MIGRATION_FILE"
require_file "$VERSIONED_MIGRATIONS_DIR/V3__wallet_destination_hash_index.sql"
require_docker

case "$FORCE_MIGRATIONS" in 0|1) ;; *) fail "KEROSENE_FORCE_MIGRATIONS must be 0 or 1." ;; esac
case "$VERBOSE_MIGRATIONS" in 0|1) ;; *) fail "KEROSENE_MIGRATION_VERBOSE must be 0 or 1." ;; esac

versioned_migration_files() {
  find "$VERSIONED_MIGRATIONS_DIR" -maxdepth 1 -type f -name 'V*.sql' \
    | sort -V \
    | awk -F/ '
        {
          filename = $NF
          if (filename !~ /^V[0-9_]+__/) {
            next
          }
          version = filename
          sub(/^V/, "", version)
          sub(/__.*/, "", version)
          if ((version + 0) >= 3) {
            print
          }
        }
      '
}

service_is_running() {
  local service="$1"
  compose ps --services --status running | grep -Fxq "$service"
}

db_accepts_target_credentials() {
  local service="$1"
  compose exec -T "$service" sh -lc \
    'PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "${POSTGRES_DB:-kerosene}" -tAc "SELECT 1"' \
    >/dev/null 2>&1
}

wait_for_db_service() {
  local service="$1"
  local deadline now
  deadline=$(( $(date +%s) + DB_WAIT_TIMEOUT_SECONDS ))

  while true; do
    if db_accepts_target_credentials "$service"; then
      return 0
    fi

    now=$(date +%s)
    if (( now >= deadline )); then
      return 1
    fi

    sleep 2
  done
}

set_temporary_local_trust() {
  local service="$1"
  compose exec -T -u root "$service" sh -lc '
    set -eu
    backup="/tmp/pg_hba.conf.kerosene-local-repair"
    cp "$PGDATA/pg_hba.conf" "$backup"
    {
      printf "%s\n" "local all all trust"
      printf "%s\n" "host all all 0.0.0.0/0 scram-sha-256"
    } > "$PGDATA/pg_hba.conf"
    chown postgres:postgres "$PGDATA/pg_hba.conf"
    kill -HUP 1
  '
}

restore_pg_hba() {
  local service="$1"
  compose exec -T -u root "$service" sh -lc '
    set -eu
    backup="/tmp/pg_hba.conf.kerosene-local-repair"
    if [ -f "$backup" ]; then
      cp "$backup" "$PGDATA/pg_hba.conf"
      chown postgres:postgres "$PGDATA/pg_hba.conf"
      rm -f "$backup"
      kill -HUP 1
    fi
  ' >/dev/null 2>&1 || true
}

discover_repair_users() {
  local service="$1"
  {
    printf '%s\n' "${POSTGRES_USER:-}" api_system kerosene_admin
    compose exec -T -u root "$service" sh -lc \
      'grep -aEo "[A-Za-z_][A-Za-z0-9_]{2,}" "$PGDATA/global/1260" 2>/dev/null | grep -Ev "^(pg_|SCRAM$|SHA$)" | sort -u' \
      2>/dev/null || true
  } | awk 'NF && !seen[$0]++'
}

can_connect_as_repair_user() {
  local service="$1"
  local candidate="$2"
  compose exec -T -u postgres -e KEROSENE_DB_REPAIR_USER="$candidate" "$service" sh -lc \
    'psql -v ON_ERROR_STOP=1 -U "$KEROSENE_DB_REPAIR_USER" -d postgres -tAc "SELECT 1"' \
    >/dev/null 2>&1
}

repair_db_credentials_if_needed() {
  local service="$1"
  local repair_user=""
  local candidate

  if db_accepts_target_credentials "$service"; then
    return 0
  fi

  warn "$service is running, but the configured POSTGRES_USER cannot authenticate. Attempting local role repair for persisted volumes."
  set_temporary_local_trust "$service"

  for candidate in $(discover_repair_users "$service"); do
    if can_connect_as_repair_user "$service" "$candidate"; then
      repair_user="$candidate"
      break
    fi
  done

  if [[ -z "$repair_user" ]]; then
    restore_pg_hba "$service"
    fail "Could not find an existing local PostgreSQL superuser in $service for role repair."
  fi

  if ! compose exec -T -u postgres -e KEROSENE_DB_REPAIR_USER="$repair_user" "$service" sh -lc '
    set -eu
    target_user="${POSTGRES_USER:-}"
    target_db="${POSTGRES_DB:-kerosene}"
    case "$target_user" in ""|*[!A-Za-z0-9_]*)
      echo "Invalid POSTGRES_USER for local repair." >&2
      exit 1
      ;;
    esac
    case "$target_db" in ""|*[!A-Za-z0-9_]*)
      echo "Invalid POSTGRES_DB for local repair." >&2
      exit 1
      ;;
    esac

    password_sql=$(printf "%s" "${POSTGRES_PASSWORD:-}" | sed "s/'\''/'\'''\''/g")
    psql -v ON_ERROR_STOP=1 -U "$KEROSENE_DB_REPAIR_USER" -d postgres -c "DO \$\$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '\''${target_user}'\'') THEN ALTER ROLE ${target_user} WITH LOGIN SUPERUSER PASSWORD '\''${password_sql}'\''; ELSE CREATE ROLE ${target_user} WITH LOGIN SUPERUSER PASSWORD '\''${password_sql}'\''; END IF; END \$\$;" >/dev/null
    if ! psql -v ON_ERROR_STOP=1 -U "$KEROSENE_DB_REPAIR_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '\''${target_db}'\''" | grep -qx 1; then
      psql -v ON_ERROR_STOP=1 -U "$KEROSENE_DB_REPAIR_USER" -d postgres -c "CREATE DATABASE ${target_db} OWNER ${target_user}" >/dev/null
    fi
    psql -v ON_ERROR_STOP=1 -U "$KEROSENE_DB_REPAIR_USER" -d postgres -c "ALTER DATABASE ${target_db} OWNER TO ${target_user}" >/dev/null
    psql -v ON_ERROR_STOP=1 -U "$KEROSENE_DB_REPAIR_USER" -d postgres -c "GRANT ALL PRIVILEGES ON DATABASE ${target_db} TO ${target_user}" >/dev/null
  '; then
    restore_pg_hba "$service"
    fail "Failed to repair PostgreSQL role for $service."
  fi

  restore_pg_hba "$service"
  db_accepts_target_credentials "$service" || fail "PostgreSQL role repair completed, but $service still rejects configured credentials."
  info "Repaired local PostgreSQL role for $service."
}

apply_sql_file() {
  local service="$1"
  local file="$2"
  local output status
  if output="$(compose exec -T "$service" sh -lc \
    'PGOPTIONS="-c client_min_messages=warning" PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "${POSTGRES_DB:-kerosene}" -f -' \
    < "$file" 2>&1)"; then
    if [[ "$VERBOSE_MIGRATIONS" -eq 1 && -n "$output" ]]; then
      printf '%s\n' "$output"
    fi
    return 0
  fi

  status=$?
  printf '%s\n' "$output" >&2
  return "$status"
}

run_sql() {
  local service="$1"
  local sql="$2"
  local output status
  if output="$(compose exec -T -e KEROSENE_SQL="$sql" "$service" sh -lc \
    'PGOPTIONS="-c client_min_messages=warning" PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "${POSTGRES_DB:-kerosene}" -c "$KEROSENE_SQL"' \
    2>&1)"; then
    if [[ "$VERBOSE_MIGRATIONS" -eq 1 && -n "$output" ]]; then
      printf '%s\n' "$output"
    fi
    return 0
  fi

  status=$?
  printf '%s\n' "$output" >&2
  return "$status"
}

query_scalar() {
  local service="$1"
  local sql="$2"
  compose exec -T -e KEROSENE_SQL="$sql" "$service" sh -lc \
    'PGOPTIONS="-c client_min_messages=warning" PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -X -q -tA -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "${POSTGRES_DB:-kerosene}" -c "$KEROSENE_SQL"' \
    2>/dev/null | tr -d '\r' | tail -n 1
}

sql_escape_literal() {
  printf "%s" "$1" | sed "s/'/''/g"
}

checksum_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  else
    fail "sha256sum or shasum is required for migration checksum caching."
  fi
}

legacy_financial_migration() {
  local name="$1"
  case "$name" in
    migration.sql|\
    V3__wallet_destination_hash_index.sql|\
    V4__payment_intent_external_states.sql|\
    V5__payment_execution_reconciliation_states.sql|\
    V6__payment_execution_claims.sql|\
    V7__external_provider_outbox_claims.sql|\
    V8__financial_reconciliation_resolution_fields.sql|\
    V9__treasury_payout_requests.sql|\
    V10__lightning_invoice_inbound_guards.sql|\
    V11__internal_card_surface_metadata.sql|\
    V16__add_performance_indexes.sql|\
    V17__financial_integrity_constraints.sql)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

kfe_only_without_legacy_financial_schema() {
  local service="$1"
  local legacy_wallets kfe_wallets
  legacy_wallets="$(query_scalar "$service" "SELECT to_regclass('financial.wallets') IS NOT NULL;")"
  kfe_wallets="$(query_scalar "$service" "SELECT to_regclass('financial.wallets_core') IS NOT NULL;")"

  [[ "$legacy_wallets" == "f" && "$kfe_wallets" == "t" ]]
}

should_skip_legacy_financial_migration() {
  local service="$1"
  local name="$2"

  legacy_financial_migration "$name" || return 1
  kfe_only_without_legacy_financial_schema "$service"
}

ensure_migration_state_table() {
  local service="$1"
  run_sql "$service" "
    CREATE TABLE IF NOT EXISTS ${MIGRATION_STATE_TABLE} (
      name text PRIMARY KEY,
      checksum text NOT NULL,
      applied_at timestamptz NOT NULL DEFAULT now()
    );
  " >/dev/null
}

migration_is_current() {
  local service="$1"
  local name="$2"
  local checksum="$3"
  local safe_name existing
  safe_name="$(sql_escape_literal "$name")"
  existing="$(query_scalar "$service" "SELECT checksum FROM ${MIGRATION_STATE_TABLE} WHERE name = '$safe_name';")"
  [[ "$existing" == "$checksum" ]]
}

mark_migration_current() {
  local service="$1"
  local name="$2"
  local checksum="$3"
  local safe_name safe_checksum
  safe_name="$(sql_escape_literal "$name")"
  safe_checksum="$(sql_escape_literal "$checksum")"
  run_sql "$service" "
    INSERT INTO ${MIGRATION_STATE_TABLE} (name, checksum, applied_at)
    VALUES ('$safe_name', '$safe_checksum', now())
    ON CONFLICT (name)
    DO UPDATE SET checksum = EXCLUDED.checksum, applied_at = now();
  " >/dev/null
}

apply_migrations() {
  local service="$1"
  local migration name checksum applied=0 skipped=0
  local migrations=("$MIGRATION_FILE")

  while IFS= read -r migration; do
    [[ -n "$migration" ]] || continue
    migrations+=("$migration")
  done < <(versioned_migration_files)

  ensure_migration_state_table "$service"
  for migration in "${migrations[@]}"; do
    name="$(basename "$migration")"
    checksum="$(checksum_file "$migration")"
    if [[ "$FORCE_MIGRATIONS" -ne 1 ]] && migration_is_current "$service" "$name" "$checksum"; then
      skipped=$((skipped + 1))
      continue
    fi
    if should_skip_legacy_financial_migration "$service" "$name"; then
      warn "Skipping legacy financial migration $name for KFE-only schema on $service."
      mark_migration_current "$service" "$name" "$checksum"
      skipped=$((skipped + 1))
      continue
    fi

    info "Applying schema migration $name to $service..."
    apply_sql_file "$service" "$migration"
    mark_migration_current "$service" "$name" "$checksum"
    applied=$((applied + 1))
  done

  info "Schema for $service is up to date (${applied} applied, ${skipped} unchanged)."
}

for service in "${DB_SERVICES[@]}"; do
  if ! service_is_running "$service"; then
    warn "Skipping $service because it is not running."
    continue
  fi

  info "Waiting for $service to accept connections..."
  repair_db_credentials_if_needed "$service"
  wait_for_db_service "$service" || fail "Timed out waiting for $service to become ready."
  apply_migrations "$service"
done

info "Local database migrations completed."
