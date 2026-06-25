#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${1:-kerosene-production}"
KUBECTL="${KUBECTL:-kubectl}"
OUT_DIR="${OUT_DIR:-/tmp/kerosene-k8s-diagnostics-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT_DIR/logs" "$OUT_DIR/describes"

redact() {
  sed -E \
    -e 's/(password|secret|token|macaroon|key)([^[:alnum:]_-]*:?[[:space:]]*)[^[:space:]]+/\1\2[REDACTED]/Ig' \
    -e 's/(Authorization: Bearer )[A-Za-z0-9._-]+/\1[REDACTED]/Ig'
}

run() {
  local name="$1"; shift
  echo "[*] $name"
  { "$@" 2>&1 || true; } | redact > "$OUT_DIR/$name.txt"
}

run version "$KUBECTL" version --short
run nodes "$KUBECTL" get nodes -o wide
run namespace "$KUBECTL" get namespace "$NAMESPACE" -o yaml
run all "$KUBECTL" -n "$NAMESPACE" get all -o wide
run pvc "$KUBECTL" -n "$NAMESPACE" get pvc -o wide
run events "$KUBECTL" -n "$NAMESPACE" get events --sort-by=.lastTimestamp
run hpa "$KUBECTL" -n "$NAMESPACE" get hpa -o wide
run pdb "$KUBECTL" -n "$NAMESPACE" get pdb -o wide
run networkpolicies "$KUBECTL" -n "$NAMESPACE" get networkpolicy -o yaml

mapfile -t PODS < <("$KUBECTL" -n "$NAMESPACE" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' || true)
for pod in "${PODS[@]}"; do
  [[ -z "$pod" ]] && continue
  run "describes/$pod" "$KUBECTL" -n "$NAMESPACE" describe pod "$pod"
  mapfile -t CONTAINERS < <("$KUBECTL" -n "$NAMESPACE" get pod "$pod" -o jsonpath='{range .spec.containers[*]}{.name}{"\n"}{end}' || true)
  for c in "${CONTAINERS[@]}"; do
    [[ -z "$c" ]] && continue
    run "logs/${pod}_${c}_current" "$KUBECTL" -n "$NAMESPACE" logs "$pod" -c "$c" --tail=500
    run "logs/${pod}_${c}_previous" "$KUBECTL" -n "$NAMESPACE" logs "$pod" -c "$c" --previous --tail=500
  done
done

ARCHIVE="${OUT_DIR}.tar.gz"
tar -C "$(dirname "$OUT_DIR")" -czf "$ARCHIVE" "$(basename "$OUT_DIR")"
echo "[+] Diagnostics written to: $ARCHIVE"
