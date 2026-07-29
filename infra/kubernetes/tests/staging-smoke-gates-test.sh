#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TMP_DIR="$(mktemp -d)"

cleanup() {
  find "$TMP_DIR" -type f -exec unlink {} \;
  rmdir "$TMP_DIR/bin"
  rmdir "$TMP_DIR"
}
trap cleanup EXIT

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

mkdir "$TMP_DIR/bin"

cat > "$TMP_DIR/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"get secret staging-smoke-credentials"*username* ]]; then
  printf 'c21va2UtdXNlcg=='
  exit 0
fi
if [[ "$*" == *"get secret staging-smoke-credentials"*password* ]]; then
  printf 'c21va2UtcGFzc3dvcmQ='
  exit 0
fi
if [[ "$*" == *"get secret kfe-vault-mtls-certs"* ]]; then
  printf 'c3ludGhldGljLXRlc3QtY2VydA=='
  exit 0
fi
if [[ "$*" == *"port-forward"* ]]; then
  sleep 30
  exit 0
fi
echo "unexpected kubectl call: $*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/kubectl"

cat > "$TMP_DIR/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  *"/auth/login"*)
    printf '{"success":true,"data":"synthetic-token"}'
    ;;
  *"/v1/health"*)
    printf '{"status":"ready","peer_count":2,"peer_reachability":"probed"}'
    ;;
  *"/health/ready"*)
    printf '{"status":"UP"}'
    ;;
  *)
    echo "unexpected curl call: $*" >&2
    exit 42
    ;;
esac
EOF
chmod +x "$TMP_DIR/bin/curl"

PATH="$TMP_DIR/bin:$PATH" KUBECTL=kubectl \
  bash "$REPO_ROOT/infra/kubernetes/scripts/smoke-staging-login.sh" >/dev/null
PATH="$TMP_DIR/bin:$PATH" KUBECTL=kubectl \
  bash "$REPO_ROOT/infra/kubernetes/scripts/smoke-staging-frost-quorum.sh" >/dev/null

echo "[PASS] staging login and FROST readiness smoke gates"
