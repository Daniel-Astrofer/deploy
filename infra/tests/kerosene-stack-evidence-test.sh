#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STACK="$ROOT/infra/kerosene-stack"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
RELEASE="$ROOT/infra/stack/examples/release-lock.example.json"

python3 - "$STACK" "$RELEASE" "$TMP_DIR" <<'PY'
import base64
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import stat
import subprocess
import sys

stack, release_path, temp_root = sys.argv[1:]
root = pathlib.Path(temp_root)
keys = root / "keys"
keys.mkdir()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

def digest(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()

release = json.load(open(release_path, encoding="utf-8"))
release["schema"] = "kerosene.release-lock/v2"
release["schemaVersion"] = 2
release["authorization"]["tuf"] = {
    "targetPath": f"releases/{release['releaseId']}.json",
}
now = dt.datetime.now(dt.timezone.utc)
vault_keys = root / "vault-keys"
vault_keys.mkdir()
for member in range(1, 4):
    subprocess.run(["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(vault_keys / f"{member}.key")], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["openssl", "pkey", "-in", str(vault_keys / f"{member}.key"), "-pubout", "-outform", "DER", "-out", str(vault_keys / f"{member}.pub")], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
vault_roster_path = root / "vault-roster.json"
vault_roster_path.write_bytes(canonical({"schema": "kerosene.vault-compatibility-roster/v1",
    "networkId": release["network"]["id"], "members": {
        f"vault-{member}": base64.b64encode((vault_keys / f"{member}.pub").read_bytes()).decode()
        for member in range(1, 4)}}))
vault_attestation = {"schema": "kerosene.vault-compatibility-attestation/v1",
    "releaseId": release["releaseId"], "networkId": release["network"]["id"], "sequence": release["sequence"],
    "sourceBundleDigest": release["source"]["bundle"]["digest"],
    "repositoryCommits": {name: value["commit"] for name, value in release["source"]["repositories"].items()},
    "services": {name: {"image": value["image"], "configDigest": value["configDigest"]} for name, value in release["services"].items()},
    "migrationRecoveryEvidenceDigest": release["migration"]["recoveryEvidenceDigest"],
    "rebuildEvidenceDigest": "sha256:" + "d" * 64, "sbomSetDigest": "sha256:" + "e" * 64,
    "provenanceSetDigest": "sha256:" + "f" * 64,
    "verifiedAt": (now - dt.timedelta(minutes=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "expiresAt": (now + dt.timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "signatures": []}
vault_payload = canonical({key: value for key, value in vault_attestation.items() if key != "signatures"})
for member in range(1, 3):
    payload_path, signature_path = root / f"vault-{member}.payload", root / f"vault-{member}.sig"
    payload_path.write_bytes(vault_payload)
    subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(vault_keys / f"{member}.key"), "-in", str(payload_path), "-out", str(signature_path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    vault_attestation["signatures"].append({"memberId": f"vault-{member}",
        "publicKeyDerBase64": base64.b64encode((vault_keys / f"{member}.pub").read_bytes()).decode(),
        "signatureBase64": base64.b64encode(signature_path.read_bytes()).decode()})
vault_attestation_path = root / "vault-attestation.json"
vault_attestation_path.write_bytes(canonical(vault_attestation))
release["authorization"]["vaultCompatibility"]["attestationDigest"] = digest(canonical(vault_attestation))
vault_args = ["--vault-roster", str(vault_roster_path), "--vault-compatibility-attestation", str(vault_attestation_path)]
release_path = root / "release-lock-v2.json"
release_path.write_bytes(canonical(release))
release_bytes = release_path.read_bytes()
release_digest = digest(canonical(release))

def generate_ed25519(name):
    private = root / f"tuf-{name}.key"
    public = root / f"tuf-{name}.pub"
    subprocess.run(["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(private)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-outform", "DER", "-out", str(public)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raw = public.read_bytes()
    assert raw.startswith(bytes.fromhex("302a300506032b6570032100")) and len(raw) == 44
    key = {"keytype": "ed25519", "scheme": "ed25519", "keyval": {"public": raw[-32:].hex()}}
    return private, public, hashlib.sha256(canonical(key)).hexdigest(), key

tuf_keys = {role: generate_ed25519(role) for role in ("root", "timestamp", "snapshot", "targets")}
tuf_root = root / "tuf-root-key.b64"
tuf_root.write_text(base64.b64encode(tuf_keys["root"][1].read_bytes()).decode() + "\n", encoding="utf-8")

def tuf_signature(signed, private, keyid, label):
    payload_path = root / f"{label}-{keyid}.payload"
    signature_path = root / f"{label}-{keyid}.sig"
    payload_path.write_bytes(canonical(signed))
    subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(private), "-in", str(payload_path), "-out", str(signature_path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"keyid": keyid, "sig": signature_path.read_bytes().hex()}

def sign_tuf(signed, private, keyid, label):
    return {"signed": signed, "signatures": [tuf_signature(signed, private, keyid, label)]}

def write_tuf(path, document):
    raw = canonical(document)
    path.write_bytes(raw)
    return raw

tuf_dir = root / "tuf-metadata"
tuf_dir.mkdir()
expiry = (now + dt.timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
root_keys = {keyid: key for _role, (_private, _public, keyid, key) in tuf_keys.items()}
roles = {
    role: {"keyids": [tuf_keys[role][2]], "threshold": 1}
    for role in ("root", "timestamp", "snapshot", "targets")
}
root_metadata = sign_tuf(
    {"_type": "root", "spec_version": "1.0.0", "consistent_snapshot": True, "version": 1, "expires": expiry, "keys": root_keys, "roles": roles},
    tuf_keys["root"][0],
    tuf_keys["root"][2],
    "root-v1",
)
write_tuf(tuf_dir / "1.root.json", root_metadata)
tuf_trusted_root = tuf_dir / "1.root.json"
targets_metadata = sign_tuf(
    {
        "_type": "targets",
        "spec_version": "1.0.0",
        "version": release["sequence"],
        "expires": expiry,
        "targets": {
            release["authorization"]["tuf"]["targetPath"]: {
                "length": len(release_bytes),
                "hashes": {"sha256": hashlib.sha256(release_bytes).hexdigest()},
                "custom": {"keroseneReleaseLockCanonicalDigest": release_digest},
            }
        },
    },
    tuf_keys["targets"][0],
    tuf_keys["targets"][2],
    "targets-v42",
)
targets_raw = write_tuf(tuf_dir / "targets.json", targets_metadata)
snapshot_metadata = sign_tuf(
    {
        "_type": "snapshot",
        "spec_version": "1.0.0",
        "version": 1,
        "expires": expiry,
        "meta": {"targets.json": {"version": release["sequence"], "length": len(targets_raw), "hashes": {"sha256": hashlib.sha256(targets_raw).hexdigest()}}},
    },
    tuf_keys["snapshot"][0],
    tuf_keys["snapshot"][2],
    "snapshot-v1",
)
snapshot_metadata_raw = write_tuf(tuf_dir / "snapshot.json", snapshot_metadata)
timestamp_metadata = sign_tuf(
    {
        "_type": "timestamp",
        "spec_version": "1.0.0",
        "version": 1,
        "expires": expiry,
        "meta": {"snapshot.json": {"version": 1, "length": len(snapshot_metadata_raw), "hashes": {"sha256": hashlib.sha256(snapshot_metadata_raw).hexdigest()}}},
    },
    tuf_keys["timestamp"][0],
    tuf_keys["timestamp"][2],
    "timestamp-v1",
)
write_tuf(tuf_dir / "timestamp.json", timestamp_metadata)
for member in range(1, 5):
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(keys / f"{member}.key")],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "openssl", "pkey",
            "-in", str(keys / f"{member}.key"),
            "-pubout",
            "-outform", "DER",
            "-out", str(keys / f"{member}.pub"),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

roster = {
    "schema": "kerosene.release-roster/v1",
    "networkId": "bank-release-governance",
    "members": {
        f"validator-{member}": base64.b64encode((keys / f"{member}.pub").read_bytes()).decode()
        for member in range(1, 5)
    },
}
roster_path = root / "roster.json"
json.dump(roster, open(roster_path, "w", encoding="utf-8"))

def signed_document(document, path):
    document["signatures"] = []
    for member in range(1, 4):
        payload = dict(document)
        payload.pop("signatures")
        payload_path = root / f"{path.stem}-{member}.payload"
        signature_path = root / f"{path.stem}-{member}.sig"
        payload_path.write_bytes(canonical(payload))
        subprocess.run(
            [
                "openssl", "pkeyutl", "-sign", "-rawin",
                "-inkey", str(keys / f"{member}.key"),
                "-in", str(payload_path),
                "-out", str(signature_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        document["signatures"].append({
            "memberId": f"validator-{member}",
            "publicKeyDerBase64": base64.b64encode((keys / f"{member}.pub").read_bytes()).decode(),
            "signatureBase64": base64.b64encode(signature_path.read_bytes()).decode(),
        })
    json.dump(document, open(path, "w", encoding="utf-8"))

receipt_path = root / "receipt.json"
signed_document(
    {
        "schema": "kerosene.release-receipt/v1",
        "releaseLockCanonicalDigest": release_digest,
        "networkId": "bank-release-governance",
        "height": 8120,
        "threshold": 3,
        "members": 4,
    },
    receipt_path,
)

report_path = root / "observer-report.json"
bank_report_issued_at = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
bank_report_expires_at = (now + dt.timedelta(minutes=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
signed_document(
    {
        "schema": "kerosene.bank-observer-report/v2",
        "releaseId": release["releaseId"],
        "networkId": release["network"]["id"],
        "targetSequence": release["sequence"],
        "releaseLockCanonicalDigest": release_digest,
        "issuedAt": bank_report_issued_at,
        "expiresAt": bank_report_expires_at,
        "observations": [
            {
                "observerId": f"validator-{member}",
                "status": "compatible",
                "observedSequence": release["sequence"],
                "releaseDigest": release_digest,
                "observedAt": bank_report_issued_at,
            }
            for member in range(1, 4)
        ],
    },
    report_path,
)

result = subprocess.run(
    [
        stack, "verify-release", "--release", release_path,
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--bft-receipt", str(receipt_path),
        "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path),
        "--json",
    ],
    check=False,
    capture_output=True,
    text=True,
)
if result.returncode != 0:
    print(result.stdout)
    print(result.stderr, file=sys.stderr)
    raise SystemExit(result.returncode)
verification = json.loads(result.stdout)
assert verification["cryptographicAuthorization"] == "verified"
assert verification["authorization"]["tuf"]["signatureVerified"] is True
assert verification["authorization"]["tuf"]["mode"] == "full"
assert verification["authorization"]["bft"]["signaturesVerified"] == 3
assert verification["authorization"]["bankObservers"]["compatibleObservers"] == 3

# Bank quorum observations are deliberately short lived. A signature over the
# right release is not sufficient after its expiry, because the Cell must show
# operators a current quorum decision instead of replaying an old report.
expired_report_path = root / "observer-report-expired.json"
expired_report = json.load(open(report_path, encoding="utf-8"))
expired_report.pop("signatures")
expired_report["issuedAt"] = (now - dt.timedelta(hours=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
expired_report["expiresAt"] = (now - dt.timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
for observation in expired_report["observations"]:
    observation["observedAt"] = expired_report["issuedAt"]
signed_document(expired_report, expired_report_path)
expired_report_result = subprocess.run(
    [
        stack, "verify-release", "--release", release_path,
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(expired_report_path),
    ],
    check=False,
    capture_output=True,
    text=True,
)
assert expired_report_result.returncode == 65
assert "$.observerReport.expiresAt: is expired" in expired_report_result.stderr

# Evidence paths are opened with O_NOFOLLOW so a caller cannot substitute a
# symlink after preliminary validation and redirect the trust decision.
linked_receipt_path = root / "receipt-link.json"
linked_receipt_path.symlink_to(receipt_path)
linked_receipt_result = subprocess.run(
    [
        stack, "verify-release", "--release", release_path,
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--bft-receipt", str(linked_receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path),
    ],
    check=False,
    capture_output=True,
    text=True,
)
assert linked_receipt_result.returncode == 65
assert "BFT release receipt: must be a readable regular file" in linked_receipt_result.stderr

# The TUF target must authenticate the exact bytes passed as --release, not
# merely a self-declared digest inside JSON that happens to look valid.
tampered_release = dict(release)
tampered_release["source"] = dict(release["source"])
tampered_release["source"]["repositories"] = dict(release["source"]["repositories"])
tampered_release["source"]["repositories"]["core"] = dict(release["source"]["repositories"]["core"])
tampered_release["source"]["repositories"]["core"]["commit"] = "0" * 40
tampered_release_path = root / "release-lock-v2-tampered.json"
tampered_release_path.write_bytes(canonical(tampered_release))
tampered_target = subprocess.run(
    [
        stack, "verify-release", "--release", tampered_release_path,
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path),
    ],
    check=False,
    capture_output=True,
    text=True,
)
assert tampered_target.returncode == 65
assert "does not match the supplied release-lock bytes" in tampered_target.stderr

# A root rotation is valid only when the new metadata has both old-root and
# new-root threshold signatures.  The final root continues to delegate the
# existing timestamp/snapshot/targets role keys.
rotated_tuf_dir = root / "tuf-metadata-rotated"
shutil.copytree(tuf_dir, rotated_tuf_dir)
rotated_root = generate_ed25519("root-v2")
rotated_keys = dict(root_keys)
del rotated_keys[tuf_keys["root"][2]]
rotated_keys[rotated_root[2]] = rotated_root[3]
rotated_roles = dict(roles)
rotated_roles["root"] = {"keyids": [rotated_root[2]], "threshold": 1}
root_v2_signed = {
    "_type": "root",
    "spec_version": "1.0.0",
    "consistent_snapshot": True,
    "version": 2,
    "expires": expiry,
    "keys": rotated_keys,
    "roles": rotated_roles,
}
root_v2 = {
    "signed": root_v2_signed,
    "signatures": [
        tuf_signature(root_v2_signed, tuf_keys["root"][0], tuf_keys["root"][2], "root-v2-old"),
        tuf_signature(root_v2_signed, rotated_root[0], rotated_root[2], "root-v2-new"),
    ],
}
write_tuf(rotated_tuf_dir / "2.root.json", root_v2)
rotated_verification = subprocess.run(
    [
        stack, "verify-release", "--release", release_path,
        "--tuf-metadata-dir", str(rotated_tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path), "--json",
    ],
    check=True,
    capture_output=True,
    text=True,
)
assert json.loads(rotated_verification.stdout)["authorization"]["tuf"]["metadataVersions"]["root"] == 2

invalid_rotation_dir = root / "tuf-metadata-invalid-rotation"
shutil.copytree(rotated_tuf_dir, invalid_rotation_dir)
root_v2_new_only = {"signed": root_v2_signed, "signatures": [root_v2["signatures"][1]]}
write_tuf(invalid_rotation_dir / "2.root.json", root_v2_new_only)
invalid_rotation = subprocess.run(
    [
        stack, "verify-release", "--release", release_path,
        "--tuf-metadata-dir", str(invalid_rotation_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path),
    ],
    check=False,
    capture_output=True,
    text=True,
)
assert invalid_rotation.returncode == 65
assert "previous-root authorization" in invalid_rotation.stderr

now = dt.datetime.now(dt.timezone.utc)
snapshot_targets = [
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
snapshot_records = [
    {
        "boundVolumeSnapshotContentName": f"content-{index}",
        "creationTimestamp": now.isoformat().replace("+00:00", "Z"),
        "namespace": namespace,
        "persistentVolumeClaim": pvc,
        "readyToUse": True,
        "restoreSize": "1Gi",
        "volumeSnapshotClassName": "test-csi",
        "volumeSnapshotName": f"snapshot-{index}",
        "volumeSnapshotUid": f"snapshot-uid-{index}",
    }
    for index, (namespace, pvc) in enumerate(snapshot_targets, start=1)
]
snapshot_request = {
    "schema": "kerosene.snapshot-attestation-request/v1",
    "kind": "VolumeSnapshotAttestationRequest",
    "environment": "staging-cell",
    "release": {"id": release["releaseId"], "lockDigest": release_digest},
    "requestedAt": now.isoformat().replace("+00:00", "Z"),
    "selection": {
        "labelSelector": "kerosene.io/release-id=" + release["releaseId"],
        "namespaces": ["kerosene-staging", "kerosene-staging-vault"],
    },
    "snapshotSetDigest": digest(canonical(snapshot_records)),
    "snapshots": snapshot_records,
    "attestation": {"externalSignerRequired": True, "status": "unsigned-request"},
}
snapshot_request_path = root / "snapshot-attestation-request.json"
json.dump(snapshot_request, open(snapshot_request_path, "w", encoding="utf-8"), sort_keys=True)
snapshot_path = root / "snapshot.json"
snapshot_key = root / "snapshot-provider.key"
snapshot_pub = root / "snapshot-provider.pub"
snapshot_provider_root = root / "snapshot-provider-key.b64"
subprocess.run(["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(snapshot_key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(["openssl", "pkey", "-in", str(snapshot_key), "-pubout", "-outform", "DER", "-out", str(snapshot_pub)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
snapshot_provider_root.write_text(base64.b64encode(snapshot_pub.read_bytes()).decode(), encoding="utf-8")
snapshot = {
    "schema": "kerosene.snapshot-receipt/v2",
    "releaseId": release["releaseId"],
    "environment": "staging-cell",
    "status": "verified",
    "snapshotId": "snapshot-8120",
    "provider": "test-provider",
    "snapshotSetDigest": snapshot_request["snapshotSetDigest"],
    "attestationRequestDigest": digest(canonical(snapshot_request)),
    "restoreTested": True,
    "restoreVerifiedAt": now.isoformat().replace("+00:00", "Z"),
    "createdAt": now.isoformat().replace("+00:00", "Z"),
    "expiresAt": (now + dt.timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
    "providerPublicKeyDerBase64": base64.b64encode(snapshot_pub.read_bytes()).decode(),
    "signatureBase64": "",
}
snapshot_payload = root / "snapshot.payload"
snapshot_signature = root / "snapshot.sig"
snapshot_payload.write_bytes(canonical({key: value for key, value in snapshot.items() if key != "signatureBase64"}))
subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(snapshot_key), "-in", str(snapshot_payload), "-out", str(snapshot_signature)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
snapshot["signatureBase64"] = base64.b64encode(snapshot_signature.read_bytes()).decode()
json.dump(snapshot, open(snapshot_path, "w", encoding="utf-8"))

fake_bin = root / "fake-bin"
fake_bin.mkdir()
fake_kubectl = fake_bin / "kubectl"
kustomize_path = shutil.which("kustomize")
assert kustomize_path
fake_kubectl.write_text(
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    "if [[ \"${1:-}\" == \"kustomize\" ]]; then\n"
    f"  exec {kustomize_path} build \"${{@:2}}\"\n"
    "fi\n",
    encoding="utf-8",
)
fake_kubectl.chmod(0o755)

state_dir = root / "state"
environment = dict(**__import__("os").environ)
environment["KUBECTL"] = str(fake_kubectl)
environment["KUSTOMIZE"] = kustomize_path

# The historical one-signature proof may remain readable for migration, but it
# must never authorize a v2 Cell rollout.
legacy_proof_state_dir = root / "legacy-proof-state"
legacy_apply = subprocess.run(
    [
        stack, "update", "--release", release_path, "--apply", "--dry-run",
        "--environment", "staging-cell", "--confirm-release", release["releaseId"],
        "--tuf-proof", str(tuf_dir / "timestamp.json"), "--tuf-root-key", str(tuf_root),
        "--tuf-state-dir", str(legacy_proof_state_dir),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path),
        "--snapshot-attestation-request", str(snapshot_request_path),
        "--snapshot-receipt", str(snapshot_path), "--snapshot-provider-key", str(snapshot_provider_root),
        "--state-dir", str(legacy_proof_state_dir),
    ],
    check=False,
    capture_output=True,
    text=True,
    env=environment,
)
assert legacy_apply.returncode == 78
assert "legacy --tuf-proof is supported only" in legacy_apply.stderr
assert not (legacy_proof_state_dir / "update-state.json").exists()

# A provider receipt cannot be replayed against an altered or incomplete
# collection request, even if the receipt signature itself remains valid.
tampered_request = dict(snapshot_request)
tampered_request["snapshots"] = list(snapshot_request["snapshots"])
tampered_request["snapshots"][0] = dict(tampered_request["snapshots"][0])
tampered_request["snapshots"][0]["restoreSize"] = "999Gi"
tampered_request_path = root / "snapshot-attestation-request-tampered.json"
json.dump(tampered_request, open(tampered_request_path, "w", encoding="utf-8"), sort_keys=True)
tampered_request_state_dir = root / "tampered-request-state"
tampered_request_apply = subprocess.run(
    [
        stack, "update", "--release", release_path, "--apply", "--dry-run",
        "--environment", "staging-cell", "--confirm-release", release["releaseId"],
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--tuf-state-dir", str(tampered_request_state_dir),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path),
        "--snapshot-attestation-request", str(tampered_request_path),
        "--snapshot-receipt", str(snapshot_path), "--snapshot-provider-key", str(snapshot_provider_root),
        "--state-dir", str(tampered_request_state_dir),
    ],
    check=False,
    capture_output=True,
    text=True,
    env=environment,
)
assert tampered_request_apply.returncode == 78
assert "does not match the canonical snapshot set" in tampered_request_apply.stderr
assert not (tampered_request_state_dir / "update-state.json").exists()
assert not (tampered_request_state_dir / "trusted-tuf-state.json").exists()

unrestored_snapshot = dict(snapshot)
unrestored_snapshot["restoreTested"] = False
unrestored_snapshot_path = root / "snapshot-not-restore-tested.json"
json.dump(unrestored_snapshot, open(unrestored_snapshot_path, "w", encoding="utf-8"))
unrestored_state_dir = root / "unrestored-state"
unrestored_apply = subprocess.run(
    [
        stack, "update", "--release", release_path, "--apply", "--dry-run",
        "--environment", "staging-cell", "--confirm-release", release["releaseId"],
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--tuf-state-dir", str(unrestored_state_dir),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path),
        "--snapshot-attestation-request", str(snapshot_request_path),
        "--snapshot-receipt", str(unrestored_snapshot_path), "--snapshot-provider-key", str(snapshot_provider_root),
        "--state-dir", str(unrestored_state_dir),
    ],
    check=False,
    capture_output=True,
    text=True,
    env=environment,
)
assert unrestored_apply.returncode == 78
assert "restoreTested: must be true" in unrestored_apply.stderr
assert not (unrestored_state_dir / "update-state.json").exists()

tampered_signature = bytearray(snapshot_signature.read_bytes())
assert tampered_signature
tampered_signature[0] ^= 0x01
tampered_snapshot = dict(snapshot)
tampered_snapshot["signatureBase64"] = base64.b64encode(tampered_signature).decode()
tampered_snapshot_path = root / "snapshot-tampered-signature.json"
json.dump(tampered_snapshot, open(tampered_snapshot_path, "w", encoding="utf-8"))
tampered_signature_state_dir = root / "tampered-signature-state"
tampered_signature_apply = subprocess.run(
    [
        stack, "update", "--release", release_path, "--apply", "--dry-run", "--json",
        "--environment", "staging-cell", "--confirm-release", release["releaseId"],
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--tuf-state-dir", str(tampered_signature_state_dir),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path),
        "--snapshot-attestation-request", str(snapshot_request_path),
        "--snapshot-receipt", str(tampered_snapshot_path),
        "--snapshot-provider-key", str(snapshot_provider_root),
        "--state-dir", str(tampered_signature_state_dir),
    ],
    check=False,
    capture_output=True,
    text=True,
    env=environment,
)
assert tampered_signature_apply.returncode == 78
assert "$.snapshot.signatureBase64: Ed25519 signature verification failed" in tampered_signature_apply.stderr
assert not (tampered_signature_state_dir / "update-state.json").exists()

other_snapshot_provider_key = root / "other-snapshot-provider.key"
other_snapshot_provider_pub = root / "other-snapshot-provider.pub"
subprocess.run(["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(other_snapshot_provider_key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(["openssl", "pkey", "-in", str(other_snapshot_provider_key), "-pubout", "-outform", "DER", "-out", str(other_snapshot_provider_pub)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
untrusted_provider_snapshot = dict(snapshot)
untrusted_provider_snapshot["providerPublicKeyDerBase64"] = base64.b64encode(other_snapshot_provider_pub.read_bytes()).decode()
untrusted_provider_snapshot_path = root / "snapshot-untrusted-provider.json"
json.dump(untrusted_provider_snapshot, open(untrusted_provider_snapshot_path, "w", encoding="utf-8"))
untrusted_provider_state_dir = root / "untrusted-provider-state"
untrusted_provider_apply = subprocess.run(
    [
        stack, "update", "--release", release_path, "--apply", "--dry-run", "--json",
        "--environment", "staging-cell", "--confirm-release", release["releaseId"],
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--tuf-state-dir", str(untrusted_provider_state_dir),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path),
        "--snapshot-attestation-request", str(snapshot_request_path),
        "--snapshot-receipt", str(untrusted_provider_snapshot_path),
        "--snapshot-provider-key", str(snapshot_provider_root),
        "--state-dir", str(untrusted_provider_state_dir),
    ],
    check=False,
    capture_output=True,
    text=True,
    env=environment,
)
assert untrusted_provider_apply.returncode == 78
assert "$.snapshot.providerPublicKeyDerBase64: does not match the trusted snapshot provider key" in untrusted_provider_apply.stderr
assert not (untrusted_provider_state_dir / "update-state.json").exists()

applied = subprocess.run(
    [
        stack, "update", "--release", release_path, "--apply", "--dry-run", "--json",
        "--environment", "staging-cell", "--confirm-release", release["releaseId"],
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--tuf-state-dir", str(state_dir),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path),
        "--snapshot-attestation-request", str(snapshot_request_path),
        "--snapshot-receipt", str(snapshot_path),
        "--snapshot-provider-key", str(snapshot_provider_root),
        "--state-dir", str(state_dir),
    ],
    check=False,
    capture_output=True,
    text=True,
    env=environment,
)
if applied.returncode != 0:
    print(applied.stdout)
    print(applied.stderr, file=sys.stderr)
    raise SystemExit(applied.returncode)
apply_result = json.loads(applied.stdout)
assert apply_result["status"] == "dry-run-passed"
state = json.load(open(state_dir / "update-state.json", encoding="utf-8"))
assert state["phase"] == "validate-and-commit"
assert state["status"] == "dry-run-passed"
trusted_tuf_state = json.load(open(state_dir / "trusted-tuf-state.json", encoding="utf-8"))
assert trusted_tuf_state["metadataVersions"]["targets"] == release["sequence"]
assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
assert stat.S_IMODE((state_dir / "update-state.json").stat().st_mode) == 0o600
assert stat.S_IMODE((state_dir / "trusted-tuf-state.json").stat().st_mode) == 0o600

check_before_apply = subprocess.run(
    [
        stack, "check-update", "--release", release_path, "--json",
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--tuf-state-dir", str(state_dir),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path), "--state-dir", str(state_dir),
    ],
    check=True,
    capture_output=True,
    text=True,
    env=environment,
)
check_result = json.loads(check_before_apply.stdout)
assert check_result["updateRequired"] is True
assert check_result["currentSequence"] is None
assert check_result["bankObserversVerified"] == 3

# check-update reads, but never advances, persistent TUF trust.  A metadata
# bundle older than previously trusted metadata is rejected as rollback.
rollback_tuf_state_dir = root / "rollback-tuf-state"
rollback_tuf_state_dir.mkdir()
rollback_tuf_state_path = rollback_tuf_state_dir / "trusted-tuf-state.json"
rollback_tuf_state_path.write_text(
    json.dumps(
        {
            "schema": "kerosene.tuf-trusted-state/v1",
            "metadataVersions": {"root": 1, "timestamp": 2, "snapshot": 1, "targets": release["sequence"] + 1},
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
rollback_tuf_state_before = rollback_tuf_state_path.read_bytes()
rollback_check = subprocess.run(
    [
        stack, "check-update", "--release", release_path,
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--tuf-state-dir", str(rollback_tuf_state_dir),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path),
    ],
    check=False,
    capture_output=True,
    text=True,
    env=environment,
)
assert rollback_check.returncode == 78
assert "older than persistently trusted version" in rollback_check.stderr
assert rollback_tuf_state_path.read_bytes() == rollback_tuf_state_before

state["status"] = "committed"
json.dump(state, open(state_dir / "update-state.json", "w", encoding="utf-8"))
check_after_apply = subprocess.run(
    [
        stack, "check-update", "--release", release_path, "--json",
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--tuf-state-dir", str(state_dir),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path), "--state-dir", str(state_dir),
    ],
    check=True,
    capture_output=True,
    text=True,
    env=environment,
)
check_result = json.loads(check_after_apply.stdout)
assert check_result["updateRequired"] is False
assert check_result["currentSequence"] == release["sequence"]

replay = subprocess.run(
    [
        stack, "update", "--release", release_path, "--apply", "--dry-run", "--json",
        "--environment", "staging-cell", "--confirm-release", release["releaseId"],
        "--tuf-metadata-dir", str(tuf_dir), "--tuf-trusted-root", str(tuf_trusted_root),
        "--tuf-state-dir", str(state_dir),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        *vault_args,
        "--bank-observer-report", str(report_path),
        "--snapshot-attestation-request", str(snapshot_request_path),
        "--snapshot-receipt", str(snapshot_path),
        "--snapshot-provider-key", str(snapshot_provider_root),
        "--state-dir", str(state_dir),
    ],
    check=False,
    capture_output=True,
    text=True,
    env=environment,
)
assert replay.returncode == 78
assert "not newer than the committed sequence" in replay.stderr
PY

echo "Kerosene Stack signed evidence tests passed."
