#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
LOG_FILE="$TMP_DIR/kubectl.log"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

mkdir -p "$TMP_DIR/scripts"
cp "$REPO_ROOT/infra/kubernetes/scripts/logs.sh" "$TMP_DIR/scripts/logs.sh"
cp "$REPO_ROOT/infra/kubernetes/scripts/status.sh" "$TMP_DIR/scripts/status.sh"
chmod +x "$TMP_DIR/scripts/logs.sh" "$TMP_DIR/scripts/status.sh"

cat > "$TMP_DIR/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "$*" >> "$CALL_LOG"

if [[ "${1:-}" == "config" && "${2:-}" == "current-context" ]]; then
  echo "fake-context"
  exit 0
fi

if [[ "${MISSING_NS:-0}" == "1" && "${1:-}" == "get" && "${2:-}" == "namespace" ]]; then
  exit 1
fi

if [[ "${MISSING_NS:-0}" == "1" && "${1:-}" == "-n" ]]; then
  echo "namespace not found" >&2
  exit 1
fi

if [[ "${1:-}" == "-n" && "${3:-}" == "exec" && "${4:-}" == "deploy/tor-onion" ]]; then
  echo "teststableonionaddress.onion"
  exit 0
fi

if [[ "${3:-}" == "logs" ]]; then
  echo "log line from ${4:-unknown}"
  exit 0
fi

exit 0
EOF
chmod +x "$TMP_DIR/kubectl"

: > "$LOG_FILE"
CALL_LOG="$LOG_FILE" KEROSENE_HOST_HOME="$TMP_DIR/no-kube-home" KEROSENE_LOG_FOLLOW=0 KUBECTL="$TMP_DIR/kubectl" bash "$TMP_DIR/scripts/logs.sh" --tail 20 >/dev/null
grep -qxF -- "-n kerosene-local logs deployment/server --all-containers --tail=20" "$LOG_FILE" || fail "logs --tail should default to all targets"
grep -qxF -- "-n kerosene-local logs deployment/web-page --all-containers --tail=20" "$LOG_FILE" || fail "logs --tail should include web-page"

: > "$LOG_FILE"
CALL_LOG="$LOG_FILE" KEROSENE_HOST_HOME="$TMP_DIR/no-kube-home" KEROSENE_LOG_RECONNECT=0 KUBECTL="$TMP_DIR/kubectl" bash "$TMP_DIR/scripts/logs.sh" server --tail 6 >/dev/null
grep -qxF -- "-n kerosene-local logs deployment/server --all-containers --tail=6 --follow" "$LOG_FILE" || fail "logs should follow a single target by default"

: > "$LOG_FILE"
CALL_LOG="$LOG_FILE" KEROSENE_HOST_HOME="$TMP_DIR/no-kube-home" KEROSENE_LOG_RECONNECT=0 KUBECTL="$TMP_DIR/kubectl" bash "$TMP_DIR/scripts/logs.sh" --follow server --tail 5 >/dev/null
grep -qxF -- "-n kerosene-local logs deployment/server --all-containers --tail=5 --follow" "$LOG_FILE" || fail "logs should accept options before target"

: > "$LOG_FILE"
SPLIT_DIR="$TMP_DIR/split-logs"
CALL_LOG="$LOG_FILE" KEROSENE_HOST_HOME="$TMP_DIR/no-kube-home" KEROSENE_LOG_RECONNECT=0 KUBECTL="$TMP_DIR/kubectl" bash "$TMP_DIR/scripts/logs.sh" --follow --split --output-dir "$SPLIT_DIR" --tail 7 >/dev/null
grep -qxF -- "-n kerosene-local logs deployment/server --all-containers --tail=7 --follow" "$LOG_FILE" || fail "split logs should follow server"
grep -qxF -- "-n kerosene-local logs statefulset/local-postgres --all-containers --tail=7 --follow" "$LOG_FILE" || fail "split logs should include local-postgres for full runtime"
grep -qF "log line from deployment/server" "$SPLIT_DIR/server.log" || fail "split logs should write server log file"
grep -qF "log line from statefulset/local-postgres" "$SPLIT_DIR/local-postgres.log" || fail "split logs should write local-postgres log file"
grep -qF "server deployment/server" "$SPLIT_DIR/index.txt" || fail "split logs should write an index"

: > "$LOG_FILE"
status_output="$(CALL_LOG="$LOG_FILE" KEROSENE_HOST_HOME="$TMP_DIR/no-kube-home" KUBECTL="$TMP_DIR/kubectl" MISSING_NS=1 bash "$TMP_DIR/scripts/status.sh" 2>&1)" || fail "status should not fail when namespace is missing"
grep -qF "Namespace kerosene-local does not exist" <<<"$status_output" || fail "status should explain missing namespace"

: > "$LOG_FILE"
status_output="$(CALL_LOG="$LOG_FILE" KEROSENE_HOST_HOME="$TMP_DIR/no-kube-home" KUBECTL="$TMP_DIR/kubectl" bash "$TMP_DIR/scripts/status.sh" 2>&1)" || fail "status should succeed when namespace exists"
grep -qF "tor onion: http://teststableonionaddress.onion" <<<"$status_output" || fail "status should show current onion address"
grep -qF "onion keys: /home/omega/.local/state/kerosene/tor/keys/local-full" <<<"$status_output" || fail "status should show stable onion key path"

echo "[PASS] kubernetes helper scripts"
