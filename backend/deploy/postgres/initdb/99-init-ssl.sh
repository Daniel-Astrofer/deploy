#!/bin/sh
set -e

echo "Configuring SSL and pg_hba for PostgreSQL..."

# pg_hba.conf — Kerosene auth policy:
# local: scram-sha-256 mandatory for local unix sockets
#
# Network access (host): 
#   If REQUIRE_MTLS=true -> Enforce full mTLS cert verification (Production)
#   If REQUIRE_MTLS=false -> Use scram-sha-256 password auth (Local Dev)
#
if [ "${REQUIRE_MTLS:-false}" = "true" ]; then
  echo "Enforcing strict mTLS policy for network access..."
  cat <<EOF > "$PGDATA/pg_hba.conf"
# TYPE        DATABASE    USER          ADDRESS          METHOD
local         all         all                            scram-sha-256
hostssl       kerosene    api_system    0.0.0.0/0        cert clientcert=verify-full
host          all         all           0.0.0.0/0        reject
EOF
else
  echo "[WARN] REQUIRE_MTLS=false detected. Enabling password-based network access for local dev."
  cat <<EOF > "$PGDATA/pg_hba.conf"
# TYPE        DATABASE    USER          ADDRESS          METHOD
local         all         all                            scram-sha-256
host          all         all           0.0.0.0/0        scram-sha-256
EOF
fi

echo "pg_hba.conf written successfully."
