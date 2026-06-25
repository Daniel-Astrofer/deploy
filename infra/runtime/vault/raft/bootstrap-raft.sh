#!/bin/sh
set -eu

export VAULT_ADDR="${VAULT_ADDR:-https://vault-raft-1:8200}"
export VAULT_SKIP_VERIFY="${VAULT_SKIP_VERIFY:-true}"
BOOTSTRAP_DIR="${VAULT_BOOTSTRAP_DIR:-/vault/bootstrap}"
APP_READ_GID="${VAULT_APP_READ_GID:-65532}"
mkdir -p "$BOOTSTRAP_DIR"

wait_for_api() {
  node="$1"
  deadline=$(( $(date +%s) + 180 ))
  while :; do
    status_code=0
    timeout 15s vault status -address="$node" >/tmp/vault-status.txt 2>&1 || status_code=$?
    if [ "$status_code" -eq 0 ] || [ "$status_code" -eq 2 ]; then
      return 0
    fi
    if grep -Eq "Initialized[[:space:]]+false|Sealed[[:space:]]+true|Vault is sealed" /tmp/vault-status.txt 2>/dev/null; then
      return 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      cat /tmp/vault-status.txt >&2 || true
      return 1
    fi
    sleep 2
  done
}

wait_for_raft_leader() {
  deadline=$(( $(date +%s) + 180 ))
  while :; do
    for node in \
      "https://vault-raft-1:8200" \
      "https://vault-raft-2:8200" \
      "https://vault-raft-3:8200"; do
      if timeout 15s vault operator raft list-peers -address="$node" >/tmp/vault-raft-peers.txt 2>/tmp/vault-raft-leader.err; then
        export VAULT_ADDR="$node"
        return 0
      fi
    done

    if [ "$(date +%s)" -ge "$deadline" ]; then
      cat /tmp/vault-raft-leader.err >&2 || true
      return 1
    fi
    sleep 2
  done
}

wait_for_api "https://vault-raft-1:8200"
wait_for_api "https://vault-raft-2:8200"
wait_for_api "https://vault-raft-3:8200"

if [ ! -f "$BOOTSTRAP_DIR/root-token" ]; then
  vault operator init -key-shares=3 -key-threshold=2 > "$BOOTSTRAP_DIR/init.txt"
  grep '^Initial Root Token:' "$BOOTSTRAP_DIR/init.txt" | awk '{print $4}' > "$BOOTSTRAP_DIR/root-token"
  grep '^Unseal Key ' "$BOOTSTRAP_DIR/init.txt" | awk '{print $4}' > "$BOOTSTRAP_DIR/unseal-keys"
  chmod 600 "$BOOTSTRAP_DIR/root-token" "$BOOTSTRAP_DIR/unseal-keys" "$BOOTSTRAP_DIR/init.txt"
fi

unseal_node() {
  node="$1"
  sed -n '1,2p' "$BOOTSTRAP_DIR/unseal-keys" | while IFS= read -r key; do
    timeout 15s vault operator unseal -address="$node" "$key" >/dev/null
  done
}

unseal_node "https://vault-raft-1:8200"
export VAULT_TOKEN="$(cat "$BOOTSTRAP_DIR/root-token")"

join_node() {
  node="$1"
  timeout 15s vault operator raft join -address="$node" -leader-ca-cert=@/vault/certs/ca.pem "https://vault-raft-1:8200" >/dev/null || true
}

join_node "https://vault-raft-2:8200"
join_node "https://vault-raft-3:8200"
unseal_node "https://vault-raft-2:8200"
unseal_node "https://vault-raft-3:8200"

wait_for_raft_leader

timeout 15s vault policy write kerosene-raft-health - <<'POLICY'
path "sys/health" {
  capabilities = ["read"]
}

path "sys/leader" {
  capabilities = ["read"]
}

path "sys/storage/raft/configuration" {
  capabilities = ["read", "sudo"]
}
POLICY

timeout 15s vault token create \
  -policy=kerosene-raft-health \
  -orphan \
  -period=24h \
  -renewable=true \
  -field=token > "$BOOTSTRAP_DIR/app-health-token"

timeout 15s vault operator raft list-peers > "$BOOTSTRAP_DIR/raft-peers.txt"
timeout 15s vault status > "$BOOTSTRAP_DIR/status.txt"

chmod 750 "$BOOTSTRAP_DIR"
for file in "$BOOTSTRAP_DIR/app-health-token" "$BOOTSTRAP_DIR/raft-peers.txt" "$BOOTSTRAP_DIR/status.txt"; do
  if [ -f "$file" ]; then
    chgrp "$APP_READ_GID" "$file" 2>/dev/null || true
    chmod 440 "$file"
  fi
done
for file in "$BOOTSTRAP_DIR/root-token" "$BOOTSTRAP_DIR/unseal-keys" "$BOOTSTRAP_DIR/init.txt"; do
  if [ -f "$file" ]; then
    chown root:root "$file" 2>/dev/null || true
    chmod 600 "$file"
  fi
done
chgrp "$APP_READ_GID" "$BOOTSTRAP_DIR" 2>/dev/null || true

printf 'Vault Raft bootstrap complete\n'
