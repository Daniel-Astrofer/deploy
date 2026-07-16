#!/usr/bin/env bash
# Seed cluster Redis with live BTC spot + 24h change from CoinGecko (host egress).
# Local-full server pods often cannot reach the internet; the home surface reads
# these keys via TickerService.
#
# Usage:
#   bash infra/scripts/local/seed-btc-ticker.sh
#   watch -n 300 bash infra/scripts/local/seed-btc-ticker.sh
set -euo pipefail

NS="${KEROSENE_NAMESPACE:-kerosene-local}"
REDIS_PASS="$(kubectl -n "$NS" get secret kerosene-redis-secrets -o jsonpath='{.data.redis-password}' | base64 -d)"
URL='https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd,brl,eur&include_24hr_change=true'

python3 - "$URL" <<'PY' > /tmp/btc_market.json
import json, sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=25) as r:
    json.dump(json.load(r)["bitcoin"], sys.stdout)
PY

usd=$(python3 -c 'import json;print(json.load(open("/tmp/btc_market.json"))["usd"])')
brl=$(python3 -c 'import json;print(json.load(open("/tmp/btc_market.json"))["brl"])')
eur=$(python3 -c 'import json;print(json.load(open("/tmp/btc_market.json"))["eur"])')
ch_usd=$(python3 -c 'import json;print(json.load(open("/tmp/btc_market.json")).get("usd_24h_change",""))')
ch_brl=$(python3 -c 'import json;print(json.load(open("/tmp/btc_market.json")).get("brl_24h_change",""))')
ch_eur=$(python3 -c 'import json;print(json.load(open("/tmp/btc_market.json")).get("eur_24h_change",""))')

redis() {
  kubectl -n "$NS" exec deploy/local-redis -- \
    redis-cli -a "$REDIS_PASS" --no-auth-warning "$@" >/dev/null
}

redis SET btc_price:usd "$usd" EX 900
redis SET btc_price:brl "$brl" EX 900
redis SET btc_price:eur "$eur" EX 900
[[ -n "$ch_usd" ]] && redis SET btc_change_24h:usd "$ch_usd" EX 900
[[ -n "$ch_brl" ]] && redis SET btc_change_24h:brl "$ch_brl" EX 900
[[ -n "$ch_eur" ]] && redis SET btc_change_24h:eur "$ch_eur" EX 900

echo "[seed-btc-ticker] usd=$usd change24h=${ch_usd}% brl=$brl (ttl 15m)"
