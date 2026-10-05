#!/usr/bin/env bash
set -euo pipefail

DEPLOY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "$DEPLOY_ROOT/.." && pwd)"
STATE_ROOT="${KEROSENE_CELL_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/kerosene/production-cells}"
CONFIG_ROOT="${KEROSENE_CELL_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/kerosene}"
SYSTEMD_USER_ROOT="${KEROSENE_SYSTEMD_USER_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
KUBECONFIG_PATH="${KEROSENE_KUBECONFIG:-$CONFIG_ROOT/kubeconfig}"
NAMESPACE="${KEROSENE_NAMESPACE:-kerosene-local}"
CLUSTER_NAME="${KEROSENE_KIND_CLUSTER:-kerosene-local}"
VAULT_BIN="$WORKSPACE_ROOT/vault/target/release/kerosene-vault"
NODE_BIN="$WORKSPACE_ROOT/node/target/release/kerosene-node"
RAIL_OVERLAY="$DEPLOY_ROOT/infra/kubernetes/overlays/local-production-cells"
CERT_ROTATION_SCRIPT="$WORKSPACE_ROOT/vault/scripts/ceremony/rotate_mtls_certs.sh"
CERT_TTL_HOURS="${KEROSENE_CERT_TTL_HOURS:-24}"
CERT_RENEW_BEFORE_SECONDS="${KEROSENE_CERT_RENEW_BEFORE_SECONDS:-28800}"
CERT_ROTATION_LOCK_TIMEOUT_SECONDS="${KEROSENE_CERT_ROTATION_LOCK_TIMEOUT_SECONDS:-60}"
KFE_IMAGE="${KEROSENE_KFE_IMAGE:-kerosene/kfe-service:local-production-cells}"
KFE_DOCKERFILE="$DEPLOY_ROOT/infra/docker/images/kfe-service/Dockerfile"

die() { echo "[cells][error] $*" >&2; exit 1; }
info() { echo "[cells] $*"; }
need() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

onion() {
  tr -d '[:space:]' < "$STATE_ROOT/onion-$1/hostname"
}

systemd_quote() {
  local value="$1"
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || die "newline in systemd argument"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//%/%%}"
  printf '"%s"' "$value"
}

