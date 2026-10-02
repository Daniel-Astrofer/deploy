#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SUBJECT="$REPO_ROOT/infra/kubernetes/scripts/collect-staging-volumesnapshot-attestation-request.sh"
TMP_DIR="$(mktemp -d)"
FIXTURE_DIR="$TMP_DIR/fixtures"
FAKE_BIN="$TMP_DIR/bin"
RELEASE_DIGEST="sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

cleanup() {
  rm -rf -- "$TMP_DIR"
}
trap cleanup EXIT

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

assert_contains() {
  local file="$1"
  local expected="$2"
  grep -Fq -- "$expected" "$file" || fail "expected $file to contain: $expected"
}

write_snapshot_list() {
  local destination="$1"
  local namespace="$2"
  local records_json="$3"
  python3 - "$destination" "$namespace" "$records_json" <<'PY'
import json
import sys

destination, namespace, records_json = sys.argv[1:]
records = json.loads(records_json)
items = []
for record in records:
    source = record.get("source")
    if source is None:
        source = {"persistentVolumeClaimName": record["pvc"]}
    status = record.get("status")
    if status is None:
        status = {
            "boundVolumeSnapshotContentName": "snapcontent-" + record["name"],
            "readyToUse": record.get("ready", True),
            "restoreSize": record.get("restoreSize", "1Gi"),
        }
    items.append(
        {
            "apiVersion": "snapshot.storage.k8s.io/v1",
            "kind": "VolumeSnapshot",
            "metadata": {
                "creationTimestamp": record.get("created", "2026-09-30T12:00:00Z"),
                "name": record["name"],
                "namespace": namespace,
                "uid": record["uid"],
            },
            "spec": {
                "source": source,
                "volumeSnapshotClassName": record.get("snapshotClass", "csi-hostpath-snapclass"),
            },
            "status": status,
        }
    )
json.dump(
    {
        "apiVersion": "snapshot.storage.k8s.io/v1",
        "items": items,
        "kind": "VolumeSnapshotList",
    },
    open(destination, "w", encoding="utf-8"),
)
PY
}

mkdir -p "$FIXTURE_DIR" "$FAKE_BIN"

staging_records='[
  {"pvc":"data-staging-tor-0","name":"snapshot-tor","uid":"uid-tor","restoreSize":"1Gi"},
  {"pvc":"data-staging-lnd-0","name":"snapshot-lnd","uid":"uid-lnd","restoreSize":"20Gi"},
  {"pvc":"data-staging-bitcoin-0","name":"snapshot-bitcoin","uid":"uid-bitcoin","restoreSize":"100Gi"},
  {"pvc":"data-staging-redis-0","name":"snapshot-redis","uid":"uid-redis","restoreSize":"5Gi"},
  {"pvc":"data-staging-postgres-0","name":"snapshot-postgres","uid":"uid-postgres","restoreSize":"50Gi"},
  {"pvc":"vault-1-data","name":"snapshot-vault-1","uid":"uid-vault-1","restoreSize":"2Gi"},
  {"pvc":"vault-2-data","name":"snapshot-vault-2","uid":"uid-vault-2","restoreSize":"2Gi"},
  {"pvc":"vault-3-data","name":"snapshot-vault-3","uid":"uid-vault-3","restoreSize":"2Gi"}
]'
vault_records='[
  {"pvc":"vault-data","name":"snapshot-vault","uid":"uid-vault","restoreSize":"2Gi"},
  {"pvc":"data-vault-tor-0","name":"snapshot-vault-tor","uid":"uid-vault-tor","restoreSize":"1Gi"}
]'
write_snapshot_list "$FIXTURE_DIR/normal.kerosene-staging.json" kerosene-staging "$staging_records"
write_snapshot_list "$FIXTURE_DIR/normal.kerosene-staging-vault.json" kerosene-staging-vault "$vault_records"

