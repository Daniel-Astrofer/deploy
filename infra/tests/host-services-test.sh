#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
LOG_FILE="$TMP_DIR/systemctl.log"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

mkdir -p "$TMP_DIR/bin" "$TMP_DIR/scripts"
cp "$REPO_ROOT/infra/scripts/host-services.sh" "$TMP_DIR/scripts/host-services.sh"

cat > "$TMP_DIR/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "systemctl:$*" >> "$CALL_LOG"

case "${1:-}" in
  list-unit-files)
    case "${2:-}" in
      containerd.service|docker.service|kubelet.service)
        echo "${2:-} enabled"
        exit 0
        ;;
    esac
    exit 1
    ;;
  is-active)
    [[ "${2:-}" == "--quiet" ]] || exit 42
    service="${3:-}"
    [[ -f "$ACTIVE_DIR/$service" ]]
    exit $?
    ;;
  start)
    touch "$ACTIVE_DIR/${2:-}"
    exit 0
    ;;
esac

exit 0
EOF
chmod +x "$TMP_DIR/bin/systemctl"

mkdir -p "$TMP_DIR/active"
: > "$LOG_FILE"
PATH="$TMP_DIR/bin:$PATH" CALL_LOG="$LOG_FILE" ACTIVE_DIR="$TMP_DIR/active" bash -c '
  source "$1"
  ensure_local_host_services
' _ "$TMP_DIR/scripts/host-services.sh" >/dev/null

grep -qxF "systemctl:start containerd.service" "$LOG_FILE" || fail "containerd was not started"
grep -qxF "systemctl:start docker.service" "$LOG_FILE" || fail "docker was not started"
grep -qxF "systemctl:start kubelet.service" "$LOG_FILE" || fail "kubelet was not started"

: > "$LOG_FILE"
PATH="$TMP_DIR/bin:$PATH" CALL_LOG="$LOG_FILE" ACTIVE_DIR="$TMP_DIR/active" KEROSENE_AUTO_START_HOST_SERVICES=0 bash -c '
  source "$1"
  ensure_local_host_services
' _ "$TMP_DIR/scripts/host-services.sh" >/dev/null

[[ ! -s "$LOG_FILE" ]] || fail "host service preflight should be skipped when disabled"

echo "[PASS] host service preflight"
