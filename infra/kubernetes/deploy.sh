#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

usage() {
  cat <<'USAGE'
Usage: infra/kubernetes/deploy.sh [local|local-full|staging|production] [options]

Canonical local Kubernetes startup:
  bash infra/start.sh
  bash infra/deploy.sh
  bash infra/kubernetes/deploy.sh
  bash infra/kubernetes/deploy.sh local-full --wait

Environments:
  local        Apply the lightweight local overlay.
  local-full   Build/import local images, apply the complete local runtime, and optionally wait.
  staging      Apply the staging overlay.
  production   Apply the production overlay.

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
