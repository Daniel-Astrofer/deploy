#!/usr/bin/env python3
"""Six-Node resource generation tests with synthetic public documents."""

import copy
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stack"))
import archive
import node_resources

loader = importlib.machinery.SourceFileLoader("node_resource_stack", str(ROOT / "kerosene-stack"))
spec = importlib.util.spec_from_loader(loader.name, loader)
stack = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stack
loader.exec_module(stack)
lifecycle = stack.lifecycle


class NodeResourcesTest(unittest.TestCase):
    def setUp(self):
        self.network = "synthetic-cell"
        self.genesis = {"network_id": self.network, "bank": {}, "vault": {}}
        self.payloads = {plane: ("state-" + plane).encode() for plane in ("bank", "vault")}
        self.manifests = {}
        self.attestations = {}
        for offset, plane in enumerate(("bank", "vault")):
            members = [{"member_id": f"{plane}-{index}",
                        "endpoint": "https://" + chr(97 + offset * 3 + index) * 56 + ".onion:8800"}
                       for index in range(3)]
            manifest = {"network_id": self.network, "plane": plane, "threshold": 2,
                        "members": members, "signatures": [{}, {}]}
            signable = {key: value for key, value in manifest.items() if key != "signatures"}
            self.manifests[plane] = manifest
            self.attestations[plane] = {
                "network_id": self.network, "plane": plane,
                "membership_manifest_hash": hashlib.sha256(archive.canonical_bytes(signable)).hexdigest(),
                "state_root": hashlib.sha256(self.payloads[plane]).hexdigest(),
                "signatures": [{}, {}]}

    def resources(self):
        snapshots = {plane: (self.attestations[plane], self.payloads[plane])
                     for plane in ("bank", "vault")}
        return node_resources.generate(self.genesis, self.manifests, snapshots)

    def test_generates_six_contract_valid_independent_nodes(self):
        generated = self.resources()
        items = generated["items"]
        deployments = [item for item in items if item["kind"] == "Deployment"]
        claims = [item for item in items if item["kind"] == "PersistentVolumeClaim"]
        self.assertEqual(len(deployments), 6)
        self.assertEqual(len(claims), 6)
        artifact = {"resources": copy.deepcopy(items)}
        node_image = "registry.invalid/node@sha256:" + "1" * 64
        tor_image = "registry.invalid/tor@sha256:" + "2" * 64
        for deployment in (item for item in artifact["resources"] if item["kind"] == "Deployment"):
            for container in deployment["spec"]["template"]["spec"]["containers"]:
                container["image"] = node_image if container["name"] == "node" else tor_image
        summary = {"networkId": self.network, "vaultCompatibility": {"members": 3},
                   "services": {"node": {"image": node_image}, "tor": {"image": tor_image}}}
        lifecycle.verify_node_runtime_contract(stack, artifact, summary)
        references = lifecycle.required_secret_references(stack, artifact)
        self.assertEqual(len(references), 18)

    def test_rejects_wrong_payload_unbalanced_or_non_onion_membership(self):
        snapshots = {plane: (self.attestations[plane], self.payloads[plane])
                     for plane in ("bank", "vault")}
        snapshots["bank"] = (self.attestations["bank"], b"changed")
        with self.assertRaisesRegex(archive.ArchiveError, "attestation"):
            node_resources.generate(self.genesis, self.manifests, snapshots)
        changed = copy.deepcopy(self.manifests)
        changed["vault"]["members"].pop()
        with self.assertRaisesRegex(archive.ArchiveError, "2-of-3"):
            node_resources.generate(self.genesis, changed, {
                plane: (self.attestations[plane], self.payloads[plane]) for plane in ("bank", "vault")})
        changed = copy.deepcopy(self.manifests)
        changed["bank"]["members"][0]["endpoint"] = "https://example.com:8800"
        with self.assertRaisesRegex(archive.ArchiveError, "Onion"):
            node_resources.generate(self.genesis, changed, {
                plane: (self.attestations[plane], self.payloads[plane]) for plane in ("bank", "vault")})


if __name__ == "__main__":
    unittest.main()