# Real kubectl can return the generic client-side envelope. Fixtures keep
# actual CRD identities on every item; malformed envelopes/items stay denied.
python3 - "$FIXTURE_DIR" <<'PY'
import copy
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
for namespace in ("kerosene-staging", "kerosene-staging-vault"):
    original = json.loads((root / ("normal." + namespace + ".json")).read_bytes())
    generic = copy.deepcopy(original)
    generic.update(apiVersion="v1", kind="List")
    variants = {"generic": generic}
    mixed = copy.deepcopy(generic)
    mixed["kind"] = "VolumeSnapshotList"
    variants["mixed-envelope"] = mixed
    wrong = copy.deepcopy(generic)
    wrong["items"][0]["kind"] = "Secret"
    variants["generic-wrong-item"] = wrong
    paginated = copy.deepcopy(generic)
    paginated["metadata"] = {"continue": "synthetic-page-token"}
    variants["paginated"] = paginated
    variants["remaining-items"] = {**generic, "metadata": {"remainingItemCount": 1}}
    variants["too-many-items"] = {**generic, "items": generic["items"] * 1025}
    for variant, document in variants.items():
        (root / (variant + "." + namespace + ".json")).write_text(json.dumps(document))
    (root / ("duplicate-json." + namespace + ".json")).write_text(
        json.dumps(generic).replace('"items":', '"items": [], "items":', 1))
    nonfinite = copy.deepcopy(generic)
    nonfinite["items"][0]["status"]["restoreSize"] = float("nan")
    (root / ("nonfinite-json." + namespace + ".json")).write_text(json.dumps(nonfinite))
    (root / ("oversized-json." + namespace + ".json")).write_text(
        json.dumps({**generic, "syntheticPadding": "x" * (8 * 1024 * 1024)}))
PY

reversed_staging_records="$(python3 - "$staging_records" <<'PY'
import json
import sys
print(json.dumps(list(reversed(json.loads(sys.argv[1])))))
PY
)"
reversed_vault_records="$(python3 - "$vault_records" <<'PY'
import json
import sys
print(json.dumps(list(reversed(json.loads(sys.argv[1])))))
PY
)"
write_snapshot_list "$FIXTURE_DIR/reversed.kerosene-staging.json" kerosene-staging "$reversed_staging_records"
write_snapshot_list "$FIXTURE_DIR/reversed.kerosene-staging-vault.json" kerosene-staging-vault "$reversed_vault_records"

not_ready_records="$(python3 - "$staging_records" <<'PY'
import json
import sys
records = json.loads(sys.argv[1])
records[1]["ready"] = False
print(json.dumps(records))
PY
)"
write_snapshot_list "$FIXTURE_DIR/not-ready.kerosene-staging.json" kerosene-staging "$not_ready_records"
write_snapshot_list "$FIXTURE_DIR/not-ready.kerosene-staging-vault.json" kerosene-staging-vault "$vault_records"

content_source_records="$(python3 - "$staging_records" <<'PY'
import json
import sys
records = json.loads(sys.argv[1])
records[0]["source"] = {"volumeSnapshotContentName": "pre-provisioned-content"}
print(json.dumps(records))
PY
)"
write_snapshot_list "$FIXTURE_DIR/content-source.kerosene-staging.json" kerosene-staging "$content_source_records"
write_snapshot_list "$FIXTURE_DIR/content-source.kerosene-staging-vault.json" kerosene-staging-vault "$vault_records"

cat > "$FAKE_BIN/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

: "${FIXTURE_DIR:?FIXTURE_DIR is required}"
: "${FIXTURE_VARIANT:?FIXTURE_VARIANT is required}"
: "${KUBECTL_CALL_LOG:?KUBECTL_CALL_LOG is required}"

printf '%s\n' "$*" >> "$KUBECTL_CALL_LOG"

namespace=""
resource=""
while (($#)); do
  case "$1" in
    -n|--namespace)
      namespace="${2:?namespace value missing}"
      shift 2
      ;;
    get)
      resource="${2:?resource value missing}"
      shift 2
      ;;
    --context|-l|-o)
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

