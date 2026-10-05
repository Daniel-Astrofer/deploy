#!/usr/bin/env bash
# Typed-ish configuration access for production gates.  Keep environment parsing
# in one place so rollout code never reads credentials or policy ad hoc.
set -euo pipefail

production_ops_dir() { printf '%s' "${KEROSENE_PRODUCTION_OPS_DIR:-}"; }
production_evidence_dir() { printf '%s' "${KEROSENE_PRODUCTION_EVIDENCE_DIR:-}"; }
required_image_names() { printf '%s\n' SERVER_IMAGE KFE_SERVICE_IMAGE WEB_PAGE_IMAGE VAULT_IMAGE NODE_IMAGE TOR_IMAGE; }

