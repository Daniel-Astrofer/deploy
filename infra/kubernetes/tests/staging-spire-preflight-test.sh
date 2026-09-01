#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PREFLIGHT="$REPO_ROOT/infra/kubernetes/scripts/preflight-staging-spire.sh"
TMP_DIR="$(mktemp -d)"

cleanup() {
  find "$TMP_DIR" -type f -exec unlink {} \;
  rmdir "$TMP_DIR/bin"
  rmdir "$TMP_DIR"
}
trap cleanup EXIT

mkdir "$TMP_DIR/bin"

cat > "$TMP_DIR/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

case "$*" in
  *"get csidriver/csi.spiffe.io"*)
    [[ "${SPIRE_TEST_MODE:-ready}" != "missing-csi" ]]
    ;;
  *"statefulset/spire-server"*)
    if [[ "${SPIRE_TEST_MODE:-ready}" == "server-not-ready" ]]; then
      printf '1 0'
    else
      printf '1 1'
    fi
    ;;
  *"daemonset/spire-agent"*)
    if [[ "${SPIRE_TEST_MODE:-ready}" == "agent-not-ready" ]]; then
      printf '2 1'
    else
      printf '2 2'
    fi
    ;;
  *"configmap/spire-bundle"*)
    [[ "${SPIRE_TEST_MODE:-ready}" != "empty-bundle" ]] && printf '%s' 'synthetic-ca'
    ;;
  *"clusterspiffeid/"*".spec.className"*)
    printf '%s' 'kerosene-staging'
    ;;
  *"clusterspiffeid/"*"namespacesSelected"*)
    printf '1'
    ;;
  *"clusterspiffeid/"*"entryFailures"*)
    [[ "${SPIRE_TEST_MODE:-ready}" == "entry-failure" ]] && printf '1' || printf '0'
    ;;
  *"clusterspiffeid/"*"podEntryRenderFailures"*)
    printf '0'
    ;;
  *)
    echo "unexpected kubectl call: $*" >&2
    exit 42
    ;;
esac
EOF
chmod +x "$TMP_DIR/bin/kubectl"

KUBECTL="$TMP_DIR/bin/kubectl" bash "$PREFLIGHT" core >/dev/null
KUBECTL="$TMP_DIR/bin/kubectl" bash "$PREFLIGHT" vault >/dev/null

for mode in missing-csi server-not-ready agent-not-ready empty-bundle entry-failure; do
  if SPIRE_TEST_MODE="$mode" KUBECTL="$TMP_DIR/bin/kubectl" \
    bash "$PREFLIGHT" core >/dev/null 2>&1; then
    echo "[FAIL] SPIRE preflight accepted ${mode}" >&2
    exit 1
  fi
done

echo "[PASS] SPIRE rollout preflight fails closed"
