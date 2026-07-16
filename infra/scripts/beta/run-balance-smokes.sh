#!/usr/bin/env bash
# Local gate for dual-ledger / cold balance work.
# Usage (repo root):
#   export KUBECONFIG=~/.kube/kind-config-kerosene-local
#   bash infra/scripts/beta/run-balance-smokes.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PY="${SMOKE_PYTHON:-$ROOT/.local/smoke-venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  PY="${PYTHON:-python3}"
fi

export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/kind-config-kerosene-local}"
export KEROSENE_NAMESPACE="${KEROSENE_NAMESPACE:-kerosene-local}"

echo "==> smoke-cold-e2e"
"$PY" "$ROOT/infra/scripts/beta/smoke-cold-e2e.py"
echo "==> smoke-money-e2e"
"$PY" "$ROOT/infra/scripts/beta/smoke-money-e2e.py"
echo "[+] balance smokes passed"
