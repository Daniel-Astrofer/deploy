#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# shellcheck source=infra/scripts/polyrepo-env.sh
source "$REPO_ROOT/infra/scripts/polyrepo-env.sh"

AUTH_DIR="$CORE_DIR/auth-service"
KFE_DIR="$CORE_DIR/kfe-service"
CORE_CONTRACTS_DIR="$CORE_DIR/kerosene-contracts"
SHARED_DIR="$CORE_DIR/kerosene-shared"

fail=0
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

run_grep_check() {
  local label="$1"
  shift
  : > "$TMP"
  if grep "$@" > "$TMP" 2>/dev/null; then
    true
  else
    : > "$TMP"
  fi

  if [ -s "$TMP" ]; then
    printf '%s\n' "FAIL: $label"
    sed -n '1,120p' "$TMP"
    fail=1
  else
    printf '%s\n' "OK: $label"
  fi
}

run_grep_check \
  "legacy financial feature flag must not exist in executable code" \
  -RIn --exclude-dir=.git --exclude-dir=build --exclude='verify-kfe-only.sh' 'kfe\.legacy-financial\.enabled' $AUTH_DIR/src $CLIENTS_DIR/lib

for pkg in ledger payments wallet bitcoinaccounts; do
  path="$AUTH_DIR/src/main/java/source/$pkg"
  if [ -d "$path" ]; then
    printf '%s\n' "FAIL: legacy backend package still exists: source/$pkg"
    fail=1
  else
    printf '%s\n' "OK: legacy backend package absent: source/$pkg"
  fi
done

run_grep_check \
  "legacy financial package dependencies must not exist" \
  -RInE --exclude-dir=.git --exclude-dir=build 'source\.(ledger|payments|wallet|bitcoinaccounts)' $AUTH_DIR/src/main $AUTH_DIR/src/test $KFE_DIR/src/main $KFE_DIR/src/test $CORE_CONTRACTS_DIR/src/main $SHARED_DIR/src/main


: > "$TMP"
if grep -RIn --exclude-dir=.git --exclude-dir=build -E 'import (source\.kfe\.|com\.kerosene\.kfe\.)' \
  $AUTH_DIR/src/main/java \
  $CORE_CONTRACTS_DIR/src/main/java \
  $SHARED_DIR/src/main/java \
  2>/dev/null \
  | grep -vE '/(source/kfe|com/kerosene/kfe)/' > "$TMP"; then
  :
else
  : > "$TMP"
fi

if [ -s "$TMP" ]; then
  printf '%s\n' "FAIL: non-KFE code must not import KFE implementation packages"
  sed -n '1,120p' "$TMP"
  fail=1
else
  printf '%s\n' "OK: non-KFE code does not import KFE implementation packages"
fi



: > "$TMP"
if grep -RIn --exclude-dir=.git --exclude-dir=build 'import source\.auth\.' \
  $KFE_DIR/src/main/java/com/kerosene/kfe \
  $KFE_DIR/src/main/java/source/kfe \
  $KFE_DIR/src/test/java/com/kerosene/kfe \
  $KFE_DIR/src/test/java/source/kfe \
  2>/dev/null > "$TMP"; then
  :
else
  : > "$TMP"
fi

if [ -s "$TMP" ]; then
  printf '%s\n' "FAIL: KFE code must not import auth implementation packages"
  sed -n '1,120p' "$TMP"
  fail=1
else
  printf '%s\n' "OK: KFE code does not import auth implementation packages"
fi



: > "$TMP"
if grep -RIn --exclude-dir=.git --exclude-dir=build 'import source\.notification\.' \
  $KFE_DIR/src/main/java/com/kerosene/kfe \
  $KFE_DIR/src/main/java/source/kfe \
  $KFE_DIR/src/test/java/com/kerosene/kfe \
  $KFE_DIR/src/test/java/source/kfe \
  2>/dev/null > "$TMP"; then
  :
else
  : > "$TMP"
fi

if [ -s "$TMP" ]; then
  printf '%s\n' "FAIL: KFE code must not import notification implementation packages"
  sed -n '1,120p' "$TMP"
  fail=1
else
  printf '%s\n' "OK: KFE code does not import notification implementation packages"
