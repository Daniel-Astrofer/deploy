#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/polyrepo-env.sh
source "$SCRIPT_DIR/polyrepo-env.sh"

MODE="${1:---check}"
if [[ "$MODE" != "--check" && "$MODE" != "--apply" ]]; then
  echo "Usage: scripts/sync-polyrepo-workspace.sh [--check|--apply]" >&2
  exit 2
fi

repositories=("$CORE_DIR" "$CLIENTS_DIR" "$VAULT_DIR" "$NODE_DIR" "$CONTRACTS_DIR" "$REPO_ROOT")
for repository in "${repositories[@]}"; do
  branch="$(git -C "$repository" branch --show-current)"
  if [[ "$branch" != "main" ]]; then
    echo "[sync][blocked] $repository is on $branch, expected main" >&2
    exit 3
  fi
  if [[ -n "$(git -C "$repository" status --porcelain)" ]]; then
    echo "[sync][blocked] $repository has uncommitted work" >&2
    exit 3
  fi
  git -C "$repository" fetch origin main
  if [[ "$MODE" == "--apply" ]]; then
    git -C "$repository" merge --ff-only origin/main
  elif [[ "$(git -C "$repository" rev-parse HEAD)" != "$(git -C "$repository" rev-parse origin/main)" ]]; then
    echo "[sync][outdated] $repository"
    exit 4
  fi
done

echo "Kerosene polyrepo workspace is synchronized."
