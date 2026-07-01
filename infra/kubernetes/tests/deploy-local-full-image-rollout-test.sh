#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SUBJECT="$REPO_ROOT/infra/kubernetes/scripts/deploy-local-full.sh"
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

mkdir -p "$TMP_DIR/bin" "$TMP_DIR/infra/scripts" "$TMP_DIR/infra/kubernetes/scripts" "$TMP_DIR/infra/kubernetes/overlays/local-full"
cp "$SUBJECT" "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh"
chmod +x "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh"

cat > "$TMP_DIR/infra/scripts/host-services.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ensure_local_host_services() { :; }
EOF

cat > "$TMP_DIR/infra/kubernetes/scripts/validate-local-full.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "validated"
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/validate-local-full.sh"

cat > "$TMP_DIR/infra/kubernetes/scripts/import-local-docker-images.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "import succeeded"
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/import-local-docker-images.sh"

cat > "$TMP_DIR/bin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "docker:$*" >> "$CALL_LOG"
if [[ "${1:-}" == "image" && "${2:-}" == "inspect" ]]; then
  image="${*: -1}"
  case "$image" in
    kerosene/server:local) echo "sha256:server-image-id" ;;
    localhost:5000/kerosene/kfe-service:local) echo "sha256:kfe-service-image-id" ;;
    kerosene/mpc-sidecar:local) echo "sha256:mpc-sidecar-image-id" ;;
    kerosene/web-page:local) echo "sha256:web-page-image-id" ;;
    kerosene/tor:local) echo "sha256:tor-image-id" ;;
    *) echo "unknown image: ${3:-}" >&2; exit 42 ;;
  esac
  exit 0
fi
echo "unexpected docker call: $*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/docker"

cat > "$TMP_DIR/bin/kubectl" <<EOF
#!/usr/bin/env bash
set -euo pipefail
echo "\$*" >> "$LOG_FILE"
if [[ "\${1:-}" == "config" && "\${2:-}" == "current-context" ]]; then
  echo "test-context"
  exit 0
fi
if [[ "\${1:-}" == "cluster-info" ]]; then
  echo "cluster ok"
  exit 0
fi
if [[ "\${1:-}" == "apply" ]]; then
  echo "applied"
  exit 0
fi
if [[ "\${1:-}" == "-n" && "\${2:-}" == "kerosene-local" && "\${3:-}" == "patch" ]]; then
  echo "patched"
  exit 0
fi
if [[ "\${1:-}" == "-n" && "\${2:-}" == "kerosene-local" && "\${3:-}" == "get" ]]; then
  if [[ "\${4:-}" == "deploy/tor-onion" ]]; then
    exit 1
  fi
  echo "objects"
  exit 0
fi
echo "unexpected kubectl call: \$*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/kubectl"

: > "$LOG_FILE"

PATH="$TMP_DIR/bin:$PATH" CALL_LOG="$LOG_FILE" KUBECTL=kubectl "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh" >/dev/null

grep -qF 'patch deployment/server --type merge -p' "$LOG_FILE" || fail "server image id was not recorded on the pod template"
grep -qF 'patch deployment/kfe-service --type merge -p' "$LOG_FILE" || fail "kfe-service image id was not recorded on the pod template"
grep -qF 'patch statefulset/mpc-sidecar --type merge -p' "$LOG_FILE" || fail "mpc-sidecar image id was not recorded on the pod template"
grep -qF 'patch deployment/web-page --type merge -p' "$LOG_FILE" || fail "web-page image id was not recorded on the pod template"
grep -qF 'patch deployment/tor-onion --type merge -p' "$LOG_FILE" || fail "tor-onion image id was not recorded on the pod template"
grep -qF '"kerosene.io/local-image-id":"sha256:server-image-id"' "$LOG_FILE" || fail "server patch did not include the local image id annotation"

echo "[PASS] deploy-local-full.sh image rollout annotations"