write_service_unit() {
  local name="$1" pid_file="$2" log_file="$3" env_file="$4"
  local unit="kerosene-$name.service" unit_path tmp dependency="" extra_after="" changed=0 pid arg executable
  local -a command
  shift 4
  command=("$@")
  executable="${command[0]}"
  if [[ "$executable" != */* ]]; then
    executable="$(command -v "$executable")" || die "executable not found: ${command[0]}"
    command[0]="$executable"
  fi
  unit_path="$SYSTEMD_USER_ROOT/$unit"

  case "$name" in
    vault-*)
      dependency="kerosene-tor-${name#vault-}.service"
      ;;
    node-*)
      dependency="kerosene-tor-${name#node-}.service"
      extra_after="kerosene-vault-${name#node-}.service"
      ;;
  esac

  mkdir -p "$SYSTEMD_USER_ROOT"
  chmod 700 "$SYSTEMD_USER_ROOT"
  tmp="$(mktemp "$SYSTEMD_USER_ROOT/.${unit}.XXXXXX")"
  {
    printf '[Unit]\nDescription=Kerosene local production cell %s\n' "$name"
    printf 'PartOf=kerosene-production-cells.target\n'
    printf 'Wants=network-online.target\nAfter=network-online.target'
    [[ -z "$dependency" ]] || printf ' %s' "$dependency"
    [[ -z "$extra_after" ]] || printf ' %s' "$extra_after"
    printf '\n'
    [[ -z "$dependency" ]] || printf 'Requires=%s\n' "$dependency"
    printf '\n[Service]\nType=simple\nUMask=0077\n'
    if [[ -n "$env_file" ]]; then
      [[ "$env_file" = /* && "$env_file" != *[[:space:]]* ]] || die "EnvironmentFile must be an absolute path without whitespace"
      printf 'EnvironmentFile=%s\n' "$env_file"
    fi
    printf 'ExecStart='
    for arg in "${command[@]}"; do printf '%s ' "$(systemd_quote "$arg")"; done
    printf '\nRestart=on-failure\nRestartSec=2\n'
    printf 'NoNewPrivileges=yes\nPrivateTmp=yes\nRestrictSUIDSGID=yes\n'
    printf 'ProtectKernelTunables=yes\nProtectKernelModules=yes\nProtectControlGroups=yes\n'
    printf 'StandardOutput=append:%s\nStandardError=append:%s\n' "$log_file" "$log_file"
    printf '\n[Install]\nWantedBy=kerosene-production-cells.target\n'
  } > "$tmp"
  chmod 600 "$tmp"

  if [[ -f "$unit_path" ]] && cmp -s "$tmp" "$unit_path"; then
    rm -f "$tmp"
  else
    mv -f "$tmp" "$unit_path"
    changed=1
  fi

  systemctl --user daemon-reload
  systemctl --user enable "$unit" >/dev/null 2>&1
  if systemctl --user is-active --quiet "$unit"; then
    if [[ "$changed" -eq 1 ]]; then
      systemctl --user restart "$unit"
    fi
  else
    systemctl --user start "$unit"
  fi
  sleep 0.4
  systemctl --user is-active --quiet "$unit" || die "$name failed to start; inspect $log_file"
  pid="$(systemctl --user show -p MainPID --value "$unit")"
  printf '%s\n' "$pid" > "$pid_file"
  info "$name running as enabled $unit (pid $pid)"
}

install_target_unit() {
  local unit_path="$SYSTEMD_USER_ROOT/kerosene-production-cells.target" tmp
  mkdir -p "$SYSTEMD_USER_ROOT"
  chmod 700 "$SYSTEMD_USER_ROOT"
  tmp="$(mktemp "$SYSTEMD_USER_ROOT/.kerosene-production-cells.target.XXXXXX")"
  {
    printf '[Unit]\nDescription=Kerosene local production cells\n'
    printf 'After=network-online.target\nWants=network-online.target\n'
    printf '\n[Install]\nWantedBy=default.target\n'
  } > "$tmp"
  chmod 600 "$tmp"
  if [[ -f "$unit_path" ]] && cmp -s "$tmp" "$unit_path"; then
    rm -f "$tmp"
  else
    mv -f "$tmp" "$unit_path"
  fi
  systemctl --user daemon-reload
  systemctl --user enable kerosene-production-cells.target >/dev/null 2>&1
}

install_certificate_timer() {
  local service_path="$SYSTEMD_USER_ROOT/kerosene-certificate-renewal.service"
  local timer_path="$SYSTEMD_USER_ROOT/kerosene-certificate-renewal.timer" tmp
  tmp="$(mktemp "$SYSTEMD_USER_ROOT/.kerosene-certificate-renewal.service.XXXXXX")"
  {
    printf '[Unit]\nDescription=Renew Kerosene short-lived workload certificates\n'
    printf 'After=kerosene-production-cells.target\n'
    printf '\n[Service]\nType=oneshot\nTimeoutStartSec=20min\nUMask=0077\n'
    printf 'ExecStart=%s %s\n' "$(systemd_quote "$DEPLOY_ROOT/infra/local-production-cells.sh")" "$(systemd_quote renew-certs-if-needed)"
    printf 'NoNewPrivileges=yes\nPrivateTmp=yes\nRestrictSUIDSGID=yes\n'
  } > "$tmp"
  chmod 600 "$tmp"
  if [[ -f "$service_path" ]] && cmp -s "$tmp" "$service_path"; then rm -f "$tmp"; else mv -f "$tmp" "$service_path"; fi

  tmp="$(mktemp "$SYSTEMD_USER_ROOT/.kerosene-certificate-renewal.timer.XXXXXX")"
  {
    printf '[Unit]\nDescription=Kerosene workload certificate renewal timer\n'
    printf 'PartOf=kerosene-production-cells.target\n'
    printf '\n[Timer]\nOnActiveSec=30s\nOnUnitActiveSec=4h\nPersistent=true\nUnit=kerosene-certificate-renewal.service\n'
    printf '\n[Install]\nWantedBy=kerosene-production-cells.target\n'
  } > "$tmp"
  chmod 600 "$tmp"
  if [[ -f "$timer_path" ]] && cmp -s "$tmp" "$timer_path"; then rm -f "$tmp"; else mv -f "$tmp" "$timer_path"; fi
  systemctl --user daemon-reload
  systemctl --user enable kerosene-certificate-renewal.timer >/dev/null 2>&1
  systemctl --user restart kerosene-certificate-renewal.timer
}

start_daemon() {
  local name="$1" pid_file="$2" log_file="$3"
  shift 3
  write_service_unit "$name" "$pid_file" "$log_file" "" "$@"
}

start_daemon_with_env_file() {
  local name="$1" pid_file="$2" log_file="$3" env_file="$4" executable="$5"
  write_service_unit "$name" "$pid_file" "$log_file" "$env_file" "$executable"
}

require_state() {
  [[ -f "$STATE_ROOT/ceremony-certs/ca.crt" ]] || die "persistent ceremony state missing under $STATE_ROOT"
  [[ -f "$STATE_ROOT/node/genesis-trust.json" ]] || die "Node genesis trust is missing"
  for i in 1 2 3; do
    [[ -f "$STATE_ROOT/onion-$i/hostname" ]] || die "onion identity $i is missing"
    [[ -f "$STATE_ROOT/secrets/vault-$i-passphrase" ]] || die "Vault passphrase $i is missing"
    [[ -f "$STATE_ROOT/node/member-$i/identity.key" ]] || die "Node identity $i is missing"
  done
}

ensure_kubernetes() {
  need docker
  need kubectl
  mkdir -p "$CONFIG_ROOT"
  chmod 700 "$CONFIG_ROOT"
  if ! docker inspect "${CLUSTER_NAME}-control-plane" >/dev/null 2>&1; then
    die "kind cluster $CLUSTER_NAME does not exist"
  fi
  if [[ "$(docker inspect -f '{{.State.Running}}' "${CLUSTER_NAME}-control-plane")" != true ]]; then
    docker start "${CLUSTER_NAME}-control-plane" >/dev/null
  fi
  if [[ ! -s "$KUBECONFIG_PATH" ]]; then
    need kind
    kind get kubeconfig --name "$CLUSTER_NAME" > "$KUBECONFIG_PATH"
    chmod 600 "$KUBECONFIG_PATH"
  fi
  kubectl --kubeconfig "$KUBECONFIG_PATH" get namespace "$NAMESPACE" >/dev/null
}

ensure_rail_secrets() {
  local secret_dir="$STATE_ROOT/secrets/rail"
  for name in bitcoin-read-token bitcoin-write-token bitcoin-admin-token lightning-read-token lightning-write-token lightning-admin-token; do
    [[ -s "$secret_dir/$name" ]] || die "persistent Rail credential missing: $name"
  done
  [[ -s "$secret_dir/tls.crt" && -s "$secret_dir/tls.key" ]] || die "persistent Rail TLS identity is missing"
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" create secret generic rail-runtime-credentials \
    --from-file=bitcoin-read-token="$secret_dir/bitcoin-read-token" \
    --from-file=bitcoin-write-token="$secret_dir/bitcoin-write-token" \
    --from-file=bitcoin-admin-token="$secret_dir/bitcoin-admin-token" \
    --from-file=lightning-read-token="$secret_dir/lightning-read-token" \
    --from-file=lightning-write-token="$secret_dir/lightning-write-token" \
    --from-file=lightning-admin-token="$secret_dir/lightning-admin-token" \
    --dry-run=client -o yaml | kubectl --kubeconfig "$KUBECONFIG_PATH" apply -f - >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" create secret tls rail-runtime-tls \
    --cert="$secret_dir/tls.crt" --key="$secret_dir/tls.key" \
    --dry-run=client -o yaml | kubectl --kubeconfig "$KUBECONFIG_PATH" apply -f - >/dev/null
}

ensure_vault_client_secret() {
  local certs="$STATE_ROOT/ceremony-certs" combined_truststore
  for name in ca.crt kfe/client.crt kfe/client.key kfe-client.p12 truststore.p12 lnd-tls.cert; do
    [[ -s "$certs/$name" ]] || die "persistent TLS material missing: $name"
  done
  need keytool
  combined_truststore="$certs/kfe-truststore.p12"
  install -m 600 "$certs/truststore.p12" "$combined_truststore"
  keytool -delete -alias lnd-local -keystore "$combined_truststore" \
    -storepass changeit -storetype PKCS12 >/dev/null 2>&1 || true
  keytool -importcert -noprompt -alias lnd-local -file "$certs/lnd-tls.cert" \
    -keystore "$combined_truststore" -storepass changeit -storetype PKCS12 >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" create secret generic vault-client-mtls \
    --from-file=ca.crt="$certs/ca.crt" \
    --from-file=client.crt="$certs/kfe/client.crt" \
    --from-file=client.key="$certs/kfe/client.key" \
    --from-file=keystore.p12="$certs/kfe-client.p12" \
    --from-file=truststore.p12="$combined_truststore" \
    --from-file=lnd-tls.cert="$certs/lnd-tls.cert" \
    --dry-run=client -o yaml | kubectl --kubeconfig "$KUBECONFIG_PATH" apply -f - >/dev/null
}

build_kfe_image() {
  need docker
  need kind
  [[ -f "$KFE_DOCKERFILE" ]] || die "KFE Dockerfile missing: $KFE_DOCKERFILE"
  info "building $KFE_IMAGE from the local KFE, contracts and shared sources"
  docker build \
    --build-context contracts="$WORKSPACE_ROOT/contracts" \
    --build-context shared="$WORKSPACE_ROOT/shared" \
    --build-context deploy="$DEPLOY_ROOT" \
    --file "$KFE_DOCKERFILE" \
    --tag "$KFE_IMAGE" \
    "$WORKSPACE_ROOT/kfe"
  kind load docker-image --name "$CLUSTER_NAME" "$KFE_IMAGE"
}

ensure_kfe_image() {
  need kind
  docker image inspect "$KFE_IMAGE" >/dev/null 2>&1 || \
    die "KFE image is missing; run: $0 rebuild-kfe"
  kind load docker-image --name "$CLUSTER_NAME" "$KFE_IMAGE" >/dev/null
}

ensure_rail_images() {
  need docker
  need kind
  local image
  for image in kerosene/bitcoin-rail:local kerosene/lightning-rail:local; do
    docker image inspect "$image" >/dev/null 2>&1 || \
      die "Rail image is missing: $image; build it with infra/docker/build-image.sh"
    kind load docker-image --name "$CLUSTER_NAME" "$image" >/dev/null
  done
}

wait_for_financial_dependencies() {
  local timeout="${KEROSENE_DEPENDENCY_READY_TIMEOUT:-30m}"
  info "waiting up to $timeout for Bitcoin Core, PostgreSQL, Redis and LND readiness"
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" wait \
    --for=condition=available \
    deployment/local-bitcoin deployment/local-redis deployment/local-lnd \
    --timeout="$timeout" >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" rollout status \
    statefulset/local-postgres --timeout="$timeout" >/dev/null
}

configure_kfe_runtime() {
  local i context constitution_hash="" member_hash vault_urls node_url patch
  need curl
  need jq
  for i in 1 2 3; do
    context="$(curl --fail --silent --show-error --max-time 10 \
      --cacert "$STATE_ROOT/ceremony-certs/ca.crt" \
      --cert "$STATE_ROOT/ceremony-certs/kfe/client.crt" \
      --key "$STATE_ROOT/ceremony-certs/kfe/client.key" \
      "https://127.0.0.1:$((7700 + i))/v1/financial-quorum/context")" || \
      die "cannot read financial constitution from vault-$i"
    member_hash="$(jq -er '.constitution_hash | select(type == "string" and length == 64)' <<<"$context")" || \
      die "vault-$i returned an invalid financial constitution"
    if [[ -z "$constitution_hash" ]]; then
      constitution_hash="$member_hash"
    elif [[ "$constitution_hash" != "$member_hash" ]]; then
      die "Vault financial constitutions disagree"
    fi
  done

  vault_urls="https://$(onion 1):7701,https://$(onion 2):7701,https://$(onion 3):7701"
  node_url="https://$(onion 1):8800"
  patch="$(jq -cn \
    --arg vault_base "https://$(onion 1):7701" \
    --arg vault_urls "$vault_urls" \
    --arg node_url "$node_url" \
    --arg constitution_hash "$constitution_hash" \
    '{data:{
      SPRING_PROFILES_ACTIVE:"production,kfe,docker",
      SPRING_CONFIG_ADDITIONAL_LOCATION:"optional:classpath:kfe-service-vaultmesh-testnet3.properties",
      BITCOIN_NETWORK:"testnet3",
      BITCOIN_RPC_URL:"http://bitcoin-core:18332",
      BITCOIN_RPC_PRUNED_REQUIRED:"false",
      BITCOIN_ZMQ_RAWTX:"tcp://bitcoin-core:28333",
      BITCOIN_ZMQ_HASHBLOCK:"tcp://bitcoin-core:28332",
      KFE_VAULTMESH_ENABLED:"true",
      KFE_VAULTMESH_MESH_ONLY:"true",
      KFE_VAULTMESH_BASE_URL:$vault_base,
      KFE_VAULTMESH_URLS:$vault_urls,
      KFE_VAULTMESH_TRANSPORT:"tor",
      KFE_VAULTMESH_SOCKS_HOST:"tor-onion",
      KFE_VAULTMESH_SOCKS_PORT:"9050",
      KFE_VAULTMESH_CONNECT_TIMEOUT_MS:"10000",
      KFE_VAULTMESH_READ_TIMEOUT_MS:"20000",
      KFE_VAULTMESH_REQUIRE_MTLS:"true",
      KFE_VAULTMESH_TLS_ENABLED:"true",
      KFE_VAULTMESH_TLS_CERT_PATH:"/certs/client.crt",
      KFE_VAULTMESH_TLS_KEY_PATH:"/certs/client.key",
      KFE_VAULTMESH_TLS_CA_PATH:"/certs/ca.crt",
      KFE_VAULTMESH_TLS_KEYSTORE_PATH:"/certs/keystore.p12",
      KFE_VAULTMESH_TLS_KEYSTORE_PASSWORD:"changeit",
      KFE_VAULTMESH_TLS_TRUSTSTORE_PATH:"/certs/truststore.p12",
      KFE_VAULTMESH_TLS_TRUSTSTORE_PASSWORD:"changeit",
      KFE_VAULTMESH_CONSTITUTION_HASH:$constitution_hash,
      VAULT_MESH_CONSTITUTION_HASH:$constitution_hash,
      KFE_VAULTMESH_DAY_ROTATION_ENABLED:"true",
      KFE_VAULTMESH_DAY_ROTATION_FIXED_DELAY_MS:"900000",
      KFE_VAULTMESH_DAY_ROTATION_INITIAL_DELAY_MS:"60000",
      KFE_VAULTMESH_READINESS_FIXED_DELAY_MS:"60000",
      KFE_VAULTMESH_READINESS_INITIAL_DELAY_MS:"5000",
      KFE_VAULTMESH_READINESS_STALE_AFTER_MS:"180000",
      KFE_KEROSENE_NODE_ENABLED:"true",
      KFE_KEROSENE_NODE_BASE_URL:$node_url,
      KFE_KEROSENE_NODE_NETWORK_ID:"kerosene-testnet3",
      KFE_KEROSENE_NODE_TRANSPORT:"tor",
      KFE_KEROSENE_NODE_SOCKS_HOST:"tor-onion",
      KFE_KEROSENE_NODE_SOCKS_PORT:"9050",
      KFE_KEROSENE_NODE_TLS_CERT_PATH:"/certs/client.crt",
      KFE_KEROSENE_NODE_TLS_KEY_PATH:"/certs/client.key",
      KFE_KEROSENE_NODE_TLS_CA_PATH:"/certs/ca.crt",
      KFE_KEROSENE_NODE_CONNECT_TIMEOUT_MS:"10000",
      KFE_KEROSENE_NODE_READ_TIMEOUT_MS:"20000",
      KFE_MPC_SIGNING_ENABLED:"false",
      KFE_STANDALONE_MPC_DEV_KEYGEN_ENABLED:"false",
      QUORUM_ALLOW_LOCAL_SIMULATION:"false",
      QUORUM_PSBT_LOCAL_CORE_SIGNER_ENABLED:"false",
      QUORUM_PSBT_REQUIRED_SIGNATURES:"2",
      QUORUM_PSBT_REQUIRE_SIGNER_IDENTITY:"true"
    }}')"
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" patch configmap/kfe-service-config \
    --type merge -p "$patch" >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" patch deployment/kfe-service \
    --type strategic --patch-file "$RAIL_OVERLAY/kfe-runtime-patch.yaml" >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" set image deployment/kfe-service \
    "kfe-service=$KFE_IMAGE" >/dev/null
  info "KFE configured for testnet3, Tor+mTLS and the fixed 2-of-3 Vault constitution"
}

sync_node_client_identities() {
  local i certs="$STATE_ROOT/ceremony-certs" member_dir identity p12 tmp
  for i in 1 2 3; do
    member_dir="$STATE_ROOT/node/member-$i"
    identity="$member_dir/client-identity.pem"
    p12="$(mktemp "$member_dir/.client-identity.XXXXXX.p12")"
    tmp="$(mktemp "$member_dir/.client-identity.XXXXXX.pem")"
    openssl pkcs12 -export \
      -inkey "$certs/nodes/vault-$i/client.key" \
      -in "$certs/nodes/vault-$i/client.crt" \
      -certfile "$certs/ca.crt" -passout pass: -out "$p12" >/dev/null 2>&1
    openssl pkcs12 -in "$p12" -nodes -passin pass: -out "$tmp" >/dev/null 2>&1
    chmod 600 "$tmp"
    mv -f "$tmp" "$identity"
    rm -f "$p12"
  done
}

validate_mtls_material() {
  local check_seconds="${1:-0}" i j cert onion certs="$STATE_ROOT/ceremony-certs"
  openssl x509 -in "$certs/ca.crt" -noout -checkend "$check_seconds" >/dev/null || die "ceremony CA is expired or near expiry"
  openssl x509 -in "$certs/kfe/client.crt" -noout -checkend "$check_seconds" >/dev/null || return 1
  openssl verify -CAfile "$certs/ca.crt" "$certs/kfe/client.crt" >/dev/null || return 1
  for i in 1 2 3; do
    for cert in "$certs/nodes/vault-$i/server.crt" "$certs/nodes/vault-$i/client.crt"; do
      openssl x509 -in "$cert" -noout -checkend "$check_seconds" >/dev/null || return 1
      openssl verify -CAfile "$certs/ca.crt" "$cert" >/dev/null || return 1
    done
    for j in 1 2 3; do
      onion="$(onion "$j")"
      openssl x509 -in "$certs/nodes/vault-$i/server.crt" -noout -checkhost "$onion" 2>/dev/null | \
        grep -q 'does match certificate' || return 1
    done
  done
}

acquire_certificate_rotation_lock() {
  [[ "$CERT_ROTATION_LOCK_TIMEOUT_SECONDS" =~ ^[1-9][0-9]{0,2}$ ]] && \
    (( CERT_ROTATION_LOCK_TIMEOUT_SECONDS <= 300 )) || \
    die "certificate rotation lock timeout must be between 1 and 300 seconds"
  exec 9>"$STATE_ROOT/run/certificate-rotation.lock"
  flock --wait "$CERT_ROTATION_LOCK_TIMEOUT_SECONDS" 9 || \
    die "certificate rotation remained busy for ${CERT_ROTATION_LOCK_TIMEOUT_SECONDS}s"
}

reload_certificate_consumers() {
  local i target_state
  target_state="$(systemctl --user show -p ActiveState --value kerosene-production-cells.target)" || \
    die "unable to inspect kerosene-production-cells.target; refusing consumer reload"
  case "$target_state" in
    inactive)
      info "production cells target is inactive; certificate consumers remain stopped"
      return 0
      ;;
    active)
      ;;
    *)
      die "production cells target is $target_state; refusing consumer reload"
      ;;
  esac

  for i in 1 2 3; do systemctl --user restart "kerosene-vault-$i.service"; done
  for i in 1 2 3; do systemctl --user restart "kerosene-node-$i.service"; done
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" rollout restart deployment/kfe-service >/dev/null
  # Cold-start readiness includes bounded Bitcoin retry plus the Tor/Vault
  # circuit warm-up. Eight minutes produced false failures while the rollout
  # completed successfully seconds later after a host resume.
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" rollout status deployment/kfe-service --timeout=12m >/dev/null
}

rotate_certificates() {
  local force="${1:-true}"
  local certs="$STATE_ROOT/ceremony-certs" backup_root="$STATE_ROOT/cert-backups"
  local backup stamp onions="" onion_value pre_ca post_ca i
  need flock
  need openssl
  need kubectl
  require_state
  ensure_kubernetes
  [[ -x "$CERT_ROTATION_SCRIPT" ]] || die "certificate rotation script missing: $CERT_ROTATION_SCRIPT"
  mkdir -p "$STATE_ROOT/run" "$backup_root"
  chmod 700 "$STATE_ROOT/run" "$backup_root"
  acquire_certificate_rotation_lock

  if [[ "$force" != true ]] && validate_mtls_material "$CERT_RENEW_BEFORE_SECONDS"; then
    info "another rotation refreshed workload certificates while this run waited"
    return 0
  fi

  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup="$backup_root/$stamp"
  mkdir -p "$backup"
  chmod 700 "$backup"
  cp -a "$certs/." "$backup/"
  pre_ca="$(openssl x509 -in "$certs/ca.crt" -noout -fingerprint -sha256)"
  for i in 1 2 3; do
    onion_value="$(onion "$i")"
    if [[ -z "$onions" ]]; then onions="$onion_value"; else onions="$onions,$onion_value"; fi
  done
  VAULT_CEREMONY_MTLS_OUT="$certs" \
  VAULT_CEREMONY_MTLS_TTL_HOURS="$CERT_TTL_HOURS" \
  VAULT_CEREMONY_MTLS_ONION_SANS="$onions" \
  VAULT_LAB_MTLS_P12_PASSWORD=changeit \
    "$CERT_ROTATION_SCRIPT" >/dev/null
  post_ca="$(openssl x509 -in "$certs/ca.crt" -noout -fingerprint -sha256)"
  [[ "$pre_ca" == "$post_ca" ]] || die "ceremony CA changed during leaf rotation"
  validate_mtls_material 0 || die "new workload certificates failed validation"
  sync_node_client_identities
  ensure_vault_client_secret

  reload_certificate_consumers
  info "workload certificates rotated; CA preserved; backup=$backup"
}

renew_certificates_if_needed() {
  require_state
  if validate_mtls_material "$CERT_RENEW_BEFORE_SECONDS"; then
    info "workload certificates remain valid beyond the renewal window"
  else
    rotate_certificates false
  fi
}

start_tor() {
  local i="$1" socks=$((19050 + i)) node_port=$((8800 + i)) vault_port=$((7700 + i))
  start_daemon "tor-$i" "$STATE_ROOT/run/tor-$i.pid" "$STATE_ROOT/log/tor-$i.log" \
    tor --DataDirectory "$STATE_ROOT/tor-$i" --SocksPort "127.0.0.1:$socks" \
    --ControlPort 0 --HiddenServiceDir "$STATE_ROOT/onion-$i" --HiddenServiceVersion 3 \
    --HiddenServicePort "7701 127.0.0.1:$vault_port" \
    --HiddenServicePort "8800 127.0.0.1:$node_port" --Log "notice stdout"
}

start_vault() {
  local i="$1" socks=$((19050 + i)) port=$((7700 + i)) peer_a peer_b env_file
  case "$i" in
    1) peer_a="vault-2=https://$(onion 2):7701"; peer_b="vault-3=https://$(onion 3):7701" ;;
    2) peer_a="vault-1=https://$(onion 1):7701"; peer_b="vault-3=https://$(onion 3):7701" ;;
    3) peer_a="vault-1=https://$(onion 1):7701"; peer_b="vault-2=https://$(onion 2):7701" ;;
  esac
  env_file="$STATE_ROOT/secrets/vault-$i.env"
  {
    printf 'KEROSENE_ENV=production\nRUST_LOG=warn\nVAULT_NODE_ID=vault-%s\nVAULT_NODE_TIER=domestic\n' "$i"
    printf 'VAULT_LISTEN_ADDR=127.0.0.1:%s\nVAULT_TRANSPORT=tor\nVAULT_SOCKS_PROXY=socks5h://127.0.0.1:%s\n' "$port" "$socks"
    printf 'VAULT_SEED_PEERS=%s,%s\nVAULT_PEER_TIERS=vault-1=domestic,vault-2=domestic,vault-3=domestic\n' "$peer_a" "$peer_b"
    # Bound a failed hidden-service request while parallel quorum collection
    # continues through the other peer. One attempt per peer is sufficient for
    # 2-of-3 failover; retries happen at the operation/caller level with a new
    # anti-nonce session, never by reusing a signing round.
    printf 'VAULT_HTTP_TIMEOUT_SECS=45\nVAULT_HTTP_CONNECT_TIMEOUT_SECS=15\nVAULT_HTTP_MAX_RETRIES=1\n'
    printf 'VAULT_AUTH_MODE=mtls\nVAULT_CEREMONY_MODE=production\nVAULT_DKG_MODE=distributed_wire\nVAULT_GENESIS_N=3\nVAULT_RESHARE_POLICY=manual\nVAULT_SHARE_STORE=aead_disk\n'
    printf 'VAULT_DATA_DIR=%s/vault-%s-data-v2\nVAULT_DATA_PASSPHRASE=%s\n' "$STATE_ROOT" "$i" "$(<"$STATE_ROOT/secrets/vault-$i-passphrase")"
    printf 'VAULT_ATTESTATION_ROOT=%s\nVAULT_MEASUREMENT_PIN=%s\n' "$(<"$STATE_ROOT/secrets/attestation-root")" "$(<"$STATE_ROOT/secrets/measurement-pin")"
    printf 'VAULT_AUDIT_PUBKEYS_PATH=%s/ceremony-certs/audit/allowlist.txt\n' "$STATE_ROOT"
    printf 'VAULT_TLS_CERT_PATH=%s/ceremony-certs/nodes/vault-%s/server.crt\nVAULT_TLS_KEY_PATH=%s/ceremony-certs/nodes/vault-%s/server.key\n' "$STATE_ROOT" "$i" "$STATE_ROOT" "$i"
    printf 'VAULT_TLS_CLIENT_CA_PATH=%s/ceremony-certs/ca.crt\n' "$STATE_ROOT"
    printf 'VAULT_TLS_CLIENT_CERT_PATH=%s/ceremony-certs/nodes/vault-%s/client.crt\nVAULT_TLS_CLIENT_KEY_PATH=%s/ceremony-certs/nodes/vault-%s/client.key\n' "$STATE_ROOT" "$i" "$STATE_ROOT" "$i"
    printf 'VAULT_TLS_VERIFY_MODE=onion_or_spiffe\nVAULT_TLS_PEER_SPIFFE_ID=spiffe://kerosene.ceremony/vault/vault-1,spiffe://kerosene.ceremony/vault/vault-2,spiffe://kerosene.ceremony/vault/vault-3\n'
  } > "$env_file"
  chmod 600 "$env_file"
  start_daemon_with_env_file "vault-$i" "$STATE_ROOT/run/vault-$i.pid" "$STATE_ROOT/log/vault-$i.log" "$env_file" "$VAULT_BIN"
}

start_node() {
  local i="$1" socks=$((19050 + i)) port=$((8800 + i)) own peer_a peer_b
  own="$(onion "$i")"
  case "$i" in
    1) peer_a="https://$(onion 2):8800"; peer_b="https://$(onion 3):8800" ;;
    2) peer_a="https://$(onion 1):8800"; peer_b="https://$(onion 3):8800" ;;
    3) peer_a="https://$(onion 1):8800"; peer_b="https://$(onion 2):8800" ;;
  esac
  start_daemon "node-$i" "$STATE_ROOT/run/node-$i.pid" "$STATE_ROOT/log/node-$i.log" env \
    RUST_LOG=kerosene_node=info KEROSENE_NETWORK_ID=kerosene-testnet3 KEROSENE_DISCOVERY_PLANE=vault \
    KEROSENE_NODE_LISTEN_ADDR="127.0.0.1:$port" KEROSENE_NODE_ONION_ENDPOINT="https://$own:8800" \
    KEROSENE_GENESIS_ENDPOINTS="$peer_a,$peer_b" KEROSENE_GENESIS_TRUST_BUNDLE="$STATE_ROOT/node/genesis-trust.json" \
    KEROSENE_IDENTITY_KEY_PATH="$STATE_ROOT/node/member-$i/identity.key" \
    KEROSENE_PEER_STORE="$STATE_ROOT/node/member-$i/peer-store" KEROSENE_LEDGER_DB_PATH="$STATE_ROOT/node/member-$i/ledger" \
    KEROSENE_TLS_CERT_PATH="$STATE_ROOT/ceremony-certs/nodes/vault-$i/server.crt" \
    KEROSENE_TLS_KEY_PATH="$STATE_ROOT/ceremony-certs/nodes/vault-$i/server.key" \
    KEROSENE_TLS_CLIENT_CA_PATH="$STATE_ROOT/ceremony-certs/ca.crt" \
    KEROSENE_TLS_CLIENT_IDENTITY_PEM="$STATE_ROOT/node/member-$i/client-identity.pem" \
    KEROSENE_TOR_SOCKS_PROXY="socks5h://127.0.0.1:$socks" \
    KEROSENE_DISCOVERY_INTERVAL_MS=60000 KEROSENE_PEER_LIVE_WINDOW_MS=300000 \
    "$NODE_BIN"
}

start_all() {
  need tor
  need systemctl
  need openssl
  require_state
  [[ -x "$VAULT_BIN" ]] || die "Vault release binary missing: $VAULT_BIN"
  [[ -x "$NODE_BIN" ]] || die "Node release binary missing: $NODE_BIN"
  mkdir -p "$STATE_ROOT/run" "$STATE_ROOT/log"
  chmod 700 "$STATE_ROOT" "$STATE_ROOT/run" "$STATE_ROOT/log" "$STATE_ROOT/secrets"
  install_target_unit
  validate_mtls_material 0 || die "workload certificates expired; run: $0 renew-certs"
  ensure_kubernetes
  KEROSENE_KUBECONFIG="$KUBECONFIG_PATH" KEROSENE_NAMESPACE="$NAMESPACE" \
    bash "$DEPLOY_ROOT/infra/configure-local-auth.sh" --check
  ensure_kfe_image
  ensure_rail_images
  ensure_rail_secrets
  ensure_vault_client_secret
  sync_node_client_identities
  kubectl --kubeconfig "$KUBECONFIG_PATH" apply -f "$RAIL_OVERLAY/coredns.yaml" >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" apply -f "$RAIL_OVERLAY/server-runtime-hardening.yaml" >/dev/null
  KEROSENE_KUBECONFIG="$KUBECONFIG_PATH" KEROSENE_NAMESPACE="$NAMESPACE" \
    bash "$DEPLOY_ROOT/infra/configure-local-auth.sh" --apply
  for i in 1 2 3; do start_tor "$i"; done
  for i in 1 2 3; do start_vault "$i"; done
  for i in 1 2 3; do start_node "$i"; done
  systemctl --user start kerosene-production-cells.target
  install_certificate_timer
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" apply -f "$RAIL_OVERLAY/rail-cell-volumes.yaml" >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" apply -f "$RAIL_OVERLAY/rail-cells.yaml" >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" scale deployment/local-bitcoin deployment/local-lnd deployment/local-lnd-peer deployment/local-redis deployment/server deployment/web-page --replicas=1 >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" patch deployment/local-lnd deployment/local-lnd-peer \
    --type merge -p '{"spec":{"strategy":{"type":"Recreate","rollingUpdate":null}}}' >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" scale statefulset/local-postgres --replicas=1 >/dev/null
  if kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" get hpa/kfe-service >/dev/null 2>&1; then
    kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" patch hpa/kfe-service --type merge \
      -p '{"spec":{"minReplicas":3,"maxReplicas":3}}' >/dev/null
  fi
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" scale deployment/kfe-service --replicas=0 >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" scale statefulset/rail-cell --replicas=3 >/dev/null
  wait_for_financial_dependencies
  configure_kfe_runtime
  KEROSENE_KUBECONFIG="$KUBECONFIG_PATH" KEROSENE_NAMESPACE="$NAMESPACE" \
    bash "$DEPLOY_ROOT/infra/configure-local-auth.sh" --verify
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" scale deployment/kfe-service --replicas=3 >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" rollout status deployment/kfe-service --timeout=12m >/dev/null
  info "all cells started; run: $0 status"
}

rebuild_kfe() {
  ensure_kubernetes
  KEROSENE_KUBECONFIG="$KUBECONFIG_PATH" KEROSENE_NAMESPACE="$NAMESPACE" \
    bash "$DEPLOY_ROOT/infra/configure-local-auth.sh" --verify
  build_kfe_image
  require_state
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" scale deployment/kfe-service --replicas=0 >/dev/null
  wait_for_financial_dependencies
  configure_kfe_runtime
  KEROSENE_KUBECONFIG="$KUBECONFIG_PATH" KEROSENE_NAMESPACE="$NAMESPACE" \
    bash "$DEPLOY_ROOT/infra/configure-local-auth.sh" --verify
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" scale deployment/kfe-service --replicas=3 >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" rollout status deployment/kfe-service --timeout=12m
}

stop_local() {
  local kind i pid_file unit
  systemctl --user stop kerosene-production-cells.target 2>/dev/null || true
  for kind in node vault tor; do
    for i in 1 2 3; do
      pid_file="$STATE_ROOT/run/$kind-$i.pid"
      unit="kerosene-$kind-$i.service"
      if systemctl --user is-active --quiet "$unit"; then
        systemctl --user stop "$unit"
        info "$kind-$i stopped"
      fi
      rm -f "$pid_file"
    done
  done
}

stop_all() {
  stop_local
  if [[ -s "$KUBECONFIG_PATH" ]] && kubectl --kubeconfig "$KUBECONFIG_PATH" get namespace "$NAMESPACE" >/dev/null 2>&1; then
    kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" scale statefulset/rail-cell statefulset/local-postgres --replicas=0 >/dev/null
    kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" scale deployment/kfe-service deployment/local-bitcoin deployment/local-lnd deployment/local-lnd-peer deployment/local-redis deployment/server deployment/tor-onion deployment/web-page --replicas=0 >/dev/null
    info "Kubernetes services stopped; persistent volumes were retained"
  fi
}

status_all() {
  local kind i unit pid active enabled
  echo "Local cells:"
  for kind in tor vault node; do
    for i in 1 2 3; do
      unit="kerosene-$kind-$i.service"
      active="$(systemctl --user is-active "$unit" 2>/dev/null || true)"
      enabled="$(systemctl --user is-enabled "$unit" 2>/dev/null || true)"
      pid="$(systemctl --user show -p MainPID --value "$unit" 2>/dev/null || true)"
      printf '  %-8s active=%-8s enabled=%-8s pid=%s\n' "$kind-$i" "${active:-unknown}" "${enabled:-unknown}" "${pid:-0}"
    done
  done
  if [[ -s "$KUBECONFIG_PATH" ]]; then
    echo
    echo "Kubernetes:"
    kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" get pods -o wide
  fi
  echo
  echo "Node quorum:"
  for i in 1 2 3; do
    curl -sk --max-time 3 --cert "$STATE_ROOT/ceremony-certs/nodes/vault-$i/client.crt" \
      --key "$STATE_ROOT/ceremony-certs/nodes/vault-$i/client.key" \
      "https://127.0.0.1:$((8800 + i))/v1/readiness" || true
    echo
  done
}

logs() {
  local service="${1:-}"
  [[ "$service" =~ ^(tor|vault|node)-[123]$ ]] || die "usage: $0 logs {tor|vault|node}-{1|2|3}"
  tail -f "$STATE_ROOT/log/$service.log"
}

main() {
  case "${1:-}" in
    start) start_all ;;
    stop) stop_all ;;
    restart) stop_all; start_all ;;
    renew-certs) rotate_certificates ;;
    renew-certs-if-needed) renew_certificates_if_needed ;;
    rebuild-kfe) rebuild_kfe ;;
    status) status_all ;;
    logs) logs "${2:-}" ;;
    *) die "usage: $0 {start|stop|restart|rebuild-kfe|renew-certs|renew-certs-if-needed|status|logs SERVICE}" ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
