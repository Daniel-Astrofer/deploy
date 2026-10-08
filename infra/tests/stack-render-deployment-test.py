#!/usr/bin/env python3
"""Deterministic deployment renderer tests; never contacts a cluster."""

import importlib.machinery
import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stack"))
import archive
import node_resources
import render_deployment
import vault_resources

stack_loader = importlib.machinery.SourceFileLoader("render_test_stack", str(ROOT / "kerosene-stack"))
stack_spec = importlib.util.spec_from_loader(stack_loader.name, stack_loader)
stack = importlib.util.module_from_spec(stack_spec)
sys.modules[stack_spec.name] = stack
stack_loader.exec_module(stack)
package_spec = importlib.util.spec_from_file_location(
    "render_test_package", ROOT / "stack/package-release.py")
package = importlib.util.module_from_spec(package_spec)
package_spec.loader.exec_module(package)


class RenderDeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kerosene-render-test-")
        self.root = Path(self.temp.name)
        self.admin_image = "registry.example.invalid/admin@sha256:" + "a" * 64
        self.images = self.root / "images.json"
        self.images.write_text(json.dumps({
            "schema": render_deployment.IMAGE_SELECTION_SCHEMA,
            "services": {"admin": {"image": self.admin_image}}}))
        self.resources = self.root / "resources.yaml"
        self.resources.write_text("apiVersion: v1\nkind: Namespace\nmetadata:\n  name: kerosene-staging\n")
        self.admin = self.root / "admin.json"
        self.admin.write_text(json.dumps({"apiBaseUrl": "https://core.invalid"}))

    def tearDown(self):
        self.temp.cleanup()

    def test_renders_new_canonical_validated_deployment(self):
        namespace = {"apiVersion": "v1", "kind": "Namespace",
                     "metadata": {"name": "kerosene-staging"}}
        output = self.root / "deployment.json"
        with patch.object(render_deployment, "decode_yaml", return_value=[namespace]):
            result = render_deployment.render(
                stack, package, self.images, self.resources, self.admin, output,
                kubectl="/not-executed")
        raw = output.read_bytes()
        self.assertEqual(raw, archive.canonical_bytes(json.loads(raw)) + b"\n")
        self.assertEqual(result["digest"], archive.bytes_digest(raw))
        self.assertEqual(json.loads(raw)["admin"]["image"], self.admin_image)
        self.assertEqual(output.stat().st_mode & 0o777, 0o444)
        with patch.object(render_deployment, "decode_yaml", return_value=[namespace]), \
                self.assertRaisesRegex(FileExistsError, "deployment.json"):
            render_deployment.render(
                stack, package, self.images, self.resources, self.admin, output,
                kubectl="/not-executed")

    def test_substitutes_only_explicit_selected_placeholders(self):
        core = "registry.example.invalid/core@sha256:" + "c" * 64
        selected = {"admin": {"image": self.admin_image}, "core": {"image": core}}
        resource = {"metadata": {}, "spec": {"template": {"spec": {"containers": [
            {"image": "kerosene-cell.invalid/core:selected"}]}}}}
        normalized = render_deployment.normalize_resources([resource], selected)
        self.assertEqual(normalized[0]["spec"]["template"]["spec"]["containers"][0]["image"], core)
        resource["spec"]["template"]["spec"]["containers"][0]["image"] = "core:latest"
        with self.assertRaisesRegex(archive.ArchiveError, "placeholder"):
            render_deployment.normalize_resources([resource], selected)

    def test_composes_multiple_reviewed_resource_files(self):
        output = self.root / "composed.json"
        namespaces = [
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "kerosene-staging"}},
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "kerosene-staging-vault"}}]
        with patch.object(render_deployment, "decode_yaml", side_effect=[[namespaces[0]], [namespaces[1]]]) as decode:
            render_deployment.render(stack, package, self.images,
                                     [self.resources, self.resources], self.admin, output,
                                     kubectl="/not-executed")
        self.assertEqual(decode.call_count, 2)
        self.assertEqual(len(json.loads(output.read_bytes())["resources"]), 2)

    def test_rejects_unknown_fields_and_unpinned_images(self):
        document = json.loads(self.images.read_text())
        document["unexpected"] = True
        with self.assertRaises(stack.ReleaseValidationError):
            render_deployment.selected_images(document, stack)
        document.pop("unexpected")
        document["services"]["admin"]["image"] = "admin:latest"
        with self.assertRaises(stack.ReleaseValidationError):
            render_deployment.selected_images(document, stack)

    @unittest.skipUnless(shutil.which("kubectl"), "kubectl not installed")
    def test_real_kubectl_decodes_local_yaml_without_cluster(self):
        resources = render_deployment.decode_yaml(shutil.which("kubectl"), self.resources)
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0]["kind"], "Namespace")
        self.assertEqual(resources[0]["metadata"]["name"], "kerosene-staging")

    @unittest.skipUnless(shutil.which("kubectl"), "kubectl not installed")
    def test_renders_complete_eleven_component_cell(self):
        services = ("admin", "core", "kfe", "web-page", "postgres", "redis",
                    "bitcoin", "lnd", "node", "vault", "tor")
        self.images.write_text(json.dumps({
            "schema": render_deployment.IMAGE_SELECTION_SCHEMA,
            "services": {name: {"image": "registry.invalid/" + name + "@sha256:" +
                                 format(index, "x") * 64}
                         for index, name in enumerate(services, 1)}}))
        foundation = self.root / "foundation.yaml"
        foundation.write_bytes(subprocess.run(
            [shutil.which("kubectl"), "kustomize", str(ROOT / "kubernetes/overlays/complete-cell")],
            check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=60).stdout)

        network = "synthetic-cell"
        genesis = {"network_id": network, "bank": {}, "vault": {}}
        manifests, snapshots = {}, {}
        for offset, plane in enumerate(("bank", "vault")):
            members = [{"member_id": f"{plane}-{index}",
                        "endpoint": "https://" + chr(97 + offset * 3 + index) * 56 + ".onion:8800"}
                       for index in range(3)]
            manifest = {"network_id": network, "plane": plane, "threshold": 2,
                        "members": members, "signatures": [{}, {}]}
            payload = ("state-" + plane).encode()
            signable = {key: value for key, value in manifest.items() if key != "signatures"}
            attestation = {
                "network_id": network, "plane": plane,
                "membership_manifest_hash": hashlib.sha256(
                    archive.canonical_bytes(signable)).hexdigest(),
                "state_root": hashlib.sha256(payload).hexdigest(), "signatures": [{}, {}]}
            manifests[plane] = manifest
            snapshots[plane] = (attestation, payload)
        nodes = self.root / "nodes.json"
        vaults = self.root / "vaults.json"
        node_resources.write(node_resources.generate(genesis, manifests, snapshots), nodes)
        vault_resources.write(vault_resources.generate(network, "a" * 64), vaults)
        self.admin.write_text(json.dumps({"apiBaseUrl": "https://core.invalid",
                                          "kfeBaseUrl": "https://kfe.invalid"}))

        output = self.root / "complete-deployment.json"
        result = render_deployment.render(
            stack, package, self.images, [foundation, nodes, vaults], self.admin, output)
        deployment = json.loads(output.read_bytes())
        workloads = [resource for resource in deployment["resources"]
                     if resource["kind"] in render_deployment.lifecycle.WORKLOADS]
        observed = {container["image"] for resource in workloads
                    for container in render_deployment.lifecycle.containers(resource)}
        selected = {value["image"] for name, value in
                    json.loads(self.images.read_text())["services"].items() if name != "admin"}
        self.assertEqual(len(workloads), 16)
        self.assertEqual(observed, selected)
        self.assertEqual(set(result["configurationDigests"]), set(services))
        unsafe_foundation = self.root / "unsafe-foundation.yaml"
        unsafe_foundation.write_text(
            foundation.read_text().replace("automountServiceAccountToken: false",
                                           "automountServiceAccountToken: true", 1))
        with self.assertRaisesRegex(stack.ApplyBlockedError, "disabled ServiceAccount token"):
            render_deployment.render(
                stack, package, self.images, [unsafe_foundation, nodes, vaults], self.admin,
                self.root / "unsafe-deployment.json")


if __name__ == "__main__":
    unittest.main()
