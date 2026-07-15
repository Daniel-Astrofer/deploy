#!/usr/bin/env bash
set -euo pipefail

HOST_SERVICES=(containerd docker kubelet)

host_info() {
  echo "[*] $*"
}

host_warn() {
  echo "[!] $*" >&2
}

systemd_available() {
  command -v systemctl >/dev/null 2>&1
}

service_exists() {
  local unit="$1"
  systemctl list-unit-files "$unit" --no-legend >/dev/null 2>&1
}

service_active() {
  local unit="$1"
  systemctl is-active --quiet "$unit" >/dev/null 2>&1
}

start_service() {
  local unit="$1"

  if systemctl start "$unit" >/dev/null 2>&1; then
    return 0
  fi

  if command -v sudo >/dev/null 2>&1; then
    if sudo -n true >/dev/null 2>&1; then
      sudo -n systemctl start "$unit"
      return $?
    fi

    if [[ -t 0 ]]; then
      sudo systemctl start "$unit"
      return $?
    fi
  fi

  return 1
}

ensure_local_host_services() {
  if [[ "${KEROSENE_AUTO_START_HOST_SERVICES:-1}" == "0" ]]; then
    return 0
  fi

  if ! systemd_available; then
    host_warn "systemctl not found; skipping host service autostart."
    return 0
  fi

  local service unit
  for service in "${HOST_SERVICES[@]}"; do
    unit="$service.service"
    if ! service_exists "$unit"; then
      host_warn "Host service not found, skipping: $unit"
      continue
    fi

    if service_active "$unit"; then
      host_info "Host service active: $unit"
      continue
    fi

    host_info "Starting host service: $unit"
    if ! start_service "$unit"; then
      host_warn "Could not start $unit. Run 'sudo systemctl start $unit' or set KEROSENE_AUTO_START_HOST_SERVICES=0 to skip autostart."
      return 1
    fi
  done
}