[[ "$resource" == "volumesnapshots.snapshot.storage.k8s.io" ]] || {
  echo "unexpected resource: $resource" >&2
  exit 42
}
[[ "$namespace" == "kerosene-staging" || "$namespace" == "kerosene-staging-vault" ]] || {
  echo "unexpected namespace: $namespace" >&2
  exit 43
}
cat "$FIXTURE_DIR/$FIXTURE_VARIANT.$namespace.json"
EOF
chmod +x "$FAKE_BIN/kubectl"

run_subject() {
  local variant="$1"
  local output="$2"
  local stderr_file="$3"
  : > "$TMP_DIR/kubectl-$variant.log"
  KUBECTL="$FAKE_BIN/kubectl" \
  FIXTURE_DIR="$FIXTURE_DIR" \
  FIXTURE_VARIANT="$variant" \
  KUBECTL_CALL_LOG="$TMP_DIR/kubectl-$variant.log" \
  "$SUBJECT" \
    --release-id staging-release-42 \
    --release-lock-digest "$RELEASE_DIGEST" \
    --snapshot-selector 'kerosene.io/attestation-set=staging-release-42' \
    --requested-at 2026-09-30T12:34:56Z \
    > "$output" 2> "$stderr_file"
}

run_subject normal "$TMP_DIR/normal.json" "$TMP_DIR/normal.stderr"
assert_contains "$TMP_DIR/kubectl-normal.log" "get volumesnapshots.snapshot.storage.k8s.io"
assert_contains "$TMP_DIR/kubectl-normal.log" "-l kerosene.io/attestation-set=staging-release-42"

python3 - "$TMP_DIR/normal.json" "$RELEASE_DIGEST" <<'PY'
import hashlib
import json
import sys

payload_path, release_digest = sys.argv[1:]
payload = json.load(open(payload_path, encoding="utf-8"))
assert payload["schema"] == "kerosene.snapshot-attestation-request/v1"
assert payload["kind"] == "VolumeSnapshotAttestationRequest"
assert payload["environment"] == "staging-cell"
assert payload["release"] == {"id": "staging-release-42", "lockDigest": release_digest}
assert payload["attestation"] == {"externalSignerRequired": True, "status": "unsigned-request"}
assert "signature" not in payload
assert "receipt" not in payload
assert len(payload["snapshots"]) == 10
assert [
    (item["namespace"], item["persistentVolumeClaim"])
    for item in payload["snapshots"]
] == sorted(
    [
        ("kerosene-staging", "data-staging-bitcoin-0"),
        ("kerosene-staging", "data-staging-lnd-0"),
        ("kerosene-staging", "data-staging-postgres-0"),
        ("kerosene-staging", "data-staging-redis-0"),
        ("kerosene-staging", "data-staging-tor-0"),
        ("kerosene-staging", "vault-1-data"),
        ("kerosene-staging", "vault-2-data"),
        ("kerosene-staging", "vault-3-data"),
        ("kerosene-staging-vault", "data-vault-tor-0"),
        ("kerosene-staging-vault", "vault-data"),
    ]
)
canonical = json.dumps(
    payload["snapshots"], ensure_ascii=False, separators=(",", ":"), sort_keys=True
).encode("utf-8")
assert payload["snapshotSetDigest"] == "sha256:" + hashlib.sha256(canonical).hexdigest()
PY

run_subject reversed "$TMP_DIR/reversed.json" "$TMP_DIR/reversed.stderr"
cmp -s "$TMP_DIR/normal.json" "$TMP_DIR/reversed.json" || fail "snapshot request changed when kubectl item order changed"

run_subject generic "$TMP_DIR/generic.json" "$TMP_DIR/generic.stderr"
cmp -s "$TMP_DIR/normal.json" "$TMP_DIR/generic.json" || fail "generic kubectl envelope changed snapshot evidence"
for variant in mixed-envelope generic-wrong-item paginated remaining-items too-many-items duplicate-json nonfinite-json oversized-json; do
  if run_subject "$variant" "$TMP_DIR/$variant.json" "$TMP_DIR/$variant.stderr"; then
    fail "collector accepted malformed/incomplete snapshot list: $variant"
  fi
  [[ ! -s "$TMP_DIR/$variant.json" ]] || fail "collector emitted payload for rejected list: $variant"
