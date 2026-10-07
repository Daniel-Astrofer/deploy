#!/usr/bin/env python3
"""Three-Vault resource generation tests."""

import copy
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stack"))
import archive
import node_resources
import vault_resources

loader = importlib.machinery.SourceFileLoader("vault_resource_stack", str(ROOT / "kerosene-stack"))
spec = importlib.util.spec_from_loader(loader.name, loader)
stack = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stack
loader.exec_module(stack)
lifecycle = stack.lifecycle


class VaultResourcesTest(unittest.TestCase):
    def test_generates_three_contract_valid_independent_vaults(self):
        generated = vault_resources.generate("synthetic-cell", "a" * 64)
        artifact = {"resources": copy.deepcopy(generated["items"])}
        vault_image = "registry.invalid/vault@sha256:" + "1" * 64
        tor_image = "registry.invalid/tor@sha256:" + "2" * 64
        deployments = [item for item in artifact["resources"] if item["kind"] == "Deployment"]
        self.assertEqual(len(deployments), 3)
        self.assertEqual(len([item for item in artifact["resources"]
                              if item["kind"] == "PersistentVolumeClaim"]), 3)
        for deployment in deployments:
            for container in deployment["spec"]["template"]["spec"]["containers"]:
                container["image"] = vault_image if container["name"] == "vault" else tor_image
        summary = {"vaultCompatibility": {"members": 3},
                   "services": {"vault": {"image": vault_image}, "tor": {"image": tor_image}}}
        lifecycle.verify_vault_probe_configuration(stack, artifact)
        lifecycle.verify_vault_runtime_contract(stack, artifact, summary)
        references = lifecycle.required_secret_references(stack, artifact)
        self.assertEqual(len(references), 9)
        for index in range(1, 4):
            self.assertEqual(references[("kerosene-staging-vault", f"vault-{index}-runtime")],
                             {"seed-peers", "audit-pubkeys", "tls-peer-spiffe-ids",
                              "attestation-root", "data-passphrase", "node-url"})

    def test_rejects_unpinned_measurement_and_invalid_network(self):
        for network, measurement in (("x", "a" * 64), ("synthetic-cell", "A" * 64),
                                     ("synthetic-cell", "a" * 63)):
            with self.subTest(network=network, measurement=measurement), \
                    self.assertRaises(archive.ArchiveError):
                vault_resources.generate(network, measurement)

    def test_combined_generators_form_exact_independent_critical_topology(self):
        items = copy.deepcopy(vault_resources.generate("synthetic-cell", "a" * 64)["items"])
        for plane, namespace in (("bank", "kerosene-staging"),
                                 ("vault", "kerosene-staging-vault")):
            for index in range(1, 4):
                name = f"node-{plane}-{index}"
                items.extend([
                    {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
                     "metadata": {"name": name + "-data", "namespace": namespace}},
                    node_resources.node_workload(name, namespace, plane, "synthetic-cell")])
        images = {"node": "registry.invalid/node@sha256:" + "1" * 64,
                  "vault": "registry.invalid/vault@sha256:" + "2" * 64,
                  "tor": "registry.invalid/tor@sha256:" + "3" * 64}
        for item in items:
            if item["kind"] != "Deployment":
                continue
            for container in item["spec"]["template"]["spec"]["containers"]:
                container["image"] = images[container["name"]]
        topology = lifecycle.critical_replica_topology(
            stack, {"resources": items},
            {"services": {name: {"image": image} for name, image in images.items()},
             "vaultCompatibility": {"members": 3}})
        self.assertEqual(len(topology["node"]), 6)
        self.assertEqual(len(topology["vault"]), 3)
        self.assertEqual(len({member["storage"] for group in topology.values() for member in group}), 9)


if __name__ == "__main__":
    unittest.main()
