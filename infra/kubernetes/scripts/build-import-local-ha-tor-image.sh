#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
TOR_SRC="$ROOT/infra/runtime/tor"
docker build -t kerosene/tor:local "$TOR_SRC"
if command -v ctr >/dev/null 2>&1; then
  if ctr -n k8s.io images ls >/dev/null 2>&1; then
    docker save kerosene/tor:local | ctr -n k8s.io images import -
  elif command -v sudo >/dev/null 2>&1; then
    docker save kerosene/tor:local | sudo ctr -n k8s.io images import -
  fi
fi
