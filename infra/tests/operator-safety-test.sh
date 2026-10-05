#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
MOCK_KUBECTL="$TMP_DIR/kubectl"
LOG="$TMP_DIR/kubectl.log"

cleanup() { rm -rf -- "$TMP_DIR"; }
trap cleanup EXIT
fail() { echo "[FAIL] $*" >&2; exit 1; }

cat >"$MOCK_KUBECTL" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$KUBECTL_LOG"
if [[ "$*" == *"jsonpath="* ]]; then
  printf 'pod-1'
fi
MOCK
chmod 0755 "$MOCK_KUBECTL"
export KUBECTL="$MOCK_KUBECTL"
export KUBECTL_LOG="$LOG"

: >"$LOG"
bash "$ROOT/infra/kubernetes/scripts/reset-instance.sh" \
  kerosene-production server rollout >/dev/null
grep -q 'get deployment/server' "$LOG" || fail "reset check omitted target read"
if grep -Eq 'rollout restart|delete pod' "$LOG"; then
  fail "reset check changed the cluster"
fi

: >"$LOG"
bash "$ROOT/infra/kubernetes/scripts/reset-instance.sh" --apply \
  kerosene-production server rollout >/dev/null
grep -q 'rollout restart deployment/server' "$LOG" || fail "reset apply did not restart"

: >"$LOG"
bash "$ROOT/infra/kubernetes/scripts/rollback.sh" \
  kerosene-production server >/dev/null
grep -q 'rollout history deployment/server' "$LOG" || fail "rollback check omitted history"
if grep -q 'rollout undo' "$LOG"; then
  fail "rollback check changed the cluster"
fi

: >"$LOG"
bash "$ROOT/infra/kubernetes/scripts/rollback.sh" --apply \
  kerosene-production server 3 >/dev/null
grep -q 'rollout undo deployment/server --to-revision=3' "$LOG" || \
  fail "rollback apply omitted selected revision"

if bash "$ROOT/infra/kubernetes/scripts/debug-pod.sh" \
  kerosene-production server --shell >/dev/null 2>&1; then
  fail "debug shell accepted an unpinned image"
fi

: >"$LOG"
DEBUG_IMAGE="registry.invalid/netshoot@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
  bash "$ROOT/infra/kubernetes/scripts/debug-pod.sh" \
    kerosene-production server --shell >/dev/null
grep -q 'debug -it pod-1' "$LOG" || fail "pinned debug image did not run"

echo "[PASS] explicit operator mutation controls"
