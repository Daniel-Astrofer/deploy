#!/usr/bin/env bash
# Sourced by installed smoke probes; argv is never evaluated as shell code.
KUBECTL_COMMAND=("${KUBECTL:-kubectl}")
if [[ $# -ne 0 ]]; then
  if [[ $# -ne 4 || "$1" != --cell-binding || "$2" != /* || "$3" != /* || -z "$4" ]]; then
    echo "[!] Expected --cell-binding ABSOLUTE_KUBECTL ABSOLUTE_KUBECONFIG CONTEXT." >&2
    exit 64
  fi
  # The approved controller supplies these values from its verified bootstrap.
  # Do not allow ambient wrappers or namespace/port redirection in this path.
  if [[ -n "${KUBECTL:-}" || -n "${KEROSENE_STAGING_NAMESPACE:-}" ||
        -n "${KEROSENE_STAGING_VAULT_NAMESPACE:-}" || -n "${KEROSENE_STAGING_LOGIN_PORT:-}" ||
        -n "${KEROSENE_STAGING_VAULT_SMOKE_PORT:-}" ]]; then
    echo "[!] Bound Cell smoke forbids tool/namespace/port overrides." >&2
    exit 64
  fi
  KUBECTL_COMMAND=("$2" --kubeconfig "$3" --context "$4" --request-timeout=30s)
fi
