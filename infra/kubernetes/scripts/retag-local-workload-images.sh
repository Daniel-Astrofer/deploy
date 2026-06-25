#!/usr/bin/env bash
set -euo pipefail

docker tag kerosene/kerosene-app:local kerosene/server:local 2>/dev/null || true
docker tag kerosene/web-admin:local kerosene/web-page:local 2>/dev/null || true

docker image inspect kerosene/server:local >/dev/null
docker image inspect kerosene/kfe-service:local >/dev/null
docker image inspect kerosene/mpc-sidecar:local >/dev/null
docker image inspect kerosene/web-page:local >/dev/null

echo "[+] Local workload images are available with canonical Kubernetes names."
