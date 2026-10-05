#!/usr/bin/env bash
set -euo pipefail

TEST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_TMP="$(mktemp -d)"
TEST_CALLS="$TEST_TMP/calls.log"

cleanup() { rm -rf -- "$TEST_TMP"; }
trap cleanup EXIT
fail() { echo "[FAIL] $*" >&2; exit 1; }
assert_file_line() {
  local expected="$1" file="$2"
  rg -Fxq -- "$expected" "$file" || fail "missing '$expected' in $file"
}
assert_no_file_line() {
  local unexpected="$1" file="$2"
  ! rg -Fxq -- "$unexpected" "$file" || fail "unexpected '$unexpected' in $file"
}
assert_call() {
  local expected="$1"
  rg -Fxq -- "$expected" "$TEST_CALLS" || fail "missing call: $expected"
}

export KEROSENE_CELL_STATE_DIR="$TEST_TMP/state"
export KEROSENE_CELL_CONFIG_DIR="$TEST_TMP/config"
export KEROSENE_SYSTEMD_USER_DIR="$TEST_TMP/systemd"
export KEROSENE_KUBECONFIG="$TEST_TMP/config/kubeconfig"
export KEROSENE_NAMESPACE="resume-test"
mkdir -p "$KEROSENE_CELL_STATE_DIR/run" "$KEROSENE_SYSTEMD_USER_DIR"
: > "$TEST_CALLS"

# Sourcing is intentionally supported so this test can exercise the generators
# and reload policy without dispatching a command or touching the host runtime.
# shellcheck source=../local-production-cells.sh
source "$TEST_ROOT/infra/local-production-cells.sh"

MOCK_TARGET_ACTIVE=0
systemctl() {
  printf 'systemctl %s\n' "$*" >> "$TEST_CALLS"
  if [[ "$*" == "--user show -p ActiveState --value kerosene-production-cells.target" ]]; then
    if [[ "$MOCK_TARGET_ACTIVE" == 1 ]]; then
      printf 'active\n'
    else
      printf 'inactive\n'
    fi
    return
  fi
  return 0
}
kubectl() {
  printf 'kubectl %s\n' "$*" >> "$TEST_CALLS"
  return 0
}
flock() {
  printf 'flock %s\n' "$*" >> "$TEST_CALLS"
  return 0
}

install_certificate_timer

SERVICE_UNIT="$KEROSENE_SYSTEMD_USER_DIR/kerosene-certificate-renewal.service"
TIMER_UNIT="$KEROSENE_SYSTEMD_USER_DIR/kerosene-certificate-renewal.timer"
assert_file_line 'Type=oneshot' "$SERVICE_UNIT"
assert_file_line 'TimeoutStartSec=20min' "$SERVICE_UNIT"
assert_file_line 'OnActiveSec=30s' "$TIMER_UNIT"
assert_file_line 'OnUnitActiveSec=4h' "$TIMER_UNIT"
assert_file_line 'Persistent=true' "$TIMER_UNIT"
assert_no_file_line 'OnBootSec=15min' "$TIMER_UNIT"
[[ "$(stat -c '%a' "$SERVICE_UNIT")" == 600 ]] || fail 'renewal service permissions are not 0600'
[[ "$(stat -c '%a' "$TIMER_UNIT")" == 600 ]] || fail 'renewal timer permissions are not 0600'

: > "$TEST_CALLS"
acquire_certificate_rotation_lock
assert_call 'flock --wait 60 9'
if (CERT_ROTATION_LOCK_TIMEOUT_SECONDS=0; acquire_certificate_rotation_lock) >/dev/null 2>&1; then
  fail 'zero lock wait was accepted'
fi
if (CERT_ROTATION_LOCK_TIMEOUT_SECONDS=301; acquire_certificate_rotation_lock) >/dev/null 2>&1; then
  fail 'unbounded lock wait was accepted'
fi

: > "$TEST_CALLS"
MOCK_TARGET_ACTIVE=1
reload_certificate_consumers
assert_call 'systemctl --user show -p ActiveState --value kerosene-production-cells.target'
for member in 1 2 3; do
  assert_call "systemctl --user restart kerosene-vault-$member.service"
  assert_call "systemctl --user restart kerosene-node-$member.service"
done
assert_call "kubectl --kubeconfig $KEROSENE_KUBECONFIG -n resume-test rollout restart deployment/kfe-service"
assert_call "kubectl --kubeconfig $KEROSENE_KUBECONFIG -n resume-test rollout status deployment/kfe-service --timeout=12m"
if rg -q -- 'try-restart' "$TEST_CALLS"; then
  fail 'active target still used try-restart and could leave failed consumers down'
fi

: > "$TEST_CALLS"
MOCK_TARGET_ACTIVE=0
reload_certificate_consumers >/dev/null
assert_call 'systemctl --user show -p ActiveState --value kerosene-production-cells.target'
if rg -q -- ' restart |^kubectl ' "$TEST_CALLS"; then
  fail 'inactive target caused a consumer start, restart or rollout'
fi

echo '[PASS] local production resume units, bounded lock and consumer reload policy'
