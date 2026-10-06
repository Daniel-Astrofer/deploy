#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

mkdir -m 0700 "$TMP/bootstrap" "$TMP/bin"
cat > "$TMP/bin/go" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
output=""
while (($#)); do
  if [[ "$1" == "-o" ]]; then
    output="$2"
    break
  fi
  shift
done
[[ -n "$output" ]]
printf '#!/usr/bin/env bash\nexit 0\n' > "$output"
SH
chmod 0700 "$TMP/bin/go"

first="$(PATH="$TMP/bin:$PATH" "$ROOT/infra/install-stack-controllers" --bootstrap-dir "$TMP/bootstrap")"
[[ "$first" == *"kerosene-release-consensus"* ]]
[[ "$first" == *"kerosene-cell-acceptance"* ]]
[[ -x "$TMP/bootstrap/kerosene-release-consensus" ]]
[[ -x "$TMP/bootstrap/kerosene-cell-acceptance" ]]
[[ "$(stat -c %a "$TMP/bootstrap/kerosene-release-consensus")" == 700 ]]
[[ "$(stat -c %a "$TMP/bootstrap/kerosene-cell-acceptance")" == 700 ]]

before="$(sha256sum "$TMP/bootstrap/kerosene-release-consensus" "$TMP/bootstrap/kerosene-cell-acceptance")"
if PATH="$TMP/bin:$PATH" "$ROOT/infra/install-stack-controllers" --bootstrap-dir "$TMP/bootstrap" >/dev/null 2>&1; then
  echo "installer unexpectedly overwrote existing controllers" >&2
  exit 1
fi
[[ "$before" == "$(sha256sum "$TMP/bootstrap/kerosene-release-consensus" "$TMP/bootstrap/kerosene-cell-acceptance")" ]]

mkdir -m 0700 "$TMP/partial"
printf 'preexisting\n' > "$TMP/partial/kerosene-cell-acceptance"
chmod 0700 "$TMP/partial/kerosene-cell-acceptance"
if PATH="$TMP/bin:$PATH" "$ROOT/infra/install-stack-controllers" --bootstrap-dir "$TMP/partial" >/dev/null 2>&1; then
  echo "installer accepted a partially occupied destination" >&2
  exit 1
fi
[[ ! -e "$TMP/partial/kerosene-release-consensus" ]]
[[ "$(cat "$TMP/partial/kerosene-cell-acceptance")" == preexisting ]]

mkdir "$TMP/shared"
chmod 0755 "$TMP/shared"
if PATH="$TMP/bin:$PATH" "$ROOT/infra/install-stack-controllers" --bootstrap-dir "$TMP/shared" >/dev/null 2>&1; then
  echo "installer accepted a shared bootstrap directory" >&2
  exit 1
fi

echo "[PASS] stack controller installer"
