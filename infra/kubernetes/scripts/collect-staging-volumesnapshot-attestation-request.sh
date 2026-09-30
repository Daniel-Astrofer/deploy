#!/usr/bin/env bash
#
# Collect an unsigned request for external attestation of the Kubernetes
# VolumeSnapshots that protect the canonical staging Cell state.  This script
# deliberately has no signing material, private-key handling, or receipt
# generation: its JSON output is only an input to an independently trusted
# attester.
set -euo pipefail

readonly STAGING_NAMESPACE="kerosene-staging"
readonly VAULT_NAMESPACE="kerosene-staging-vault"
readonly SNAPSHOT_RESOURCE="volumesnapshots.snapshot.storage.k8s.io"

KUBECTL_BIN="${KUBECTL:-kubectl}"
KUBE_CONTEXT=""
RELEASE_ID=""
RELEASE_LOCK_DIGEST=""
SNAPSHOT_SELECTOR=""
REQUESTED_AT=""
OUTPUT="-"
TMP_DIR=""
OUTPUT_TMP=""

usage() {
  cat <<'EOF'
Usage:
  collect-staging-volumesnapshot-attestation-request.sh \
    --release-id <immutable-release-id> \
    --release-lock-digest sha256:<64-hex> \
    [--snapshot-selector <kubernetes-label-selector>] \
    [--context <kube-context>] \
    [--requested-at <RFC3339-UTC>] \
    [--output <path>|-]

Lists VolumeSnapshots through kubectl and emits an UNSIGNED attestation
request for the complete canonical Kerosene staging Cell storage set:

  kerosene-staging:       data-staging-{postgres,redis,bitcoin,lnd,tor}-0,
                           vault-{1,2,3}-data
  kerosene-staging-vault: vault-data, data-vault-tor-0

Exactly one snapshot is required for every PVC.  Every returned snapshot must
be readyToUse=true, originate from one of those PVCs, be bound to snapshot
content, and not be marked for deletion.  Any missing, duplicate, malformed,
or unexpected snapshot causes a fail-closed exit and no payload is emitted.

Use --snapshot-selector to isolate one intentionally created release snapshot
set when historical snapshots are retained in the namespace.  The script never
signs output and never creates a trusted receipt.
EOF
}

fail() {
  printf 'fail closed: %s\n' "$*" >&2
  exit 78
}

cleanup() {
  if [[ -n "$OUTPUT_TMP" && -e "$OUTPUT_TMP" ]]; then
    rm -f -- "$OUTPUT_TMP"
  fi
  if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
    rm -rf -- "$TMP_DIR"
  fi
}

require_value() {
  local option="$1"
  local value="${2:-}"
  [[ -n "$value" ]] || fail "missing value for $option"
}

