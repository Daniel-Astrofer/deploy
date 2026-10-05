#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_ROOT="${KEROSENE_DEPLOY_DIR:-$DEFAULT_REPO_ROOT}"
# shellcheck source=infra/scripts/polyrepo-env.sh
source "${SCRIPT_DIR}/polyrepo-env.sh"

MODE="${1:---check}"
if [[ "$#" -gt 1 || ( "$MODE" != "--check" && "$MODE" != "--apply" ) ]]; then
  echo "Usage: infra/scripts/sync-polyrepo-workspace.sh [--check|--apply]" >&2
  exit 2
fi

GITHUB_DIR="${KEROSENE_GITHUB_DIR:-${KEROSENE_WORKSPACE_ROOT}/.github}"
labels=(github admin clients contracts core deploy kfe node rails shared vault)
repositories=(
  "$GITHUB_DIR"
  "$ADMIN_DIR"
  "$CLIENTS_DIR"
  "$CONTRACTS_DIR"
  "$CORE_DIR"
  "$REPO_ROOT"
  "$KFE_DIR"
  "$NODE_DIR"
  "$RAILS_DIR"
  "$SHARED_DIR"
  "$VAULT_DIR"
)

branches=()
upstreams=()
remotes=()
preflight_errors=0

# Complete the preflight before fetching or changing any repository. This
# prevents a partial workspace update when a later checkout is dirty or has no
# configured upstream.
for index in "${!repositories[@]}"; do
  label="${labels[$index]}"
  repository="${repositories[$index]}"

  if ! git -C "$repository" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "[sync][blocked] $label is not a Git worktree: $repository" >&2
    preflight_errors=1
    branches+=("")
    upstreams+=("")
    remotes+=("")
    continue
  fi

  branch="$(git -C "$repository" branch --show-current)"
  if [[ -z "$branch" ]]; then
    echo "[sync][blocked] $label is detached at $repository" >&2
    preflight_errors=1
    branches+=("")
    upstreams+=("")
    remotes+=("")
    continue
  fi

  upstream="$(git -C "$repository" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
  if [[ -z "$upstream" ]]; then
    echo "[sync][blocked] $label branch $branch has no upstream" >&2
    preflight_errors=1
  fi

  remote="$(git -C "$repository" config --get "branch.${branch}.remote" || true)"
  if [[ -z "$remote" ]]; then
    echo "[sync][blocked] $label branch $branch has no remote configured" >&2
    preflight_errors=1
  fi

  if [[ -n "$(git -C "$repository" status --porcelain)" ]]; then
    echo "[sync][blocked] $label has uncommitted work: $repository" >&2
    preflight_errors=1
  fi

  branches+=("$branch")
  upstreams+=("$upstream")
  remotes+=("$remote")
done

if [[ "$preflight_errors" -ne 0 ]]; then
  echo "[sync] preflight failed; no working tree was updated." >&2
  exit 3
fi

ahead_counts=()
behind_counts=()
comparison_errors=0
non_fast_forward=0
out_of_sync=0

for index in "${!repositories[@]}"; do
  label="${labels[$index]}"
  repository="${repositories[$index]}"
  upstream="${upstreams[$index]}"
  remote="${remotes[$index]}"

  if [[ "$remote" != "." ]]; then
    git -C "$repository" fetch --prune "$remote"
  fi

  counts="$(git -C "$repository" rev-list --left-right --count "HEAD...${upstream}" 2>/dev/null || true)"
  if [[ ! "$counts" =~ ^[0-9]+[[:space:]]+[0-9]+$ ]]; then
    echo "[sync][blocked] cannot compare $label with $upstream" >&2
    comparison_errors=1
    ahead_counts+=(0)
    behind_counts+=(0)
    continue
  fi
  read -r ahead behind <<<"$counts"
  ahead_counts+=("$ahead")
  behind_counts+=("$behind")

  if [[ "$ahead" -eq 0 && "$behind" -eq 0 ]]; then
    echo "[sync][current] $label (${branches[$index]} -> $upstream)"
  elif [[ "$ahead" -eq 0 ]]; then
    echo "[sync][behind] $label by $behind commit(s) ($upstream)"
    out_of_sync=1
  elif [[ "$behind" -eq 0 ]]; then
    echo "[sync][ahead] $label by $ahead commit(s); push/review is required"
    non_fast_forward=1
    out_of_sync=1
  else
    echo "[sync][diverged] $label is $ahead ahead and $behind behind $upstream" >&2
    non_fast_forward=1
    out_of_sync=1
  fi
done

if [[ "$comparison_errors" -ne 0 ]]; then
  exit 3
fi

if [[ "$MODE" == "--check" ]]; then
  if [[ "$out_of_sync" -ne 0 ]]; then
    echo "[sync] workspace differs from one or more configured upstreams." >&2
    exit 4
  fi
  echo "Kerosene polyrepo workspace matches every configured upstream."
  exit 0
fi

if [[ "$non_fast_forward" -ne 0 ]]; then
  echo "[sync] apply refused: at least one checkout is ahead or diverged." >&2
  exit 4
fi

for index in "${!repositories[@]}"; do
  if [[ "${behind_counts[$index]}" -gt 0 ]]; then
    git -C "${repositories[$index]}" merge --ff-only "${upstreams[$index]}"
    echo "[sync][updated] ${labels[$index]} -> ${upstreams[$index]}"
  fi
done

echo "Kerosene polyrepo workspace was fast-forwarded to every configured upstream."
