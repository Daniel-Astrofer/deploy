#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=infra/scripts/polyrepo-env.sh
source "${SCRIPT_DIR}/polyrepo-env.sh"

MODE="${1:---check}"
CLEAN_SKIP_PATHS="${KEROSENE_CLEAN_SKIP_PATHS:-}"
if [[ "${MODE}" == "-h" || "${MODE}" == "--help" ]]; then
  cat <<'USAGE'
Usage: infra/scripts/clean-polyrepo-workspace.sh [--check|--apply]

Removes reproducible build caches from every active Kerosene repository and
linked worktree. The default is a dry run. Runtime state and sensitive material
(.local, var, wallets, seeds, macaroons, certificates and local secrets) are
always preserved. Optional operator-owned paths can be preserved for one run
with KEROSENE_CLEAN_SKIP_PATHS, using absolute paths separated by ':'.
USAGE
  exit 0
fi
if [[ "${MODE}" != "--check" && "${MODE}" != "--apply" ]]; then
  echo "Usage: infra/scripts/clean-polyrepo-workspace.sh [--check|--apply]" >&2
  exit 2
fi

removed_kib=0
candidate_count=0

path_size_kib() {
  du -sk -- "$1" 2>/dev/null | awk '{print $1}'
}

assert_safe_relative_path() {
  local relative="$1"
  if [[ -z "${relative}" || "${relative}" == "." || "${relative}" == "/" ||
        "${relative}" == ".." || "${relative}" == ../* || "${relative}" == */../* ]]; then
    echo "[clean][error] refusing unsafe relative path: ${relative}" >&2
    exit 5
  fi
}

is_skipped_path() {
  local target="$1"
  local skip_path
  local -a skip_paths=()

  [[ -n "${CLEAN_SKIP_PATHS}" ]] || return 1
  IFS=':' read -r -a skip_paths <<< "${CLEAN_SKIP_PATHS}"
  for skip_path in "${skip_paths[@]}"; do
    [[ -n "${skip_path}" && "${target}" == "${skip_path}" ]] && return 0
  done
  return 1
}

handle_path() {
  local repository_root="$1"
  local relative="$2"
  local target
  local size_kib

  assert_safe_relative_path "${relative}"
  target="${repository_root}/${relative}"
  if [[ ! -e "${target}" && ! -L "${target}" ]]; then
    return
  fi
  case "${target}" in
    "${repository_root}"/*) ;;
    *)
      echo "[clean][error] target escaped repository root: ${target}" >&2
      exit 5
      ;;
  esac

  if is_skipped_path "${target}"; then
    printf '[clean][preserved][operator-skip] %s\n' "${target}"
    return
  fi

  size_kib="$(path_size_kib "${target}")"
  size_kib="${size_kib:-0}"
  candidate_count=$((candidate_count + 1))
  removed_kib=$((removed_kib + size_kib))

  if [[ "${MODE}" == "--apply" ]]; then
    rm -rf -- "${target}"
    printf '[clean][removed] %s (%s KiB)\n' "${target}" "${size_kib}"
  else
    printf '[clean][candidate] %s (%s KiB)\n' "${target}" "${size_kib}"
  fi
}

clean_python_caches() {
  local repository_root="$1"
  local cache_dir
  while IFS= read -r -d '' cache_dir; do
    handle_path "${repository_root}" "${cache_dir#"${repository_root}/"}"
  done < <(
    find "${repository_root}" \
      \( -path "${repository_root}/.git" -o -type d -name .venv \) -prune -o \
      -type d \( -name __pycache__ -o -name .pytest_cache \) -print0
  )
}

clean_component_tree() {
  local component="$1"
  local repository_root="$2"

  case "${component}" in
    admin)
      handle_path "${repository_root}" .gradle
      handle_path "${repository_root}" build
      ;;
    clients)
      handle_path "${repository_root}" .dart_tool
      handle_path "${repository_root}" .flutter-plugins-dependencies
      handle_path "${repository_root}" build
      handle_path "${repository_root}" android/.gradle
      handle_path "${repository_root}" android/.kotlin/sessions
      handle_path "${repository_root}" android/app/build
      handle_path "${repository_root}" ios/Flutter/ephemeral
      handle_path "${repository_root}" ios/Flutter/Generated.xcconfig
      handle_path "${repository_root}" ios/Flutter/flutter_export_environment.sh
      handle_path "${repository_root}" ios/Runner/GeneratedPluginRegistrant.h
      handle_path "${repository_root}" ios/Runner/GeneratedPluginRegistrant.m
      handle_path "${repository_root}" linux/flutter/ephemeral
      handle_path "${repository_root}" macos/Flutter/ephemeral
      handle_path "${repository_root}" native/target
      handle_path "${repository_root}" third_party/tor/rust/target
      handle_path "${repository_root}" windows/flutter/ephemeral
      ;;
    contracts)
      handle_path "${repository_root}" .gradle
      handle_path "${repository_root}" build
      handle_path "${repository_root}" target
      ;;
    core)
      handle_path "${repository_root}" .gradle
      handle_path "${repository_root}" build
      handle_path "${repository_root}" auth-service/build
      ;;
    deploy)
      clean_python_caches "${repository_root}"
      ;;
    kfe)
      handle_path "${repository_root}" .gradle
      handle_path "${repository_root}" build
      ;;
    node)
      handle_path "${repository_root}" fuzz/target
      handle_path "${repository_root}" target
      ;;
    rails)
      clean_python_caches "${repository_root}"
      ;;
    shared)
      handle_path "${repository_root}" .gradle
      handle_path "${repository_root}" build
      ;;
    vault)
      handle_path "${repository_root}" target
      handle_path "${repository_root}" target-roomy
      ;;
    *)
      echo "[clean][error] unknown component: ${component}" >&2
      exit 4
      ;;
  esac
}

clean_linked_worktrees() {
  local component="$1"
  local repository_root="$2"
  local line
  local worktree_path

  while IFS= read -r line; do
    [[ "${line}" == worktree\ * ]] || continue
    worktree_path="${line#worktree }"
    [[ "${worktree_path}" != "${repository_root}" ]] || continue
    case "${worktree_path}" in
      "${KEROSENE_WORKSPACE_ROOT}/.worktrees/"*)
        clean_component_tree "${component}" "${worktree_path}"
        ;;
      *)
        printf '[clean][preserved] external worktree %s\n' "${worktree_path}"
        ;;
    esac
  done < <(git -C "${repository_root}" worktree list --porcelain 2>/dev/null || true)
}

components=(admin clients contracts core deploy kfe node rails shared vault)
repositories=(
  "${ADMIN_DIR}"
  "${CLIENTS_DIR}"
  "${CONTRACTS_DIR}"
  "${CORE_DIR}"
  "${REPO_ROOT}"
  "${KFE_DIR}"
  "${NODE_DIR}"
  "${RAILS_DIR}"
  "${SHARED_DIR}"
  "${VAULT_DIR}"
)

for index in "${!components[@]}"; do
  component="${components[$index]}"
  repository="${repositories[$index]}"
  require_kerosene_repo "${component}" "${repository}"
  clean_component_tree "${component}" "${repository}"
  clean_linked_worktrees "${component}" "${repository}"
done

for protected_path in \
  "${VAULT_DIR}/var" \
  "${REPO_ROOT}/.local" \
  "${KEROSENE_WORKSPACE_ROOT}/kerosene-deploy/.local"
do
  if [[ -e "${protected_path}" ]]; then
    printf '[clean][preserved] runtime or sensitive state %s\n' "${protected_path}"
  fi
done

if [[ "${MODE}" == "--apply" ]]; then
  printf '[clean] removed %s path(s), %s KiB before deletion\n' "${candidate_count}" "${removed_kib}"
else
  printf '[clean] %s removable path(s), %s KiB total; rerun with --apply\n' "${candidate_count}" "${removed_kib}"
fi