parse_args() {
  while (($#)); do
    case "$1" in
      --release-id)
        require_value "$1" "${2:-}"
        RELEASE_ID="$2"
        shift 2
        ;;
      --release-lock-digest)
        require_value "$1" "${2:-}"
        RELEASE_LOCK_DIGEST="$2"
        shift 2
        ;;
      --snapshot-selector)
        require_value "$1" "${2:-}"
        SNAPSHOT_SELECTOR="$2"
        shift 2
        ;;
      --context)
        require_value "$1" "${2:-}"
        KUBE_CONTEXT="$2"
        shift 2
        ;;
      --requested-at)
        require_value "$1" "${2:-}"
        REQUESTED_AT="$2"
        shift 2
        ;;
      --output)
        require_value "$1" "${2:-}"
        OUTPUT="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "unknown argument: $1"
        ;;
    esac
  done

  [[ -n "$RELEASE_ID" ]] || fail "--release-id is required"
  [[ "$RELEASE_ID" =~ ^[a-z0-9][a-z0-9._-]{2,127}$ ]] || \
    fail "--release-id must use 3-128 lowercase letters, numbers, dot, underscore, or hyphen"
  [[ "$RELEASE_LOCK_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || \
    fail "--release-lock-digest must be sha256: followed by 64 lowercase hexadecimal characters"

  if [[ -z "$REQUESTED_AT" ]]; then
    REQUESTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  fi
}

ensure_dependencies() {
  command -v "$KUBECTL_BIN" >/dev/null 2>&1 || fail "kubectl command not found: $KUBECTL_BIN"
  command -v python3 >/dev/null 2>&1 || fail "python3 is required"
}

collect_namespace() {
  local namespace="$1"
  local destination="$2"
  local -a kubectl_args=()

  if [[ -n "$KUBE_CONTEXT" ]]; then
    kubectl_args+=(--context "$KUBE_CONTEXT")
  fi
  kubectl_args+=(-n "$namespace" get "$SNAPSHOT_RESOURCE")
  if [[ -n "$SNAPSHOT_SELECTOR" ]]; then
    kubectl_args+=(-l "$SNAPSHOT_SELECTOR")
  fi
  kubectl_args+=(-o json)

  if ! "$KUBECTL_BIN" "${kubectl_args[@]}" > "$destination"; then
    fail "could not list $SNAPSHOT_RESOURCE in namespace $namespace"
  fi
}

render_request() {
  python3 - \
    "$RELEASE_ID" \
    "$RELEASE_LOCK_DIGEST" \
    "$REQUESTED_AT" \
    "$SNAPSHOT_SELECTOR" \
    "$TMP_DIR/$STAGING_NAMESPACE.json" \
    "$TMP_DIR/$VAULT_NAMESPACE.json" <<'PY'
import hashlib
import json
import sys
from datetime import datetime

(
    release_id,
    release_lock_digest,
    requested_at,
    selector,
    staging_path,
    vault_path,
) = sys.argv[1:]

STAGING_NAMESPACE = "kerosene-staging"
VAULT_NAMESPACE = "kerosene-staging-vault"
EXPECTED_TARGETS = {
    (STAGING_NAMESPACE, "data-staging-postgres-0"),
    (STAGING_NAMESPACE, "data-staging-redis-0"),
    (STAGING_NAMESPACE, "data-staging-bitcoin-0"),
    (STAGING_NAMESPACE, "data-staging-lnd-0"),
    (STAGING_NAMESPACE, "data-staging-tor-0"),
    (STAGING_NAMESPACE, "vault-1-data"),
    (STAGING_NAMESPACE, "vault-2-data"),
    (STAGING_NAMESPACE, "vault-3-data"),
    (VAULT_NAMESPACE, "vault-data"),
    (VAULT_NAMESPACE, "data-vault-tor-0"),
}


def fail(message):
    print(f"fail closed: {message}", file=sys.stderr)
    raise SystemExit(78)


def required_string(value, field):
    if not isinstance(value, str) or not value:
        fail(f"{field} must be a non-empty string")
    return value


def required_mapping(value, field):
    if not isinstance(value, dict):
        fail(f"{field} must be an object")
    return value


try:
    datetime.strptime(requested_at, "%Y-%m-%dT%H:%M:%SZ")
except ValueError:
    fail("--requested-at must be a valid RFC3339 UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)")


def load_snapshot_list(path, namespace):
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"could not parse VolumeSnapshot list for namespace {namespace}: {exc}")

    document = required_mapping(document, f"VolumeSnapshot list for {namespace}")
    if document.get("apiVersion") != "snapshot.storage.k8s.io/v1":
        fail(f"VolumeSnapshot list for {namespace} must use snapshot.storage.k8s.io/v1")
    if document.get("kind") != "VolumeSnapshotList":
        fail(f"VolumeSnapshot list for {namespace} must have kind VolumeSnapshotList")
    items = document.get("items")
    if not isinstance(items, list):
        fail(f"VolumeSnapshot list for {namespace} must contain an items array")
    return items


snapshot_records_by_target = {target: [] for target in EXPECTED_TARGETS}

for namespace, path in ((STAGING_NAMESPACE, staging_path), (VAULT_NAMESPACE, vault_path)):
    for item in load_snapshot_list(path, namespace):
        item = required_mapping(item, f"VolumeSnapshot item in {namespace}")
        if item.get("apiVersion") != "snapshot.storage.k8s.io/v1" or item.get("kind") != "VolumeSnapshot":
            fail(f"VolumeSnapshot item in {namespace} has an unexpected apiVersion or kind")

        metadata = required_mapping(item.get("metadata"), f"VolumeSnapshot metadata in {namespace}")
        item_namespace = required_string(metadata.get("namespace"), "VolumeSnapshot metadata.namespace")
        if item_namespace != namespace:
            fail(f"VolumeSnapshot namespace mismatch: expected {namespace}, got {item_namespace}")
        name = required_string(metadata.get("name"), "VolumeSnapshot metadata.name")
        uid = required_string(metadata.get("uid"), f"VolumeSnapshot {namespace}/{name} metadata.uid")
        creation_timestamp = required_string(
            metadata.get("creationTimestamp"),
            f"VolumeSnapshot {namespace}/{name} metadata.creationTimestamp",
        )
        if metadata.get("deletionTimestamp") is not None:
            fail(f"VolumeSnapshot {namespace}/{name} is being deleted")

        spec = required_mapping(item.get("spec"), f"VolumeSnapshot {namespace}/{name} spec")
        source = required_mapping(spec.get("source"), f"VolumeSnapshot {namespace}/{name} spec.source")
        if source.get("volumeSnapshotContentName") is not None:
            fail(
                f"VolumeSnapshot {namespace}/{name} must be sourced from a PersistentVolumeClaim, "
                "not a VolumeSnapshotContent"
            )
        pvc_name = required_string(
            source.get("persistentVolumeClaimName"),
            f"VolumeSnapshot {namespace}/{name} spec.source.persistentVolumeClaimName",
        )
        target = (namespace, pvc_name)
        if target not in EXPECTED_TARGETS:
            fail(f"VolumeSnapshot {namespace}/{name} has unexpected source PVC {pvc_name}")

        status = required_mapping(item.get("status"), f"VolumeSnapshot {namespace}/{name} status")
        if status.get("readyToUse") is not True:
            fail(f"VolumeSnapshot {namespace}/{name} is not readyToUse=true")
        if status.get("error") is not None:
            fail(f"VolumeSnapshot {namespace}/{name} reports a snapshot error")
        content_name = required_string(
            status.get("boundVolumeSnapshotContentName"),
            f"VolumeSnapshot {namespace}/{name} status.boundVolumeSnapshotContentName",
        )

        snapshot_class_name = spec.get("volumeSnapshotClassName")
        if snapshot_class_name is not None and not isinstance(snapshot_class_name, str):
            fail(f"VolumeSnapshot {namespace}/{name} spec.volumeSnapshotClassName must be a string or null")
        restore_size = status.get("restoreSize")
        if restore_size is not None and not isinstance(restore_size, str):
            fail(f"VolumeSnapshot {namespace}/{name} status.restoreSize must be a string or null")

        snapshot_records_by_target[target].append(
            {
                "boundVolumeSnapshotContentName": content_name,
                "creationTimestamp": creation_timestamp,
                "namespace": namespace,
                "persistentVolumeClaim": pvc_name,
                "readyToUse": True,
                "restoreSize": restore_size,
                "volumeSnapshotClassName": snapshot_class_name,
                "volumeSnapshotName": name,
                "volumeSnapshotUid": uid,
            }
        )

snapshot_records = []
for namespace, pvc_name in sorted(EXPECTED_TARGETS):
    records = snapshot_records_by_target[(namespace, pvc_name)]
    if not records:
        fail(f"missing VolumeSnapshot for required PVC {namespace}/{pvc_name}")
    if len(records) != 1:
        fail(f"ambiguous VolumeSnapshots for required PVC {namespace}/{pvc_name}: found {len(records)}")
    snapshot_records.extend(records)

snapshot_records.sort(
    key=lambda record: (
        record["namespace"],
        record["persistentVolumeClaim"],
        record["volumeSnapshotName"],
        record["volumeSnapshotUid"],
    )
)
canonical_snapshot_set = json.dumps(
    snapshot_records,
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
).encode("utf-8")
snapshot_set_digest = "sha256:" + hashlib.sha256(canonical_snapshot_set).hexdigest()

request = {
    "attestation": {
        "externalSignerRequired": True,
        "status": "unsigned-request",
    },
    "environment": "staging-cell",
    "kind": "VolumeSnapshotAttestationRequest",
    "release": {
        "id": release_id,
        "lockDigest": release_lock_digest,
    },
    "requestedAt": requested_at,
    "schema": "kerosene.snapshot-attestation-request/v1",
    "selection": {
        "labelSelector": selector if selector else None,
        "namespaces": [STAGING_NAMESPACE, VAULT_NAMESPACE],
    },
    "snapshotSetDigest": snapshot_set_digest,
    "snapshots": snapshot_records,
}
json.dump(request, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
sys.stdout.write("\n")
PY
}

write_request() {
  if [[ "$OUTPUT" == "-" ]]; then
    render_request
    return
  fi

  local output_parent
  output_parent="$(dirname -- "$OUTPUT")"
  [[ -d "$output_parent" ]] || fail "output directory does not exist: $output_parent"
  [[ ! -L "$output_parent" ]] || fail "refusing symlinked output directory: $output_parent"
  [[ ! -L "$OUTPUT" ]] || fail "refusing to replace symlinked output path: $OUTPUT"
  OUTPUT_TMP="$(mktemp "$output_parent/.kerosene-snapshot-attestation-request.XXXXXX")"
  if render_request > "$OUTPUT_TMP"; then
    :
  else
    local render_status=$?
    exit "$render_status"
  fi
  chmod 600 "$OUTPUT_TMP"
  mv -f -- "$OUTPUT_TMP" "$OUTPUT"
  OUTPUT_TMP=""
  printf 'unsigned attestation request written to %s\n' "$OUTPUT" >&2
}

main() {
  parse_args "$@"
  ensure_dependencies
  umask 077
  TMP_DIR="$(mktemp -d)"
  trap cleanup EXIT

  collect_namespace "$STAGING_NAMESPACE" "$TMP_DIR/$STAGING_NAMESPACE.json"
  collect_namespace "$VAULT_NAMESPACE" "$TMP_DIR/$VAULT_NAMESPACE.json"
  write_request
}

main "$@"
