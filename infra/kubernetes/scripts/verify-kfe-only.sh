#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

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
  -RIn --exclude-dir=.git --exclude-dir=build --exclude='verify-kfe-only.sh' 'kfe\.legacy-financial\.enabled' backend/kerosene/src frontend/lib

for pkg in ledger payments wallet bitcoinaccounts; do
  path="backend/kerosene/src/main/java/source/$pkg"
  if [ -d "$path" ]; then
    printf '%s\n' "FAIL: legacy backend package still exists: source/$pkg"
    fail=1
  else
    printf '%s\n' "OK: legacy backend package absent: source/$pkg"
  fi
done

run_grep_check \
  "legacy financial package dependencies must not exist" \
  -RInE --exclude-dir=.git --exclude-dir=build 'source\.(ledger|payments|wallet|bitcoinaccounts)' backend/kerosene/src/main backend/kerosene/src/test backend/kerosene/kfe-service/src/main backend/kerosene/kfe-service/src/test backend/kerosene/kerosene-contracts/src/main backend/kerosene/kerosene-shared/src/main


: > "$TMP"
if grep -RIn --exclude-dir=.git --exclude-dir=build 'import source\.kfe\.' backend/kerosene/src/main/java backend/kerosene/src/test/java 2>/dev/null \
  | grep -v '/source/kfe/' > "$TMP"; then
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
if grep -RIn --exclude-dir=.git --exclude-dir=build 'import source\.auth\.' backend/kerosene/kfe-service/src/main/java/source/kfe backend/kerosene/kfe-service/src/test/java/source/kfe 2>/dev/null > "$TMP"; then
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
if grep -RIn --exclude-dir=.git --exclude-dir=build 'import source\.notification\.' backend/kerosene/kfe-service/src/main/java/source/kfe backend/kerosene/kfe-service/src/test/java/source/kfe 2>/dev/null > "$TMP"; then
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
if grep -RIn --exclude-dir=.git --exclude-dir=build 'import source\.security\.' backend/kerosene/kfe-service/src/main/java/source/kfe backend/kerosene/kfe-service/src/test/java/source/kfe 2>/dev/null > "$TMP"; then
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
if grep -RIn --exclude-dir=.git --exclude-dir=build 'import source\.sovereign\.' backend/kerosene/kfe-service/src/main/java/source/kfe backend/kerosene/kfe-service/src/test/java/source/kfe 2>/dev/null > "$TMP"; then
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
  -RInE --exclude-dir=.git --exclude-dir=build 'import (source\.(kfe|auth|notification|security|sovereign)\.|org\.springframework\.|jakarta\.persistence\.|javax\.persistence\.)' backend/kerosene/kerosene-contracts/src/main/java



run_grep_check \
  "kerosene-shared must not depend on implementation packages" \
  -RInE --exclude-dir=.git --exclude-dir=build 'import source\.(kfe|auth|notification|security|sovereign)\.' backend/kerosene/kerosene-shared/src/main/java

run_grep_check \
  "legacy financial API routes must not exist in executable code" \
  -RInE --exclude-dir=.git --exclude-dir=build '"/(ledger|payments|deposit|treasury)(/|")|"/wallet/(create|all|find|update|delete)(/|")|"/bitcoin/(accounts|cold-wallets|psbt|receive|receive-requests|tax-events)(/|")|"/transactions/(estimate-fee|deposit-address|create-unsigned|broadcast|create-payment-link|payment-link|payment-links|withdraw)(/|")' backend/kerosene/src/main backend/kerosene/kfe-service/src/main frontend/lib

run_grep_check \
  "legacy financial AppConfig aliases must not exist" \
  -RInE --exclude-dir=.git --exclude-dir=build '\b(walletCreate|walletAll|walletFind|ledgerAll|ledgerFind|ledgerBalance|ledgerHistory|ledgerTransaction|treasuryOverview|bitcoinTaxEvents|bitcoinTaxEventsExport|bitcoinTaxEventClassify|bitcoinPsbt|bitcoinPsbtSigned|bitcoinColdWalletPsbt|bitcoinColdWalletUtxos|bitcoinAccountReceiveRequests)\b' frontend/lib/core/config frontend/test/core/config

if [ "${STRICT_DOCS:-0}" = "1" ]; then
  run_grep_check \
    "legacy financial API routes must not exist in docs" \
    -RInE --exclude-dir=.git --exclude-dir=build '`/(ledger|payments|deposit|treasury)(/|`)|`/wallet/(create|all|find|update|delete)(/|`)|`/bitcoin/(accounts|cold-wallets|psbt|receive|receive-requests|tax-events)(/|`)|`/transactions/(estimate-fee|deposit-address|create-unsigned|broadcast|create-payment-link|payment-link|payment-links|withdraw)(/|`)' docs
fi

exit "$fail"