fi



: > "$TMP"
if grep -RIn --exclude-dir=.git --exclude-dir=build 'import source\.security\.' \
  $KFE_DIR/src/main/java/com/kerosene/kfe \
  $KFE_DIR/src/main/java/source/kfe \
  $KFE_DIR/src/test/java/com/kerosene/kfe \
  $KFE_DIR/src/test/java/source/kfe \
  2>/dev/null > "$TMP"; then
  :
else
  : > "$TMP"
fi

if [ -s "$TMP" ]; then
  printf '%s\n' "FAIL: KFE code must not import security implementation packages"
  sed -n '1,120p' "$TMP"
  fail=1
else
  printf '%s\n' "OK: KFE code does not import security implementation packages"
fi



: > "$TMP"
if grep -RIn --exclude-dir=.git --exclude-dir=build 'import source\.sovereign\.' \
  $KFE_DIR/src/main/java/com/kerosene/kfe \
  $KFE_DIR/src/main/java/source/kfe \
  $KFE_DIR/src/test/java/com/kerosene/kfe \
  $KFE_DIR/src/test/java/source/kfe \
  2>/dev/null > "$TMP"; then
  :
else
  : > "$TMP"
fi

if [ -s "$TMP" ]; then
  printf '%s\n' "FAIL: KFE code must not import sovereign implementation packages"
  sed -n '1,120p' "$TMP"
  fail=1
else
  printf '%s\n' "OK: KFE code does not import sovereign implementation packages"
fi



run_grep_check \
  "kerosene-contracts must not depend on implementation packages" \
  -RInE --exclude-dir=.git --exclude-dir=build 'import (source\.(kfe|auth|notification|security|sovereign)\.|com\.kerosene\.kfe\.|org\.springframework\.|jakarta\.persistence\.|javax\.persistence\.)' $CORE_CONTRACTS_DIR/src/main/java



run_grep_check \
  "kerosene-shared must not depend on implementation packages" \
  -RInE --exclude-dir=.git --exclude-dir=build 'import (source\.(kfe|auth|notification|security|sovereign)\.|com\.kerosene\.kfe\.)' $SHARED_DIR/src/main/java

run_grep_check \
  "legacy financial API routes must not exist in executable code" \
  -RInE --exclude-dir=.git --exclude-dir=build '"/(ledger|payments|deposit|treasury)(/|")|"/wallet/(create|all|find|update|delete)(/|")|"/bitcoin/(accounts|cold-wallets|psbt|receive|receive-requests|tax-events)(/|")|"/transactions/(estimate-fee|deposit-address|create-unsigned|broadcast|create-payment-link|payment-link|payment-links|withdraw)(/|")' $AUTH_DIR/src/main $KFE_DIR/src/main $CLIENTS_DIR/lib

run_grep_check \
  "legacy financial AppConfig aliases must not exist" \
  -RInE --exclude-dir=.git --exclude-dir=build '\b(walletCreate|walletAll|walletFind|ledgerAll|ledgerFind|ledgerBalance|ledgerHistory|ledgerTransaction|treasuryOverview|bitcoinTaxEvents|bitcoinTaxEventsExport|bitcoinTaxEventClassify|bitcoinPsbt|bitcoinPsbtSigned|bitcoinColdWalletPsbt|bitcoinColdWalletUtxos|bitcoinAccountReceiveRequests)\b' $CLIENTS_DIR/lib/core/config $CLIENTS_DIR/test/core/config

if [ "${STRICT_DOCS:-0}" = "1" ]; then
  run_grep_check \
    "legacy financial API routes must not exist in docs" \
    -RInE --exclude-dir=.git --exclude-dir=build '`/(ledger|payments|deposit|treasury)(/|`)|`/wallet/(create|all|find|update|delete)(/|`)|`/bitcoin/(accounts|cold-wallets|psbt|receive|receive-requests|tax-events)(/|`)|`/transactions/(estimate-fee|deposit-address|create-unsigned|broadcast|create-payment-link|payment-link|payment-links|withdraw)(/|`)' "$CORE_DIR/docs" "$CLIENTS_DIR/docs"
fi

exit "$fail"
