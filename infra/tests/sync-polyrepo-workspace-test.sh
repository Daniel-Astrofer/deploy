#!/usr/bin/env bash
set -euo pipefail

DEPLOY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUBJECT="$DEPLOY_ROOT/infra/scripts/sync-polyrepo-workspace.sh"
TMP_DIR="$(mktemp -d)"
WORKSPACE="$TMP_DIR/workspace"
REMOTES="$TMP_DIR/remotes"

cleanup() { rm -rf -- "$TMP_DIR"; }
trap cleanup EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }

make_repository() {
  local name="$1"
  git init --bare -b main "$REMOTES/$name.git" >/dev/null
  git init -b main "$WORKSPACE/$name" >/dev/null
  git -C "$WORKSPACE/$name" config user.name "Kerosene Test"
  git -C "$WORKSPACE/$name" config user.email "test@invalid.example"
  printf '%s\n' "initial-$name" >"$WORKSPACE/$name/state.txt"
  git -C "$WORKSPACE/$name" add state.txt
  git -C "$WORKSPACE/$name" commit -m initial >/dev/null
  git -C "$WORKSPACE/$name" remote add origin "$REMOTES/$name.git"
  git -C "$WORKSPACE/$name" push -u origin main >/dev/null
}

mkdir -p "$WORKSPACE" "$REMOTES"
for repository in .github admin clients contracts core deploy kfe node rails shared vault; do
  make_repository "$repository"
done

# A non-main branch with a configured upstream is valid.
git -C "$WORKSPACE/.github" switch -c docs/test >/dev/null
git -C "$WORKSPACE/.github" push -u origin docs/test >/dev/null

run_subject() {
  KEROSENE_DEPLOY_DIR="$WORKSPACE/deploy" \
  KEROSENE_WORKSPACE_ROOT="$WORKSPACE" \
  bash "$SUBJECT" "$@"
}

run_subject --check >/dev/null || fail "clean workspace did not pass"

git clone "$REMOTES/node.git" "$TMP_DIR/node-upstream" >/dev/null
git -C "$TMP_DIR/node-upstream" config user.name "Kerosene Test"
git -C "$TMP_DIR/node-upstream" config user.email "test@invalid.example"
printf '%s\n' upstream >"$TMP_DIR/node-upstream/state.txt"
git -C "$TMP_DIR/node-upstream" commit -am upstream >/dev/null
git -C "$TMP_DIR/node-upstream" push >/dev/null

if run_subject --check >/dev/null 2>&1; then
  fail "behind checkout was unexpectedly accepted"
fi
run_subject --apply >/dev/null || fail "fast-forward apply failed"
grep -q '^upstream$' "$WORKSPACE/node/state.txt" || fail "node was not fast-forwarded"

printf '%s\n' dirty >>"$WORKSPACE/core/state.txt"
if run_subject --apply >/dev/null 2>&1; then
  fail "dirty checkout was unexpectedly updated"
fi

echo "[PASS] upstream-aware polyrepo synchronization"
