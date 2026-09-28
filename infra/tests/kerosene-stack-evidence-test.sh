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
release_digest = digest(canonical(release))

tuf_key = root / "tuf-root.key"
tuf_pub = root / "tuf-root.pub"
tuf_root = root / "tuf-root-key.b64"
subprocess.run(["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(tuf_key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(["openssl", "pkey", "-in", str(tuf_key), "-pubout", "-outform", "DER", "-out", str(tuf_pub)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
tuf_root.write_text(base64.b64encode(tuf_pub.read_bytes()).decode(), encoding="utf-8")
tuf_proof = {
    "schema": "kerosene.tuf-target-proof/v1",
    "targetPath": release["authorization"]["tuf"]["targetPath"],
    "metadataVersion": release["authorization"]["tuf"]["metadataVersion"],
    "targetDigest": release["authorization"]["tuf"]["targetDigest"],
    "expiresAt": release["authorization"]["tuf"]["expiresAt"],
    "signatureBase64": "",
}
tuf_payload = root / "tuf.payload"
tuf_signature = root / "tuf.sig"
tuf_payload.write_bytes(canonical({key: value for key, value in tuf_proof.items() if key != "signatureBase64"}))
subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(tuf_key), "-in", str(tuf_payload), "-out", str(tuf_signature)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
tuf_proof["signatureBase64"] = base64.b64encode(tuf_signature.read_bytes()).decode()
tuf_proof_path = root / "tuf-proof.json"
json.dump(tuf_proof, open(tuf_proof_path, "w", encoding="utf-8"))
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
signed_document(
    {
        "schema": "kerosene.bank-observer-report/v1",
        "releaseId": release["releaseId"],
        "networkId": release["network"]["id"],
        "targetSequence": release["sequence"],
        "releaseLockCanonicalDigest": release_digest,
        "observations": [
            {
                "observerId": f"validator-{member}",
                "status": "compatible",
                "observedSequence": release["sequence"],
                "releaseDigest": release_digest,
            }
            for member in range(1, 4)
        ],
    },
    report_path,
)

result = subprocess.run(
    [
        stack, "verify-release", "--release", release_path,
        "--tuf-proof", str(tuf_proof_path), "--tuf-root-key", str(tuf_root),
        "--bft-receipt", str(receipt_path),
        "--validator-roster", str(roster_path),
        "--bank-observer-report", str(report_path),
        "--json",
    ],
    check=True,
    capture_output=True,
    text=True,
)
verification = json.loads(result.stdout)
assert verification["cryptographicAuthorization"] == "verified"
assert verification["authorization"]["tuf"]["signatureVerified"] is True
assert verification["authorization"]["bft"]["signaturesVerified"] == 3
assert verification["authorization"]["bankObservers"]["compatibleObservers"] == 3

now = dt.datetime.now(dt.timezone.utc)
snapshot_path = root / "snapshot.json"
json.dump(
    {
        "schema": "kerosene.snapshot-receipt/v1",
        "releaseId": release["releaseId"],
        "environment": "staging-cell",
        "status": "verified",
        "snapshotId": "snapshot-8120",
        "provider": "test-provider",
        "snapshotDigest": "sha256:" + "f" * 64,
        "createdAt": now.isoformat().replace("+00:00", "Z"),
        "expiresAt": (now + dt.timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
    },
    open(snapshot_path, "w", encoding="utf-8"),
)

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
applied = subprocess.run(
    [
        stack, "update", "--release", release_path, "--apply", "--dry-run", "--json",
        "--environment", "staging-cell", "--confirm-release", release["releaseId"],
        "--tuf-proof", str(tuf_proof_path), "--tuf-root-key", str(tuf_root),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        "--bank-observer-report", str(report_path), "--snapshot-receipt", str(snapshot_path),
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

check_before_apply = subprocess.run(
    [
        stack, "check-update", "--release", release_path, "--json",
        "--tuf-proof", str(tuf_proof_path), "--tuf-root-key", str(tuf_root),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
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

state["status"] = "committed"
json.dump(state, open(state_dir / "update-state.json", "w", encoding="utf-8"))
check_after_apply = subprocess.run(
    [
        stack, "check-update", "--release", release_path, "--json",
        "--tuf-proof", str(tuf_proof_path), "--tuf-root-key", str(tuf_root),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
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
        "--tuf-proof", str(tuf_proof_path), "--tuf-root-key", str(tuf_root),
        "--bft-receipt", str(receipt_path), "--validator-roster", str(roster_path),
        "--bank-observer-report", str(report_path), "--snapshot-receipt", str(snapshot_path),
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
