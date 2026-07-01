#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SUBJECT="$REPO_ROOT/infra/kubernetes/scripts/import-local-docker-images.sh"
TMP_DIR="$(mktemp -d)"
LOG_FILE="$TMP_DIR/calls.log"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  [[ "$haystack" == *"$needle"* ]] || fail "Expected output to contain: $needle"
}

mkdir -p "$TMP_DIR/bin" "$TMP_DIR/infra/kubernetes/scripts" "$TMP_DIR/infra/scripts" \
  "$TMP_DIR/infra/docker/images/server" \
  "$TMP_DIR/infra/docker/images/kfe-service" \
  "$TMP_DIR/infra/docker/images/mpc-sidecar" \
  "$TMP_DIR/infra/docker/images/web-page" \
  "$TMP_DIR/infra/docker/images/tor" \
  "$TMP_DIR/infra/runtime/tor" \
  "$TMP_DIR/infra/runtime/web" \
  "$TMP_DIR/backend/kerosene" \
  "$TMP_DIR/backend/mpc-sidecar"
cp "$SUBJECT" "$TMP_DIR/infra/kubernetes/scripts/import-local-docker-images.sh"
chmod +x "$TMP_DIR/infra/kubernetes/scripts/import-local-docker-images.sh"
touch \
  "$TMP_DIR/infra/docker/images/server/Dockerfile" \
  "$TMP_DIR/infra/docker/images/kfe-service/Dockerfile" \
  "$TMP_DIR/infra/docker/images/mpc-sidecar/Dockerfile" \
  "$TMP_DIR/infra/docker/images/web-page/Dockerfile" \
  "$TMP_DIR/infra/docker/images/tor/Dockerfile" \
  "$TMP_DIR/infra/runtime/web/nginx.k8s.conf"

cat > "$TMP_DIR/infra/scripts/backend-common.sh" <<'EOF'
#!/usr/bin/env bash
info() { echo "[infra] $*"; }
fail() { echo "[infra][error] $*" >&2; exit 1; }
require_docker() { :; }
compose() { echo "unexpected compose call: $*" >&2; exit 42; }
EOF

cat > "$TMP_DIR/infra/scripts/flutter-common.sh" <<'EOF'
#!/usr/bin/env bash
kerosene_resolve_flutter_bin() { echo flutter; }
kerosene_run_flutter() { :; }
kerosene_chown_sudo_user() { :; }
EOF

cat > "$TMP_DIR/bin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "docker:$*" >> "$CALL_LOG"
case "${1:-}" in
  image)
    [[ "${2:-}" == "inspect" ]] && exit 0
    ;;
  save)
    printf 'fake-image-tar'
    exit 0
    ;;
  build|tag)
    exit 0
    ;;
esac
echo "unexpected docker call: $*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/docker"

cat > "$TMP_DIR/bin/ctr" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "ctr:$*" >> "$CALL_LOG"
if [[ "$*" == *" images import -" ]]; then
  cat >/dev/null
  exit 0
fi
if [[ "$*" == *" images ls" ]]; then
  echo "kerosene/server:local"
  exit 0
fi
echo "unexpected ctr call: $*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/ctr"

cat > "$TMP_DIR/bin/sudo" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "sudo:$*" >> "$CALL_LOG"
if [[ "${1:-}" == "-n" && "${2:-}" == "true" ]]; then
  exit 1
fi
if [[ "${1:-}" == "-v" ]]; then
  exit 0
fi
if [[ "${1:-}" == "ctr" ]]; then
  shift
  exec ctr "$@"
fi
echo "unexpected sudo call: $*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/sudo"

: > "$LOG_FILE"

set +e
noninteractive_output="$(
  PATH="$TMP_DIR/bin:$PATH" CALL_LOG="$LOG_FILE" \
    "$TMP_DIR/infra/kubernetes/scripts/import-local-docker-images.sh" \
      --skip-kfe-service-build --skip-web-page-build 2>&1
)"
noninteractive_status=$?
set -e

[[ "$noninteractive_status" -ne 0 ]] || fail "non-interactive import unexpectedly succeeded without cached sudo"
assert_contains "$noninteractive_output" "sudo"

: > "$LOG_FILE"

set +e
interactive_output="$(
  python3 - "$TMP_DIR" "$LOG_FILE" <<'PY'
import os
import pty
import subprocess
import sys

tmp_dir, log_file = sys.argv[1], sys.argv[2]
env = os.environ.copy()
env["PATH"] = f"{tmp_dir}/bin:{env['PATH']}"
env["CALL_LOG"] = log_file
cmd = [
    f"{tmp_dir}/infra/kubernetes/scripts/import-local-docker-images.sh",
    "--skip-kfe-service-build",
    "--skip-web-page-build",
]

master_fd, slave_fd = pty.openpty()
proc = subprocess.Popen(
    cmd,
    stdin=slave_fd,
    stdout=slave_fd,
    stderr=slave_fd,
    env=env,
    cwd=tmp_dir,
)
os.close(slave_fd)
output = bytearray()
while True:
    try:
        chunk = os.read(master_fd, 4096)
    except OSError:
        break
    if not chunk:
        break
    output.extend(chunk)
status = proc.wait()
os.close(master_fd)
sys.stdout.buffer.write(output)
sys.exit(status)
PY
)"
interactive_status=$?
set -e

[[ "$interactive_status" -eq 0 ]] || fail "interactive import failed. Output: $interactive_output"
grep -qxF "sudo:-v" "$LOG_FILE" || fail "interactive path did not validate sudo credentials"
grep -q '^sudo:ctr -n k8s.io images import -$' "$LOG_FILE" || fail "interactive path did not import with sudo ctr"
grep -q '^ctr:-n k8s.io images ls$' "$LOG_FILE" || fail "interactive path did not list imported images through ctr"

echo "[PASS] import-local-docker-images.sh sudo handling"
