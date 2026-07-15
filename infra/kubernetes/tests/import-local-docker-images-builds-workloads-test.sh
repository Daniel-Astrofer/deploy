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
  build|tag)
    exit 0
    ;;
  save)
    printf 'fake-image-tar'
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
  exit 0
fi
if [[ "${1:-}" == "-n" && "${2:-}" == "ctr" ]]; then
  shift 2
  exec ctr "$@"
fi
echo "unexpected sudo call: $*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/sudo"

: > "$LOG_FILE"

PATH="$TMP_DIR/bin:$PATH" CALL_LOG="$LOG_FILE" \
  "$TMP_DIR/infra/kubernetes/scripts/import-local-docker-images.sh" \
    --skip-web-page-build >/dev/null

grep -qF "docker:build -t kerosene/server:local -f $TMP_DIR/infra/docker/images/server/Dockerfile $TMP_DIR/backend/kerosene" "$LOG_FILE" || fail "server image was not rebuilt from the canonical Dockerfile"
grep -qF "docker:build -t kerosene/kfe-service:local -f $TMP_DIR/infra/docker/images/kfe-service/Dockerfile $TMP_DIR/backend/kerosene" "$LOG_FILE" || fail "kfe-service image was not rebuilt from the canonical Dockerfile"
grep -qF "docker:build -t kerosene/mpc-sidecar:local -f $TMP_DIR/infra/docker/images/mpc-sidecar/Dockerfile $TMP_DIR/backend/mpc-sidecar" "$LOG_FILE" || fail "mpc-sidecar image was not rebuilt from the canonical Dockerfile"

echo "[PASS] import-local-docker-images.sh workload builds"
