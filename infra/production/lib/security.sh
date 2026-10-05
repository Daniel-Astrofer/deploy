#!/usr/bin/env bash
set -euo pipefail

require_immutable_images() {
  local failed=0 name value
  while IFS= read -r name; do
    value="${!name:-}"
    if [[ ! "$value" =~ @sha256:[0-9a-f]{64}$ ]]; then
      echo "[production][missing] immutable ${name}" >&2
      failed=1
    fi
  done < <(required_image_names)
  return "$failed"
}

verify_signed_evidence() {
  local evidence_dir="$1" gate="$2"
  bash "$PRODUCTION_ROOT/verify-evidence.sh" "$evidence_dir" "$gate"
}

