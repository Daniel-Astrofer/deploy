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
wrong_branch_repos=()
uncommitted_repos=()
outdated_repos=()
has_errors=0

for repository in "${repositories[@]}"; do
  branch="$(git -C "$repository" branch --show-current)"
  if [[ "$branch" != "main" ]]; then
    echo "[sync][blocked] $repository is on $branch, expected main" >&2
    wrong_branch_repos+=("$repository")
    has_errors=1
    continue
  fi
  if [[ -n "$(git -C "$repository" status --porcelain)" ]]; then
    echo "[sync][blocked] $repository has uncommitted work" >&2
    uncommitted_repos+=("$repository")
    has_errors=1
    continue
  fi
  git -C "$repository" fetch origin main
  if [[ "$MODE" == "--apply" ]]; then
    git -C "$repository" merge --ff-only origin/main
  elif [[ "$(git -C "$repository" rev-parse HEAD)" != "$(git -C "$repository" rev-parse origin/main)" ]]; then
    echo "[sync][outdated] $repository"
    outdated_repos+=("$repository")
    has_errors=1
  fi
done

if [[ "${#wrong_branch_repos[@]}" -gt 0 ]]; then
  echo "[sync] Repositories on wrong branch:" >&2
  printf '  %s\n' "${wrong_branch_repos[@]}" >&2
fi
if [[ "${#uncommitted_repos[@]}" -gt 0 ]]; then
  echo "[sync] Repositories with uncommitted work:" >&2
  printf '  %s\n' "${uncommitted_repos[@]}" >&2
fi
if [[ "${#wrong_branch_repos[@]}" -gt 0 || "${#uncommitted_repos[@]}" -gt 0 ]]; then
  exit 3
fi

if [[ "${#outdated_repos[@]}" -gt 0 ]]; then
  exit 4
fi

echo "Kerosene polyrepo workspace is synchronized."
