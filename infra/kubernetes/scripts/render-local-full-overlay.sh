#!/usr/bin/env bash
set -euo pipefail

# Render overlays/local-full into a temporary kustomize tree with host paths
# rewritten for the active workstation. Prints the work overlay path on stdout.
# Optional second line on stderr with cleanup path ownership for the caller.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$K8S_DIR/../.." && pwd)"

# shellcheck source=infra/kubernetes/scripts/local-host-env.sh
source "$SCRIPT_DIR/local-host-env.sh"
kerosene_load_local_host_env "$REPO_ROOT"
kerosene_prepare_local_host_paths

OVERLAY_SRC="$K8S_DIR/overlays/local-full"
if [[ ! -d "$OVERLAY_SRC" ]]; then
  echo "[!] local-full overlay not found: $OVERLAY_SRC" >&2
  exit 1
fi

WORK_ROOT="${KEROSENE_RENDER_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/kerosene-local-full-XXXXXX")}"
WORK_K8S="$WORK_ROOT/kubernetes"
WORK_OVERLAY="$WORK_K8S/overlays/local-full"

rm -rf "$WORK_K8S"
mkdir -p "$WORK_K8S/overlays"
cp -a "$K8S_DIR/base" "$WORK_K8S/base"
cp -a "$K8S_DIR/components" "$WORK_K8S/components"
cp -a "$OVERLAY_SRC" "$WORK_OVERLAY"

while IFS= read -r -d '' file; do
  kerosene_rewrite_legacy_host_paths "$file"
done < <(find "$WORK_OVERLAY" -type f \( -name '*.yaml' -o -name '*.yml' \) -print0)

# Verify critical paths were rewritten away from the legacy omega markers when
# the active host is not the legacy workstation.
if [[ "$KEROSENE_HOST_HOME" != "$KEROSENE_LEGACY_HOST_HOME" ]] \
  || [[ "$KEROSENE_REPO_ROOT" != "$KEROSENE_LEGACY_REPO_ROOT" ]]; then
  if grep -R --fixed-strings -- "$KEROSENE_LEGACY_HOST_HOME" "$WORK_OVERLAY" >/dev/null 2>&1; then
    echo "[!] Failed to rewrite legacy host paths under $WORK_OVERLAY" >&2
    grep -R --fixed-strings -- "$KEROSENE_LEGACY_HOST_HOME" "$WORK_OVERLAY" >&2 || true
    exit 1
  fi
fi

# Expose the work root so callers can trap-cleanup when they created the dir.
if [[ -z "${KEROSENE_RENDER_ROOT:-}" ]]; then
  echo "$WORK_ROOT" >"$WORK_ROOT/.kerosene-render-root"
fi

printf '%s\n' "$WORK_OVERLAY"
