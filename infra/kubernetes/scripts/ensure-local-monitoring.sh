#!/usr/bin/env bash
# Ensure Grafana + Prometheus are installed and running for local Kubernetes.
#
# Called by deploy-local-full / deploy.sh so every server bring-up also starts
# the monitoring stack (namespace: monitoring) and local port-forwards.
#
# Override:
#   KEROSENE_SKIP_MONITORING=1              skip entirely
#   KEROSENE_SKIP_MONITORING_PORT_FORWARD=1 skip only port-forwards
#   GRAFANA_ADMIN_USER=admin
#   GRAFANA_ADMIN_PASSWORD=admin
#   GRAFANA_LOCAL_PORT=3000
#   PROMETHEUS_LOCAL_PORT=9090
#   KUBE_PROM_RELEASE=kube-prom
#   MONITORING_NS=monitoring
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KUBECTL="${KUBECTL:-kubectl}"
HELM="${HELM:-helm}"

MONITORING_NS="${MONITORING_NS:-monitoring}"
KUBE_PROM_RELEASE="${KUBE_PROM_RELEASE:-kube-prom}"
GRAFANA_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-admin}"
GRAFANA_LOCAL_PORT="${GRAFANA_LOCAL_PORT:-3000}"
PROMETHEUS_LOCAL_PORT="${PROMETHEUS_LOCAL_PORT:-9090}"
CHART_REPO_NAME="${CHART_REPO_NAME:-prometheus-community}"
CHART_REPO_URL="${CHART_REPO_URL:-https://prometheus-community.github.io/helm-charts}"
CHART_NAME="${CHART_NAME:-kube-prometheus-stack}"
# Pin loosely so reinstalls stay compatible with what is already deployed.
CHART_VERSION="${CHART_VERSION:-}"

GRAFANA_SVC="${KUBE_PROM_RELEASE}-grafana"
PROMETHEUS_SVC="${KUBE_PROM_RELEASE}-kube-prometheus-prometheus"

PF_STATE_DIR="${KEROSENE_MONITORING_PF_DIR:-${HOME:-/tmp}/.local/state/kerosene/port-forwards}"

if [[ "${KEROSENE_SKIP_MONITORING:-0}" == "1" ]]; then
  echo "[*] Skipping monitoring stack (KEROSENE_SKIP_MONITORING=1)"
  exit 0
fi

kubectl_cmd() {
  "$KUBECTL" "$@"
}

port_is_listening() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk -v p=":${port}" '$4 ~ p"$" || $4 ~ p":" { found=1 } END { exit !found }'
    return
  fi
  if command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$port" >/dev/null 2>&1
    return
  fi
  # Fallback: try curl (works for HTTP listeners).
  curl -fsS -m 1 "http://127.0.0.1:${port}/" >/dev/null 2>&1
}

kill_pid_file() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 0
  fi
  local old_pid
  old_pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
    sleep 0.3
    kill -9 "$old_pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
}

