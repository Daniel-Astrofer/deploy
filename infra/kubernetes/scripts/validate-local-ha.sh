#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
OVERLAY="$ROOT/backend/kerosene-infrastructure/k8s/overlays/local-ha"
kubectl kustomize "$OVERLAY" >/tmp/kerosene-local-ha.rendered.yaml
for n in server kfe-service web-page server-wvo server-iw5 server-ltv kfe-service-wvo kfe-service-iw5 kfe-service-ltv mpc-sidecar-wvo mpc-sidecar-iw5 mpc-sidecar-ltv db-wvo db-iw5 db-ltv redis-wvo redis-iw5 redis-ltv tor-wvo tor-iw5 tor-ltv vanguards-wvo vanguards-iw5 vanguards-ltv vault-raft-1 vault-raft-2 vault-raft-3; do
  grep -q "name: $n" /tmp/kerosene-local-ha.rendered.yaml || { echo "missing $n"; exit 1; }
done
grep -q "image: kerosene/kfe-service:local" /tmp/kerosene-local-ha.rendered.yaml || { echo "missing kfe-service local image"; exit 1; }
grep -q "value: docker,kfe" /tmp/kerosene-local-ha.rendered.yaml || { echo "missing kfe profile"; exit 1; }
grep -q "value: http://kfe-service-wvo:8080" /tmp/kerosene-local-ha.rendered.yaml || { echo "missing WVO Core to KFE route"; exit 1; }
grep -q "value: http://kfe-service-iw5:8080" /tmp/kerosene-local-ha.rendered.yaml || { echo "missing IW5 Core to KFE route"; exit 1; }
grep -q "value: http://kfe-service-ltv:8080" /tmp/kerosene-local-ha.rendered.yaml || { echo "missing LTV Core to KFE route"; exit 1; }
echo "[+] local-ha overlay renders successfully."
