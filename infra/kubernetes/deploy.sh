#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

usage() {
  cat <<'USAGE'
Usage: infra/kubernetes/deploy.sh [local|local-full|staging] [options]

Canonical local Kubernetes startup (vault mesh + local-full):
  bash infra/start.sh
  bash infra/deploy.sh
  bash infra/kubernetes/deploy.sh
  bash infra/kubernetes/deploy.sh local-full --wait

Environments:
  local        Apply the lightweight local overlay.
  local-full   Build/import local images, start vault mesh compose, apply
               local-full (kfe vaultmesh on; mpc-sidecar / HashiCorp vault off).
  staging      Apply the staging overlay.

Default local/beta settlement: testnet3 vault-mesh-lab (dealer_lab + static_token).
Opt-in profiles via KEROSENE_VAULT_MESH_PROFILE:
  staging  — mTLS mesh (falls back to lab if certs missing)
  tor      — vault-mesh-tor + distributed_wire (no clearnet vault ports)

Production overlays/helpers are kept outside the public repository.

Local deploys always ensure Grafana + Prometheus in namespace monitoring
and start local port-forwards (Grafana :3000, Prometheus :9090).
See scripts/ensure-local-monitoring.sh.
Skip stack with KEROSENE_SKIP_MONITORING=1; skip only PF with
KEROSENE_SKIP_MONITORING_PORT_FORWARD=1.

Common options:
  --dry-run            Validate without persisting resources.
  --wait               local-full only: wait for workloads after apply.
  --skip-image-import  local-full only: use images already available to the cluster.

Specialized helper scripts remain under infra/kubernetes/scripts/.
From infra/, use ./deploy.sh.
USAGE
}

environment="${1:-}"
case "$environment" in
  "")
    exec "$ROOT/infra/kubernetes/scripts/deploy-local-full.sh" --wait
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  --*)
    exec "$ROOT/infra/kubernetes/scripts/deploy-local-full.sh" "$@"
    ;;
  local-full)
    shift
    exec "$ROOT/infra/kubernetes/scripts/deploy-local-full.sh" "$@"
    ;;
  *)
    exec "$ROOT/infra/kubernetes/scripts/deploy.sh" "$@"
    ;;
esac