# Start (or refresh) a detached kubectl port-forward; idempotent.
# Args: name namespace resource local_port:remote_port
ensure_port_forward() {
  local name="$1"
  local ns="$2"
  local resource="$3"
  local mapping="$4"
  local local_port="${mapping%%:*}"
  local pid_file="$PF_STATE_DIR/${name}.pid"
  local log_file="$PF_STATE_DIR/${name}.log"

  mkdir -p "$PF_STATE_DIR"

  if [[ -f "$pid_file" ]]; then
    local old_pid
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null && port_is_listening "$local_port"; then
      echo "[*] Port-forward $name already up (pid $old_pid) → 127.0.0.1:${local_port}"
      return 0
    fi
    kill_pid_file "$pid_file"
  fi

  # Drop stale kubectl port-forwards for this service/port (from prior deploys).
  pkill -f "kubectl.*port-forward.*-n ${ns}.*${resource}.*${mapping}" 2>/dev/null || true
  pkill -f "kubectl.*port-forward.*${resource}.*${mapping}" 2>/dev/null || true
  sleep 0.2

  if ! kubectl_cmd -n "$ns" get "$resource" >/dev/null 2>&1; then
    echo "[!] Cannot port-forward $name: $resource not found in $ns" >&2
    return 0
  fi

  echo "[*] Starting port-forward $name: $resource → 127.0.0.1:${local_port}"
  # nohup needs a real binary (not the kubectl_cmd shell function).
  local -a pf_cmd=("$KUBECTL")
  if [[ -n "${KUBECONFIG:-}" ]]; then
    pf_cmd+=(--kubeconfig "$KUBECONFIG")
  fi
  pf_cmd+=(-n "$ns" port-forward --address 127.0.0.1 "$resource" "$mapping")
  nohup "${pf_cmd[@]}" >"$log_file" 2>&1 &
  local new_pid=$!
  echo "$new_pid" >"$pid_file"
  disown "$new_pid" 2>/dev/null || true

  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if port_is_listening "$local_port"; then
      echo "[+] Port-forward $name ready → http://127.0.0.1:${local_port}  (pid $new_pid, log $log_file)"
      return 0
    fi
    if ! kill -0 "$new_pid" 2>/dev/null; then
      echo "[!] Port-forward $name exited early. Log: $log_file" >&2
      tail -n 20 "$log_file" 2>/dev/null >&2 || true
      rm -f "$pid_file"
      return 0
    fi
    sleep 0.5
  done
  echo "[!] Port-forward $name started (pid $new_pid) but port ${local_port} not yet listening." >&2
  echo "    Check: $log_file" >&2
}

start_monitoring_port_forwards() {
  if [[ "${KEROSENE_SKIP_MONITORING_PORT_FORWARD:-0}" == "1" ]]; then
    echo "[*] Skipping monitoring port-forwards (KEROSENE_SKIP_MONITORING_PORT_FORWARD=1)"
    return 0
  fi

  ensure_port_forward "grafana" "$MONITORING_NS" "svc/${GRAFANA_SVC}" "${GRAFANA_LOCAL_PORT}:80"
  ensure_port_forward "prometheus" "$MONITORING_NS" "svc/${PROMETHEUS_SVC}" "${PROMETHEUS_LOCAL_PORT}:9090"
}

if ! command -v "$KUBECTL" >/dev/null 2>&1; then
  echo "[!] kubectl not found; cannot ensure monitoring." >&2
  exit 0
fi

if ! kubectl_cmd cluster-info >/dev/null 2>&1; then
  echo "[!] Kubernetes API not reachable; skip monitoring." >&2
  exit 0
fi

echo "[*] Ensuring monitoring namespace: $MONITORING_NS"
kubectl_cmd create namespace "$MONITORING_NS" --dry-run=client -o yaml | kubectl_cmd apply -f - >/dev/null

if ! command -v "$HELM" >/dev/null 2>&1; then
  echo "[!] helm not found. Scaling existing Grafana/Prometheus if present..."
  kubectl_cmd -n "$MONITORING_NS" scale deploy -l app.kubernetes.io/name=grafana --replicas=1 2>/dev/null || true
  kubectl_cmd -n "$MONITORING_NS" scale sts -l app.kubernetes.io/name=prometheus --replicas=1 2>/dev/null || true
  start_monitoring_port_forwards
  exit 0
fi

if ! helm repo list 2>/dev/null | awk '{print $1}' | grep -qx "$CHART_REPO_NAME"; then
  echo "[*] Adding Helm repo $CHART_REPO_NAME"
  helm repo add "$CHART_REPO_NAME" "$CHART_REPO_URL" >/dev/null
fi
helm repo update "$CHART_REPO_NAME" >/dev/null 2>&1 || helm repo update >/dev/null 2>&1 || true

HELM_ARGS=(
  upgrade --install "$KUBE_PROM_RELEASE" "$CHART_REPO_NAME/$CHART_NAME"
  --namespace "$MONITORING_NS"
  --create-namespace
  --wait=false
  --timeout 15m
  --set grafana.adminUser="$GRAFANA_ADMIN_USER"
  --set grafana.adminPassword="$GRAFANA_ADMIN_PASSWORD"
  --set grafana.defaultDashboardsEnabled=true
  --set prometheus.prometheusSpec.retention=7d
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false
  --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false
  --set alertmanager.enabled=true
  --set nodeExporter.enabled=true
  --set kubeStateMetrics.enabled=true
)

