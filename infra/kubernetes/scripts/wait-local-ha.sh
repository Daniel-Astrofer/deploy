#!/usr/bin/env bash
set -euo pipefail
ns=kerosene-local-ha
for s in db-wvo db-iw5 db-ltv mpc-sidecar-wvo mpc-sidecar-iw5 mpc-sidecar-ltv; do kubectl -n "$ns" rollout status "statefulset/$s" --timeout=240s; done
for d in bitcoin-core lnd-placeholder web-page vault-raft-1 vault-raft-2 vault-raft-3 redis-wvo redis-iw5 redis-ltv tor-wvo tor-iw5 tor-ltv vanguards-wvo vanguards-iw5 vanguards-ltv server-wvo server-iw5 server-ltv kfe-service-wvo kfe-service-iw5 kfe-service-ltv; do kubectl -n "$ns" rollout status "deployment/$d" --timeout=300s; done
kubectl -n "$ns" get pods,svc
