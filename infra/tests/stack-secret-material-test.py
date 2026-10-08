#!/usr/bin/env python3
"""Protected external Secret provisioning tests."""

import base64
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stack"))
import archive
import secret_material

loader = importlib.machinery.SourceFileLoader("secret_test_stack", str(ROOT / "kerosene-stack"))
spec = importlib.util.spec_from_loader(loader.name, loader)
stack = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stack
loader.exec_module(stack)
lifecycle = stack.lifecycle


class SecretMaterialTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kerosene-secret-material-test-")
        self.root = Path(self.temp.name)
        self.deployment = self.root / "deployment.json"
        workload = {
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": "unit", "namespace": "kerosene-staging"},
            "spec": {"template": {"spec": {"containers": [{
                "name": "unit", "env": [{"name": "TOKEN", "valueFrom": {
                    "secretKeyRef": {"name": "unit-secret", "key": "token"}}}]}]}}}}
        self.deployment.write_text(json.dumps({
            "schema": lifecycle.SCHEMA, "environment": "staging-cell",
            "resources": [
                {"apiVersion": "v1", "kind": "Namespace",
                 "metadata": {"name": "kerosene-staging"}},
                {"apiVersion": "v1", "kind": "Namespace",
                 "metadata": {"name": "kerosene-staging-vault"}},
                workload],
            "admin": {"image": "registry.invalid/admin@sha256:" + "a" * 64,
                      "config": {"apiBaseUrl": "https://core.invalid"}}}))
        self.value = self.root / "token"
        self.value.write_bytes(b"private-value")
        self.value.chmod(0o600)
        self.material = self.root / "material.json"
        self.material.write_text(json.dumps({
            "schema": secret_material.SCHEMA, "secrets": [{
                "namespace": "kerosene-staging", "name": "unit-secret", "type": "Opaque",
                "files": {"token": "token"}}]}))
        self.material.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def test_loads_exact_inventory_without_placing_plaintext_in_resource(self):
        _, resources = secret_material.load(stack, lifecycle, self.deployment, self.material)
        secret = resources[("kerosene-staging", "unit-secret")]
        self.assertEqual(secret["data"]["token"], base64.b64encode(b"private-value").decode())
        self.assertNotIn("private-value", json.dumps(secret))
        self.assertTrue(secret["immutable"])

    def test_rejects_shared_files_missing_inventory_and_unreferenced_secrets(self):
        self.value.chmod(0o640)
        with self.assertRaisesRegex(archive.ArchiveError, "owner-only"):
            secret_material.load(stack, lifecycle, self.deployment, self.material)
        self.value.chmod(0o600)
        document = json.loads(self.material.read_text())
        document["secrets"][0]["files"] = {"other": "token"}
        self.material.write_text(json.dumps(document))
        os.chmod(self.material, 0o600)
        with self.assertRaisesRegex(archive.ArchiveError, "required key"):
            secret_material.load(stack, lifecycle, self.deployment, self.material)
        document["secrets"][0].update(name="foreign", files={"token": "token"})
        self.material.write_text(json.dumps(document))
        os.chmod(self.material, 0o600)
        with self.assertRaisesRegex(archive.ArchiveError, "not referenced"):
            secret_material.load(stack, lifecycle, self.deployment, self.material)

    def test_provisions_only_to_explicitly_bound_cluster_and_never_overwrites(self):
        config = {"cluster": {"systemNamespaceUid": "cluster-uid"}}
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs.get("input_bytes")))
            return b""

        with patch.object(lifecycle, "load_config", return_value=config), \
             patch.object(lifecycle, "verify_bootstrap_trust"), \
             patch.object(lifecycle, "kubectl_command", return_value=["kubectl", "--bound"]), \
             patch.object(lifecycle, "run", side_effect=run):
            receipt = secret_material.provision(
                stack, lifecycle, "/cell", self.deployment, self.material)
        creates = [(command, payload) for command, payload in calls if "create" in command]
        self.assertEqual(len(creates), 3)
        self.assertEqual(receipt["clusterUid"], "cluster-uid")
        self.assertEqual(receipt["createdSecrets"][0]["keys"], ["token"])
        self.assertNotIn(b"private-value", b"".join(payload for _, payload in creates))

        def existing(command, **_kwargs):
            if "get" in command and "namespace" in command:
                return ("namespace/" + command[command.index("namespace") + 1] + "\n").encode()
            if "get" in command and "secret" in command:
                return b"secret/unit-secret\n"
            return b""

        with patch.object(lifecycle, "load_config", return_value=config), \
             patch.object(lifecycle, "verify_bootstrap_trust"), \
             patch.object(lifecycle, "kubectl_command", return_value=["kubectl", "--bound"]), \
             patch.object(lifecycle, "run", side_effect=existing), \
             self.assertRaisesRegex(archive.ArchiveError, "refusing to overwrite"):
            secret_material.provision(stack, lifecycle, "/cell", self.deployment, self.material)


if __name__ == "__main__":
    unittest.main()
