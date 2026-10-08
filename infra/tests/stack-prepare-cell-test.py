#!/usr/bin/env python3
"""One-command complete Cell preparation tests; never contacts a cluster."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("kubectl"), "kubectl not installed")
class PrepareCellTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kerosene-prepare-cell-test-")
        self.root = Path(self.temp.name)
        services = ("admin", "core", "kfe", "web-page", "postgres", "redis",
                    "bitcoin", "lnd", "node", "vault", "tor")
        (self.root / "images.json").write_text(json.dumps({
            "schema": "kerosene.cell-images/v1",
            "services": {name: {"image": "registry.invalid/" + name + "@sha256:" +
                                 format(index, "x") * 64}
                         for index, name in enumerate(services, 1)}}))
        (self.root / "admin.json").write_text(json.dumps({
            "apiBaseUrl": "https://core.invalid", "kfeBaseUrl": "https://kfe.invalid"}))
        network = "synthetic-cell"
        (self.root / "genesis.json").write_text(json.dumps({
            "network_id": network, "bank": {}, "vault": {}}))
        planes = {}
        for offset, plane in enumerate(("bank", "vault")):
            manifest = {"network_id": network, "plane": plane, "threshold": 2,
                        "members": [{"member_id": f"{plane}-{index}",
                                     "endpoint": "https://" +
                                     chr(97 + offset * 3 + index) * 56 + ".onion:8800"}
                                    for index in range(3)],
                        "signatures": [{}, {}]}
            payload = ("state-" + plane).encode()
            signable = {key: value for key, value in manifest.items() if key != "signatures"}
            attestation = {
                "network_id": network, "plane": plane,
                "membership_manifest_hash": hashlib.sha256(json.dumps(
                    signable, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "state_root": hashlib.sha256(payload).hexdigest(), "signatures": [{}, {}]}
            (self.root / f"{plane}-membership.json").write_text(json.dumps(manifest))
            (self.root / f"{plane}-attestation.json").write_text(json.dumps(attestation))
            (self.root / f"{plane}-snapshot.bin").write_bytes(payload)
            planes[plane] = {name: f"{plane}-{name}." + ("bin" if name == "snapshot" else "json")
                             for name in ("membership", "attestation", "snapshot")}
        self.config = self.root / "cell.json"
        self.config.write_text(json.dumps({
            "schema": "kerosene.cell-preparation/v1", "images": "images.json",
            "adminConfig": "admin.json", "genesis": "genesis.json", "planes": planes,
            "vaultMeasurementPin": "a" * 64}))

    def tearDown(self):
        self.temp.cleanup()

    def test_single_command_prepares_non_overwritable_complete_cell(self):
        output = self.root / "prepared"
        command = [str(ROOT / "prepare-cell-deployment"), "--config", str(self.config),
                   "--output-dir", str(output)]
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, check=True, timeout=60)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["directory"], str(output))
        self.assertEqual({path.name for path in output.iterdir()},
                         {"foundation.yaml", "nodes.json", "vaults.json", "deployment.json"})
        deployment = json.loads((output / "deployment.json").read_bytes())
        workloads = [item for item in deployment["resources"]
                     if item["kind"] in ("Deployment", "StatefulSet")]
        self.assertEqual(len(workloads), 16)
        self.assertTrue(all(path.stat().st_mode & 0o222 == 0 for path in output.iterdir()))
        repeated = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, check=False, timeout=60)
        self.assertEqual(repeated.returncode, 65)
        self.assertIn("preparation failed", repeated.stderr)


if __name__ == "__main__":
    unittest.main()