if [[ -n "$CHART_VERSION" ]]; then
  HELM_ARGS+=(--version "$CHART_VERSION")
fi

echo "[*] Installing/upgrading $KUBE_PROM_RELEASE ($CHART_NAME) in $MONITORING_NS"
echo "[*] Grafana credentials: ${GRAFANA_ADMIN_USER} / ${GRAFANA_ADMIN_PASSWORD}"
helm "${HELM_ARGS[@]}"

# Always scale to 1 in case a previous stop zeroed replicas.
kubectl_cmd -n "$MONITORING_NS" scale deploy -l app.kubernetes.io/name=grafana --replicas=1 2>/dev/null || true
kubectl_cmd -n "$MONITORING_NS" scale sts -l app.kubernetes.io/name=prometheus --replicas=1 2>/dev/null || true
kubectl_cmd -n "$MONITORING_NS" scale sts -l app.kubernetes.io/name=alertmanager --replicas=1 2>/dev/null || true

# Align k8s secret with the password we set (runtime + secret for restarts).
if kubectl_cmd -n "$MONITORING_NS" get secret "${KUBE_PROM_RELEASE}-grafana" >/dev/null 2>&1; then
  PASS_B64="$(printf '%s' "$GRAFANA_ADMIN_PASSWORD" | base64 -w0 2>/dev/null || printf '%s' "$GRAFANA_ADMIN_PASSWORD" | base64)"
  USER_B64="$(printf '%s' "$GRAFANA_ADMIN_USER" | base64 -w0 2>/dev/null || printf '%s' "$GRAFANA_ADMIN_USER" | base64)"
  kubectl_cmd -n "$MONITORING_NS" patch secret "${KUBE_PROM_RELEASE}-grafana" --type merge \
    -p "{\"data\":{\"admin-user\":\"${USER_B64}\",\"admin-password\":\"${PASS_B64}\"}}" >/dev/null 2>&1 || true
fi

# Best-effort wait (do not fail deploy if monitoring is slow).
echo "[*] Waiting for Grafana and Prometheus (best-effort)..."
kubectl_cmd -n "$MONITORING_NS" rollout status "deploy/${KUBE_PROM_RELEASE}-grafana" --timeout=180s 2>/dev/null \
  || kubectl_cmd -n "$MONITORING_NS" rollout status deploy -l app.kubernetes.io/name=grafana --timeout=180s 2>/dev/null \
  || echo "[!] Grafana not ready yet (continuing)."
kubectl_cmd -n "$MONITORING_NS" rollout status "sts/prometheus-${KUBE_PROM_RELEASE}-kube-prometheus-prometheus" --timeout=180s 2>/dev/null \
  || kubectl_cmd -n "$MONITORING_NS" wait --for=condition=ready pod -l app.kubernetes.io/name=prometheus --timeout=180s 2>/dev/null \
  || echo "[!] Prometheus not ready yet (continuing)."

# Force admin password in live Grafana DB if pod is already up (idempotent).
GRAFANA_POD="$(kubectl_cmd -n "$MONITORING_NS" get pods -l app.kubernetes.io/name=grafana -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [[ -n "$GRAFANA_POD" ]]; then
  kubectl_cmd -n "$MONITORING_NS" exec "$GRAFANA_POD" -c grafana -- \
    grafana cli admin reset-admin-password "$GRAFANA_ADMIN_PASSWORD" >/dev/null 2>&1 \
    || true
fi

# Local access: always start (or reuse) port-forwards after stack is up.
start_monitoring_port_forwards

echo "[+] Monitoring stack ensured in namespace $MONITORING_NS"
echo "[+] Grafana:    http://127.0.0.1:${GRAFANA_LOCAL_PORT}  (login ${GRAFANA_ADMIN_USER} / ${GRAFANA_ADMIN_PASSWORD})"
echo "[+] Prometheus: http://127.0.0.1:${PROMETHEUS_LOCAL_PORT}"
echo "[+] Port-forward state: $PF_STATE_DIR"