done

: > "$TMP_DIR/kubectl-output.log"
KUBECTL="$FAKE_BIN/kubectl" \
FIXTURE_DIR="$FIXTURE_DIR" \
FIXTURE_VARIANT=normal \
KUBECTL_CALL_LOG="$TMP_DIR/kubectl-output.log" \
"$SUBJECT" \
  --release-id staging-release-42 \
  --release-lock-digest "$RELEASE_DIGEST" \
  --snapshot-selector 'kerosene.io/attestation-set=staging-release-42' \
  --requested-at 2026-09-30T12:34:56Z \
  --output "$TMP_DIR/request.json" \
  > "$TMP_DIR/output.stdout" 2> "$TMP_DIR/output.stderr"
[[ ! -s "$TMP_DIR/output.stdout" ]] || fail "--output unexpectedly wrote a payload to stdout"
cmp -s "$TMP_DIR/normal.json" "$TMP_DIR/request.json" || fail "--output changed the generated request"
assert_contains "$TMP_DIR/output.stderr" "unsigned attestation request written to"

if run_subject not-ready "$TMP_DIR/not-ready.json" "$TMP_DIR/not-ready.stderr"; then
  fail "collector accepted a VolumeSnapshot without readyToUse=true"
fi
[[ ! -s "$TMP_DIR/not-ready.json" ]] || fail "collector emitted a payload for a not-ready snapshot"
assert_contains "$TMP_DIR/not-ready.stderr" "not readyToUse=true"

: > "$TMP_DIR/kubectl-not-ready-output.log"
if KUBECTL="$FAKE_BIN/kubectl" \
  FIXTURE_DIR="$FIXTURE_DIR" \
  FIXTURE_VARIANT=not-ready \
  KUBECTL_CALL_LOG="$TMP_DIR/kubectl-not-ready-output.log" \
  "$SUBJECT" \
    --release-id staging-release-42 \
    --release-lock-digest "$RELEASE_DIGEST" \
    --requested-at 2026-09-30T12:34:56Z \
    --output "$TMP_DIR/not-ready-request.json" \
    > "$TMP_DIR/not-ready-output.stdout" 2> "$TMP_DIR/not-ready-output.stderr"; then
  fail "collector accepted a not-ready snapshot when --output was used"
fi
[[ ! -e "$TMP_DIR/not-ready-request.json" ]] || fail "collector wrote a payload file for a not-ready snapshot"
assert_contains "$TMP_DIR/not-ready-output.stderr" "not readyToUse=true"

if run_subject content-source "$TMP_DIR/content-source.json" "$TMP_DIR/content-source.stderr"; then
  fail "collector accepted a snapshot sourced from VolumeSnapshotContent"
fi
[[ ! -s "$TMP_DIR/content-source.json" ]] || fail "collector emitted a payload for a non-PVC source"
assert_contains "$TMP_DIR/content-source.stderr" "must be sourced from a PersistentVolumeClaim"

if KUBECTL="$FAKE_BIN/kubectl" \
  FIXTURE_DIR="$FIXTURE_DIR" \
  FIXTURE_VARIANT=normal \
  KUBECTL_CALL_LOG="$TMP_DIR/kubectl-invalid-release.log" \
  "$SUBJECT" \
    --release-id Staging-Release-42 \
    --release-lock-digest "$RELEASE_DIGEST" \
    > "$TMP_DIR/invalid-release.stdout" 2> "$TMP_DIR/invalid-release.stderr"; then
  fail "collector accepted a release ID the stack controller would reject"
fi
assert_contains "$TMP_DIR/invalid-release.stderr" "must use 3-128 lowercase"

echo "[PASS] staging VolumeSnapshot attestation request collection"
