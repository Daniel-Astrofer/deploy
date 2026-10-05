#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUBJECT="${REPO_ROOT}/infra/scripts/clean-polyrepo-workspace.sh"
TMP_DIR="$(mktemp -d)"
WORKSPACE="${TMP_DIR}/workspace"

cleanup() {
  rm -rf -- "${TMP_DIR}"
}
trap cleanup EXIT

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

for repository in admin clients contracts core deploy kfe node rails shared vault; do
  mkdir -p "${WORKSPACE}/${repository}/.git"
done

mkdir -p \
  "${WORKSPACE}/clients/.dart_tool" \
  "${WORKSPACE}/clients/android/.kotlin/sessions" \
  "${WORKSPACE}/clients/ios/Flutter/ephemeral" \
  "${WORKSPACE}/clients/native/target" \
  "${WORKSPACE}/contracts/target" \
  "${WORKSPACE}/core/auth-service/build" \
  "${WORKSPACE}/node/target" \
  "${WORKSPACE}/node/fuzz/target" \
  "${WORKSPACE}/rails/tests/__pycache__" \
  "${WORKSPACE}/rails/tests/.venv/lib/python3.13/__pycache__" \
  "${WORKSPACE}/vault/target" \
  "${WORKSPACE}/vault/var/ceremony-certs"

: > "${WORKSPACE}/clients/.dart_tool/cache"
: > "${WORKSPACE}/clients/android/.kotlin/sessions/cache"
: > "${WORKSPACE}/clients/ios/Flutter/ephemeral/generated"
: > "${WORKSPACE}/clients/native/target/artifact"
: > "${WORKSPACE}/contracts/target/artifact"
: > "${WORKSPACE}/core/auth-service/build/artifact"
: > "${WORKSPACE}/node/target/artifact"
: > "${WORKSPACE}/node/fuzz/target/artifact"
: > "${WORKSPACE}/rails/tests/__pycache__/test_cache.pyc"
: > "${WORKSPACE}/rails/tests/.venv/lib/python3.13/__pycache__/runtime.pyc"
: > "${WORKSPACE}/vault/target/artifact"
: > "${WORKSPACE}/vault/var/ceremony-certs/private.key"

check_output="$(KEROSENE_WORKSPACE_ROOT="${WORKSPACE}" \
  bash "${SUBJECT}" --check 2>&1)" || fail "dry run failed"

[[ -d "${WORKSPACE}/node/target" ]] || fail "dry run removed a cache"
grep -qF "${WORKSPACE}/node/target" <<<"${check_output}" || fail "dry run omitted node target"

KEROSENE_WORKSPACE_ROOT="${WORKSPACE}" bash "${SUBJECT}" --apply >/dev/null

[[ ! -e "${WORKSPACE}/clients/.dart_tool" ]] || fail "Flutter cache was not removed"
[[ ! -e "${WORKSPACE}/clients/android/.kotlin/sessions" ]] || fail "Kotlin session cache was not removed"
[[ ! -e "${WORKSPACE}/clients/ios/Flutter/ephemeral" ]] || fail "iOS Flutter output was not removed"
[[ ! -e "${WORKSPACE}/clients/native/target" ]] || fail "Native Rust target was not removed"
[[ ! -e "${WORKSPACE}/contracts/target" ]] || fail "Contracts target was not removed"
[[ ! -e "${WORKSPACE}/core/auth-service/build" ]] || fail "Core build was not removed"
[[ ! -e "${WORKSPACE}/node/target" ]] || fail "Node target was not removed"
[[ ! -e "${WORKSPACE}/node/fuzz/target" ]] || fail "Node fuzz target was not removed"
[[ ! -e "${WORKSPACE}/rails/tests/__pycache__" ]] || fail "Python cache was not removed"
[[ -e "${WORKSPACE}/rails/tests/.venv/lib/python3.13/__pycache__" ]] || fail "Python virtual environment cache was not preserved"
[[ ! -e "${WORKSPACE}/vault/target" ]] || fail "Vault target was not removed"
[[ -f "${WORKSPACE}/vault/var/ceremony-certs/private.key" ]] || fail "Vault secret was removed"

echo "[PASS] safe polyrepo cache cleanup"
