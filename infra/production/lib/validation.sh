#!/usr/bin/env bash
set -euo pipefail

require_commands() {
  local failed=0 command_name
  for command_name in "$@"; do
    command -v "$command_name" >/dev/null 2>&1 || {
      echo "[production][missing] command: $command_name" >&2
      failed=1
    }
  done
  return "$failed"
}

require_files() {
  local failed=0 file
  for file in "$@"; do
    [[ -f "$file" ]] || { echo "[production][missing] file: $file" >&2; failed=1; }
  done
  return "$failed"
}

validate_rendered_manifest() { bash "$PRODUCTION_ROOT/validate-manifest.sh" "$1"; }

