#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STACK="$ROOT/infra/kerosene-stack"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
  echo "kerosene-stack test failed: $*" >&2
  exit 1
}

python3 -m json.tool "$ROOT/infra/stack/release-lock.schema.json" >/dev/null
python3 -m json.tool "$ROOT/infra/stack/release-lock-v2.schema.json" >/dev/null
python3 -m json.tool "$ROOT/infra/stack/examples/release-lock.example.json" >/dev/null
python3 -m json.tool "$ROOT/infra/stack/examples/release-lock-v2.example.json" >/dev/null
"$STACK" verify-release --release "$ROOT/infra/stack/examples/release-lock.example.json" >/dev/null
"$STACK" verify-release --release "$ROOT/infra/stack/examples/release-lock-v2.example.json" >/dev/null

make_release() {
  local destination="$1"
  python3 - "$destination" <<'PY'
import datetime as dt
import json
import sys

digest = lambda value: "sha256:" + value * 64
commit = lambda value: value * 40
release_id = "bank-mainnet-2026.09.28.1"
repositories = {}
for name, char in (
    ("admin", "1"), ("clients", "2"), ("contracts", "3"), ("core", "4"),
    ("deploy", "5"), ("kfe", "6"), ("node", "7"), ("rails", "8"),
    ("shared", "9"), ("vault", "a"),
):
    repositories[name] = {"commit": commit(char), "bundleDigest": digest(char)}

services = {}
for name, repository, rollout, char in (
    ("admin", "admin", "operator", "1"),
    ("core", "core", "application", "2"),
    ("kfe", "kfe", "application", "3"),
    ("node", "node", "quorum", "4"),
    ("vault", "vault", "quorum", "5"),
    ("web-page", "clients", "application", "6"),
    ("postgres", "deploy", "stateful", "7"),
    ("redis", "deploy", "stateful", "8"),
    ("bitcoin", "deploy", "network", "9"),
    ("lnd", "deploy", "network", "a"),
    ("tor", "deploy", "network", "b"),
):
    services[name] = {
        "image": f"registry.example.invalid/kerosene/{name}@{digest(char)}",
        "configDigest": digest(char),
        "repository": repository,
        "rollout": rollout,
    }

release = {
    "schema": "kerosene.release-lock/v1",
    "schemaVersion": 1,
    "releaseId": release_id,
    "sequence": 42,
    "network": {"id": "bank-mainnet", "plane": "bank"},
    "authorization": {
        "tuf": {
            "targetPath": f"releases/{release_id}.json",
            "metadataVersion": 42,
            "targetDigest": digest("c"),
            "expiresAt": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat().replace("+00:00", "Z"),
        },
        "bft": {
            "networkId": "bank-release-governance",
            "height": 8120,
            "commitDigest": digest("d"),
            "threshold": 3,
            "members": 4,
        },
        "vaultCompatibility": {
            "threshold": 2,
            "members": 3,
            "attestationDigest": digest("e"),
        },
    },
    "source": {
        "bundle": {
            "uri": f"oci://registry.example.invalid/kerosene/source-bundle@{digest('f')}",
            "digest": digest("f"),
        },
        "repositories": repositories,
    },
    "services": services,
    "policy": {
        "allowSourceBuild": False,
        "vaultSignerActivation": False,
        "minimumBankObservers": 3,
    },
    "migration": {
        "id": "cell-schema-42",
        "classification": "reversible",
        "snapshotRequired": True,
        "recoveryEvidenceDigest": digest("0"),
    },
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(release, handle)
PY
}

VALID_RELEASE="$TMP_DIR/release-lock.json"
PLAN="$TMP_DIR/update-plan.json"
make_release "$VALID_RELEASE"

"$STACK" verify-release --release "$VALID_RELEASE" --json > "$TMP_DIR/verify.json"
python3 - "$TMP_DIR/verify.json" <<'PY'
import json
import sys

result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["structuralValidation"] == "passed"
assert result["cryptographicAuthorization"] == "not-checked"
assert "admin" in result["requiredComponents"]
assert "node" in result["requiredComponents"]
PY

if "$STACK" verify-release --release "$VALID_RELEASE" --tuf-state-dir "$TMP_DIR/trust-only" >/dev/null 2>&1; then
  fail "verify-release accepted TUF state without TUF authorization evidence"
fi

"$STACK" update --release "$VALID_RELEASE" --output "$PLAN" --json > "$TMP_DIR/plan.json"
python3 - "$PLAN" "$TMP_DIR/plan.json" <<'PY'
import json
import sys

on_disk = json.load(open(sys.argv[1], encoding="utf-8"))
printed = json.load(open(sys.argv[2], encoding="utf-8"))
assert on_disk == printed
assert on_disk["mode"] == "plan-only"
assert on_disk["applyEnabled"] is False
phases = {phase["id"]: phase for phase in on_disk["phases"]}
assert "admin" in phases["operator-release"]["components"]
assert "web-page" in phases["rollout-applications"]["components"]
assert "node" in phases["rollout-node"]["components"]
assert "vault" in phases["rollout-vault"]["components"]
PY

MISSING_NODE="$TMP_DIR/missing-node.json"
python3 - "$VALID_RELEASE" "$MISSING_NODE" <<'PY'
import json
import sys

release = json.load(open(sys.argv[1], encoding="utf-8"))
del release["services"]["node"]
json.dump(release, open(sys.argv[2], "w", encoding="utf-8"))
PY
if "$STACK" verify-release --release "$MISSING_NODE" >/dev/null 2>&1; then
  fail "release without Node was accepted"
fi

MUTABLE_IMAGE="$TMP_DIR/mutable-image.json"
python3 - "$VALID_RELEASE" "$MUTABLE_IMAGE" <<'PY'
import json
import sys

release = json.load(open(sys.argv[1], encoding="utf-8"))
release["services"]["admin"]["image"] = "registry.example.invalid/kerosene/admin:latest"
json.dump(release, open(sys.argv[2], "w", encoding="utf-8"))
PY
if "$STACK" verify-release --release "$MUTABLE_IMAGE" >/dev/null 2>&1; then
  fail "release with mutable Admin image was accepted"
fi

TAGGED_DIGEST_IMAGE="$TMP_DIR/tagged-digest-image.json"
python3 - "$VALID_RELEASE" "$TAGGED_DIGEST_IMAGE" <<'PY'
import json
import sys

release = json.load(open(sys.argv[1], encoding="utf-8"))
digest = release["services"]["admin"]["image"].split("@", 1)[1]
release["services"]["admin"]["image"] = f"registry.example.invalid/kerosene/admin:latest@{digest}"
json.dump(release, open(sys.argv[2], "w", encoding="utf-8"))
PY
if "$STACK" verify-release --release "$TAGGED_DIGEST_IMAGE" >/dev/null 2>&1; then
  fail "release with a tagged Admin digest was accepted"
fi

if "$STACK" update --release "$VALID_RELEASE" --apply >/dev/null 2>&1; then
  fail "--apply was unexpectedly enabled"
else
  status=$?
  [[ "$status" -eq 78 ]] || fail "--apply returned $status instead of 78"
fi

EPHEMERAL_STATE_DIR="$TMP_DIR/ephemeral-update-state"
set +e
output="$("$STACK" update --release "$VALID_RELEASE" --apply \
  --environment staging-cell --confirm-release bank-mainnet-2026.09.28.1 \
  --state-dir "$EPHEMERAL_STATE_DIR" --tuf-state-dir "$EPHEMERAL_STATE_DIR" 2>&1)"
status=$?
set -e
[[ "$status" -eq 78 ]] || fail "unverified update returned $status instead of 78"
[[ ! -e "$EPHEMERAL_STATE_DIR/update.lock" ]] \
  || fail "failed evidence validation left an update lock behind"

LOCKED_STATE_DIR="$TMP_DIR/locked-update-state"
mkdir -p "$LOCKED_STATE_DIR"
printf '%s\n' 'operator investigation required' > "$LOCKED_STATE_DIR/update.lock"
set +e
output="$("$STACK" update --release "$VALID_RELEASE" --apply \
  --environment staging-cell --confirm-release bank-mainnet-2026.09.28.1 \
  --state-dir "$LOCKED_STATE_DIR" --tuf-state-dir "$LOCKED_STATE_DIR" 2>&1)"
status=$?
set -e
[[ "$status" -eq 78 ]] || fail "locked update returned $status instead of 78"
grep -q 'another update is active or requires manual investigation' <<<"$output" \
  || fail "locked update did not explain the concurrency gate"

echo "Kerosene Stack release-lock tests passed."
