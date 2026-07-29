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
cp "$REPO_ROOT/infra/kubernetes/scripts/local-host-env.sh" "$TMP_DIR/infra/kubernetes/scripts/local-host-env.sh"
chmod +x "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh"

cat > "$TMP_DIR/infra/kubernetes/overlays/local-full/local-tor-onion.yaml" <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: tor-onion-config
data:
  torrc: |
    HiddenServicePort 80 web-page:8080
EOF

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

cat > "$TMP_DIR/infra/kubernetes/scripts/ensure-vault-mesh-lab.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "127.0.0.1"
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/ensure-vault-mesh-lab.sh"

cat > "$TMP_DIR/infra/kubernetes/scripts/ensure-local-monitoring.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exit 0
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/ensure-local-monitoring.sh"

cat > "$TMP_DIR/infra/kubernetes/scripts/render-local-full-overlay.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
echo "$TMP_DIR/infra/kubernetes/overlays/local-full"
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/render-local-full-overlay.sh"

cat > "$TMP_DIR/infra/kubernetes/scripts/ensure-local-cluster.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "[*] Kubernetes context: test-context"
exit 0
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/ensure-local-cluster.sh"

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
    localhost:5000/kerosene/server:local) echo "sha256:server-image-id" ;;
    localhost:5000/kerosene/kfe-service:local) echo "sha256:kfe-service-image-id" ;;
    localhost:5000/kerosene/web-page:local) echo "sha256:web-page-image-id" ;;
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
# Drop optional --kubeconfig PATH so mocks stay stable across host configs.
args=()
while [[ \$# -gt 0 ]]; do
  case "\$1" in
    --kubeconfig)
      shift 2 || true
      continue
      ;;
    --kubeconfig=*)
      shift
      continue
      ;;
  esac
  args+=("\$1")
  shift
done
set -- "\${args[@]}"
echo "\$*" >> "$LOG_FILE"
if [[ "\${1:-}" == "config" && "\${2:-}" == "current-context" ]]; then
  echo "test-context"
  exit 0
fi
if [[ "\${1:-}" == "get" && "\${2:-}" == "--raw=/readyz" ]]; then
  echo "ok"
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
if [[ "\${1:-}" == "-n" && "\${2:-}" == "kerosene-local" && "\${3:-}" == "delete" ]]; then
  echo "deleted"
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

PATH="$TMP_DIR/bin:$PATH" \
  KEROSENE_HOST_HOME="$TMP_DIR/no-kube-home" \
  KUBECONFIG="$TMP_DIR/no-kube-home/config" \
  CALL_LOG="$LOG_FILE" \
  KUBECTL=kubectl \
  "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh" >/dev/null

grep -qF 'patch deployment/server --type merge -p' "$LOG_FILE" || fail "server image id was not recorded on the pod template"
grep -qF 'patch deployment/kfe-service --type merge -p' "$LOG_FILE" || fail "kfe-service image id was not recorded on the pod template"
grep -qF 'patch deployment/web-page --type merge -p' "$LOG_FILE" || fail "web-page image id was not recorded on the pod template"
grep -qF 'patch deployment/tor-onion --type merge -p' "$LOG_FILE" || fail "tor-onion image id was not recorded on the pod template"
grep -qF '"kerosene.io/local-image-id":"sha256:server-image-id"' "$LOG_FILE" || fail "server patch did not include the local image id annotation"
grep -qF '"kerosene.io/tor-config-hash":"' "$LOG_FILE" || fail "tor-onion config hash was not recorded on the pod template"
grep -qF 'delete networkpolicy/local-full-allow-nodeport-ingress --ignore-not-found' "$LOG_FILE" || fail "stale NodePort policy cleanup was not attempted"

echo "[PASS] deploy-local-full.sh image rollout annotations"
