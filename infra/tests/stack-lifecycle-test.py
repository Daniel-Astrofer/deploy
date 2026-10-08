#!/usr/bin/env python3
"""Local lifecycle/configuration validation tests; not a financial Cell E2E."""
import copy
import base64
import datetime as dt
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("tested_stack", str(ROOT / "kerosene-stack"))
spec = importlib.util.spec_from_loader(loader.name, loader)
stack = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stack
loader.exec_module(stack)
lifecycle = stack.lifecycle
import vault_resources


class DeploymentTest(unittest.TestCase):
    def test_integrated_node_vault_protocol_is_qualified(self):
        self.assertNotIn("node-vault-live-protocol-quorum-not-qualified", lifecycle.EXECUTION_BLOCKERS)

    def test_independent_vault_rebuild_provenance_is_qualified(self):
        self.assertNotIn("vault-live-rebuild-provenance-not-qualified", lifecycle.EXECUTION_BLOCKERS)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.release = json.loads((ROOT / "stack/examples/release-lock-v2.example.json").read_text())
        self.node_names = {"node-bank", "node-bank-2", "node-bank-3",
                           "node-vault", "node-vault-2", "node-vault-3"}
        resources = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}
                     for namespace in ("kerosene-staging", "kerosene-staging-vault")]
        resources.append({"apiVersion": "v1", "kind": "ConfigMap",
                          "metadata": {"name": "vault-probe", "namespace": "kerosene-staging"},
                          "data": {"url": "https://localhost:7801/v1/local-health"}})
        for plane, namespace in (("bank", "kerosene-staging"), ("vault", "kerosene-staging-vault")):
            resources.append({"apiVersion": "v1", "kind": "ConfigMap", "immutable": True,
                              "metadata": {"name": "node-genesis", "namespace": namespace},
                              "data": {"genesis-trust-bundle.json": json.dumps({
                                  "network_id": self.release["network"]["id"]})}})
            resources.append({"apiVersion": "v1", "kind": "ConfigMap",
                              "metadata": {"name": "node-" + plane + "-bootstrap", "namespace": namespace},
                              "data": {"genesis-endpoints": "", "mirrors": ""}})
            resources.append({"apiVersion": "v1", "kind": "ConfigMap", "immutable": True,
                              "metadata": {"name": "node-" + plane + "-state", "namespace": namespace},
                              "data": {"attestation.json": "{}", "snapshot.bin": "synthetic-state"}})
            resources.append({"apiVersion": "v1", "kind": "ConfigMap", "immutable": True,
                              "metadata": {"name": "node-" + plane + "-membership", "namespace": namespace},
                              "data": {"manifest.json": json.dumps({
                                  "network_id": self.release["network"]["id"], "plane": plane})}})
        for name, service in self.release["services"].items():
            if name in {"admin", "vault"}:
                continue
            instances = ([{"name": "node-" + plane + ("" if index == 1 else "-" + str(index)),
                           "plane": plane,
                           "namespace": "kerosene-staging" if plane == "bank" else "kerosene-staging-vault"}
                          for plane in ("bank", "vault") for index in range(1, 4)]
                         if name == "node" else
                         [{"name": instance, "namespace": "kerosene-staging"}
                          for instance in ([name] if name != "vault" else ["vault-1", "vault-2", "vault-3"])])
            for instance_spec in instances:
                instance = instance_spec["name"]
                namespace = instance_spec["namespace"]
                container = {"name": name, "image": service["image"]}
                if name == "node":
                    plane = instance_spec["plane"]
                    resources.append({"apiVersion": "v1", "kind": "ConfigMap", "immutable": True,
                                      "metadata": {"name": instance + "-tor", "namespace": namespace},
                                      "data": {"torrc": lifecycle.NODE_TORRC}})
                    values = {"KEROSENE_NETWORK_ID": self.release["network"]["id"],
                              "KEROSENE_DISCOVERY_PLANE": plane, "KEROSENE_NODE_LISTEN_ADDR": "127.0.0.1:8800",
                              "KEROSENE_NODE_ONION_HOSTNAME_PATH": "/onion/hostname",
                              "KEROSENE_NODE_ONION_PORT": "8800", "KEROSENE_IDENTITY_KEY_PATH": "/var/lib/kerosene/identity.key",
                              "KEROSENE_PEER_STORE": "/var/lib/kerosene/peer-store", "KEROSENE_LEDGER_DB_PATH": "/var/lib/kerosene/ledger",
                              "KEROSENE_GENESIS_TRUST_BUNDLE": "/etc/kerosene/node-genesis/genesis-trust-bundle.json",
                              "KEROSENE_TLS_CERT_PATH": "/etc/kerosene/node-mtls/server.crt",
                              "KEROSENE_TLS_KEY_PATH": "/etc/kerosene/node-mtls/server.key",
                              "KEROSENE_TLS_CLIENT_CA_PATH": "/etc/kerosene/node-mtls/ca.crt",
                              "KEROSENE_TLS_CLIENT_IDENTITY_PEM": "/etc/kerosene/node-mtls/client-identity.pem",
                              "KEROSENE_INITIAL_MEMBERSHIP_MANIFEST_PATH": "/etc/kerosene/node-membership/manifest.json",
                              "KEROSENE_STATE_SNAPSHOT_ATTESTATION_PATH": "/etc/kerosene/node-state/attestation.json",
                              "KEROSENE_STATE_SNAPSHOT_PAYLOAD_PATH": "/etc/kerosene/node-state/snapshot.bin",
                              "KEROSENE_TOR_SOCKS_PROXY": "socks5h://127.0.0.1:9050",
                              "KEROSENE_CHALLENGE_TTL_MS": "300000"}
                    container["env"] = [{"name": key, "value": value} for key, value in values.items()]
                    container["env"].extend([
                        {"name": "KEROSENE_GENESIS_ENDPOINTS", "valueFrom": {"configMapKeyRef": {"name": "node-" + plane + "-bootstrap", "key": "genesis-endpoints"}}},
                        {"name": "KEROSENE_DISCOVERY_MIRRORS", "valueFrom": {"configMapKeyRef": {"name": "node-" + plane + "-bootstrap", "key": "mirrors"}}}])
                    container["readinessProbe"] = {"exec": {"command": ["/usr/local/bin/kerosene-node", "--health-probe"]}, "timeoutSeconds": 6}
                if name == "vault":
                    values = {"KEROSENE_ENV": "production", "VAULT_CEREMONY_MODE": "production",
                              "VAULT_AUTH_MODE": "mtls", "VAULT_TRANSPORT": "tor",
                              "VAULT_DKG_MODE": "distributed_wire", "VAULT_NODE_TIER": "domestic",
                              "ATTESTATION_MODE": "software", "VAULT_LISTEN_ADDR": "127.0.0.1:7801",
                              "VAULT_GENESIS_N": "3", "VAULT_TLS_VERIFY_MODE": "onion_or_spiffe",
                              "VAULT_MEASUREMENT_PIN": "a" * 64, "BITCOIN_NETWORK": "testnet3",
                              "VAULT_SOCKS_PROXY": "socks5h://127.0.0.1:9050",
                              "VAULT_SHARE_STORE": "aead_disk", "VAULT_DATA_DIR": "/var/lib/kerosene-vault"}
                    container["env"] = [{"name": key, "value": value} for key, value in values.items()]
                    container["env"].extend([
                        {"name": "VAULT_SEED_PEERS", "valueFrom": {"secretKeyRef": {"name": instance + "-runtime", "key": "seed-peers"}}},
                        {"name": "VAULT_AUDIT_PUBKEY_ALLOWLIST", "valueFrom": {"secretKeyRef": {"name": instance + "-runtime", "key": "audit-pubkeys"}}},
                        {"name": "VAULT_TLS_PEER_SPIFFE_ID", "valueFrom": {"secretKeyRef": {"name": instance + "-runtime", "key": "tls-peer-spiffe-ids"}}},
                        {"name": "VAULT_ATTESTATION_ROOT", "valueFrom": {"secretKeyRef": {"name": instance + "-runtime", "key": "attestation-root"}}},
                        {"name": "VAULT_DATA_PASSPHRASE", "valueFrom": {"secretKeyRef": {"name": instance + "-runtime", "key": "data-passphrase"}}},
                        {"name": "VAULT_HEALTH_PROBE_URL", "valueFrom": {"configMapKeyRef": {"name": "vault-probe", "key": "url"}}}])
                    container["readinessProbe"] = {"exec": {"command": ["/usr/local/bin/kerosene-vault", "--health-probe"]}, "timeoutSeconds": 6}
                pod_spec = {"containers": [container]}
                if name in {"node", "vault"}:
                    tor = {"name": "tor", "image": self.release["services"]["tor"]["image"]}
                    pod_spec["containers"].append(tor)
                if name in {"node", "vault"}:
                    claim = instance + "-data"
                    container["volumeMounts"] = [{"name": "identity-data", "mountPath": "/var/lib/kerosene"}]
                    pod_spec["volumes"] = [{"name": "identity-data", "persistentVolumeClaim": {"claimName": claim}}]
                    if name == "node":
                        tor["env"] = [
                            {"name": "KEROSENE_TOR_IDENTITY_SOURCE", "value": "/etc/kerosene/tor-identity"},
                            {"name": "KEROSENE_TOR_HIDDEN_SERVICE_DIR", "value": "/var/lib/tor/node"},
                            {"name": "KEROSENE_TOR_ONION_PUBLISH_PATH", "value": "/onion/hostname"}]
                        tor["volumeMounts"] = [
                            {"name": "identity-data", "mountPath": "/var/lib/tor"},
                            {"name": "onion-public", "mountPath": "/onion"},
                            {"name": "tor-config", "mountPath": "/etc/tor/torrc", "subPath": "torrc", "readOnly": True},
                            {"name": "tor-identity", "mountPath": "/etc/kerosene/tor-identity", "readOnly": True}]
                        container["volumeMounts"].append({"name": "onion-public", "mountPath": "/onion", "readOnly": True})
                        container["volumeMounts"].append({"name": "node-identity", "mountPath": "/var/lib/kerosene/identity.key", "subPath": "identity.key", "readOnly": True})
                        container["volumeMounts"].append({"name": "node-genesis", "mountPath": "/etc/kerosene/node-genesis", "readOnly": True})
                        container["volumeMounts"].append({"name": "node-mtls", "mountPath": "/etc/kerosene/node-mtls", "readOnly": True})
                        container["volumeMounts"].append({"name": "initial-membership", "mountPath": "/etc/kerosene/node-membership/manifest.json", "subPath": "manifest.json", "readOnly": True})
                        container["volumeMounts"].append({"name": "state-snapshot", "mountPath": "/etc/kerosene/node-state", "readOnly": True})
                        pod_spec["volumes"].append({"name": "onion-public", "emptyDir": {}})
                        pod_spec["volumes"].append({"name": "node-identity", "secret": {"secretName": instance + "-identity", "defaultMode": 256, "items": [{"key": "identity.key", "path": "identity.key", "mode": 256}]}})
                        pod_spec["volumes"].append({"name": "node-genesis", "configMap": {"name": "node-genesis"}})
                        pod_spec["volumes"].append({"name": "node-mtls", "secret": {"secretName": instance + "-mtls", "defaultMode": 256, "items": [{"key": key, "path": key, "mode": 256} for key in ("ca.crt", "client-identity.pem", "server.crt", "server.key")]}})
                        pod_spec["volumes"].append({"name": "tor-config", "configMap": {"name": instance + "-tor"}})
                        pod_spec["volumes"].append({"name": "tor-identity", "secret": {"secretName": instance + "-onion-identity", "defaultMode": 256, "items": [{"key": key, "path": key, "mode": 256} for key in ("hostname", "hs_ed25519_public_key", "hs_ed25519_secret_key")]}})
                        pod_spec["volumes"].append({"name": "initial-membership", "configMap": {"name": "node-" + instance_spec["plane"] + "-membership"}})
                        pod_spec["volumes"].append({"name": "state-snapshot", "configMap": {"name": "node-" + instance_spec["plane"] + "-state"}})
                    resources.append({"apiVersion": "v1", "kind": "PersistentVolumeClaim",
                                      "metadata": {"name": claim, "namespace": namespace},
                                      "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}}})
                resources.append({"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": instance, "namespace": namespace}, "spec": {"replicas": 1, "selector": {"matchLabels": {"app": instance}}, "template": {"metadata": {"labels": {"app": instance}}, "spec": pod_spec}}})
        vault_items = vault_resources.generate(
            self.release["network"]["id"], "a" * 64)["items"]
        for resource in vault_items:
            if resource["kind"] == "Deployment":
                for runtime in resource["spec"]["template"]["spec"]["containers"]:
                    runtime["image"] = self.release["services"][runtime["name"]]["image"]
        resources.extend(vault_items)
        self.artifact = {"schema": lifecycle.SCHEMA, "environment": "staging-cell", "resources": resources, "admin": {"image": self.release["services"]["admin"]["image"], "config": {"apiBaseUrl": "https://core.invalid"}}}
        for name, service in self.release["services"].items():
            service["configDigest"] = lifecycle.digest(lifecycle.component_config(self.artifact, service["image"], name))
        self.summary = stack.validate_release(self.release)
        self.path = self.root / "deployment.json"

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self, artifact=None):
        self.path.write_text(json.dumps(artifact or self.artifact))
        return lifecycle.verify_deployment(stack, self.release, self.summary, str(self.path))

    @staticmethod
    def critical_observations(_stack, _kubectl, _component, members, _threshold):
        return [{"identity": list(lifecycle.identity(member["resource"])), "uid": "unit-uid",
                 "resourceVersion": "7", "storage": member["storage"]} for member in members]

    def test_exact_configuration(self):
        self.assertEqual(self.verify(), self.artifact)

    def test_vault_probe_requires_url_in_approved_configuration(self):
        vault = next(r for r in self.artifact["resources"] if r["metadata"]["name"] == "vault-1")
        container = next(item for item in vault["spec"]["template"]["spec"]["containers"]
                         if item["name"] == "vault")
        container["readinessProbe"] = {"exec": {"command": ["/usr/local/bin/kerosene-vault", "--health-probe"]}, "timeoutSeconds": 6}
        container["env"] = [{"name": "VAULT_HEALTH_PROBE_URL", "valueFrom": {"configMapKeyRef": {"name": "probe", "key": "url"}}}]
        with self.assertRaises(stack.ApplyBlockedError):
            lifecycle.verify_vault_probe_configuration(stack, self.artifact)
        config = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"namespace": "kerosene-staging-vault", "name": "probe"}, "data": {"url": "https://vault.example:7801/v1/local-health"}}
        self.artifact["resources"].append(config)
        lifecycle.verify_vault_probe_configuration(stack, self.artifact)
        for url in ("http://vault.example/v1/local-health", "https://user@vault.example/v1/local-health", "https://vault.example/", "https://vault.example/v1/local-health?x=1", "https://192.0.2.1/v1/local-health", "https://[::1]/v1/local-health", "https://2130706433/v1/local-health", "https://vault.example:0/v1/local-health"):
            config["data"]["url"] = url
            with self.subTest(url=url), self.assertRaises(stack.ApplyBlockedError):
                lifecycle.verify_vault_probe_configuration(stack, self.artifact)
        config["data"]["url"] = "https://vault.example:7801/v1/local-health"
        for timeout in (None, True, 1, 4, "6"):
            container["readinessProbe"]["timeoutSeconds"] = timeout
            with self.subTest(timeout=timeout), self.assertRaises(stack.ApplyBlockedError):
                lifecycle.verify_vault_probe_configuration(stack, self.artifact)
        container["readinessProbe"]["timeoutSeconds"] = 6
        container["env"][0]["valueFrom"]["configMapKeyRef"]["optional"] = True
        with self.assertRaises(stack.ApplyBlockedError):
            lifecycle.verify_vault_probe_configuration(stack, self.artifact)

    def test_vault_runtime_contract_rejects_removed_profiles_and_inline_authority(self):
        lifecycle.verify_vault_runtime_contract(stack, self.artifact, self.summary)
        vault = next(r for r in self.artifact["resources"] if r["metadata"].get("name") == "vault-1")
        container = next(item for item in vault["spec"]["template"]["spec"]["containers"]
                         if item["name"] == "vault")
        mutations = (("KEROSENE_ENV", {"name": "KEROSENE_ENV", "value": "staging"}),
                     ("VAULT_TRANSPORT", {"name": "VAULT_TRANSPORT", "value": "clearnet"}),
                     ("VAULT_LISTEN_ADDR", {"name": "VAULT_LISTEN_ADDR", "value": "0.0.0.0:7801"}),
                     ("VAULT_SEED_PEERS", {"name": "VAULT_SEED_PEERS", "value": "inline.onion"}),
                     ("VAULT_MEASUREMENT_PIN", {"name": "VAULT_MEASUREMENT_PIN", "value": "short"}))
        for name, replacement in mutations:
            changed = copy.deepcopy(container["env"])
            index = next(i for i, entry in enumerate(changed) if entry["name"] == name)
            changed[index] = replacement
            container["env"] = changed
            with self.subTest(name=name), self.assertRaisesRegex(stack.ApplyBlockedError, "hardened production"):
                lifecycle.verify_vault_runtime_contract(stack, self.artifact, self.summary)
            container["env"] = copy.deepcopy(next(
                r for r in self.artifact["resources"] if r["metadata"].get("name") == "vault-2"
            )["spec"]["template"]["spec"]["containers"][1]["env"])
        vault["spec"]["template"]["spec"]["containers"] = [container]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "hardened production"):
            lifecycle.verify_vault_runtime_contract(stack, self.artifact, self.summary)

    def test_vault_runtime_contract_binds_member_tor_storage_and_mtls(self):
        lifecycle.verify_vault_runtime_contract(stack, self.artifact, self.summary)
        for mutation in ("shared-onion", "entrypoint-bypass", "mutable-torrc", "foreign-mtls"):
            changed = copy.deepcopy(self.artifact)
            vault = next(item for item in changed["resources"]
                         if item["kind"] == "Deployment" and item["metadata"]["name"] == "vault-1")
            pod = vault["spec"]["template"]["spec"]
            tor = next(item for item in pod["containers"] if item["name"] == "tor")
            if mutation == "shared-onion":
                volume = next(item for item in pod["volumes"] if item["name"] == "tor-identity")
                volume["secret"]["secretName"] = "shared-onion"
            elif mutation == "entrypoint-bypass":
                tor["command"] = ["tor"]
            elif mutation == "mutable-torrc":
                config = next(item for item in changed["resources"]
                              if item["kind"] == "ConfigMap" and item["metadata"]["name"] == "vault-1-tor")
                config["immutable"] = False
            else:
                volume = next(item for item in pod["volumes"] if item["name"] == "vault-mtls")
                volume["secret"]["secretName"] = "foreign-mtls"
            with self.subTest(mutation=mutation), \
                    self.assertRaisesRegex(stack.ApplyBlockedError, "hardened production"):
                lifecycle.verify_vault_runtime_contract(stack, changed, self.summary)

    def test_node_runtime_contract_requires_two_loopback_tor_planes(self):
        lifecycle.verify_node_runtime_contract(stack, self.artifact, self.summary)
        bank = next(r for r in self.artifact["resources"] if r["metadata"].get("name") == "node-bank")
        container = bank["spec"]["template"]["spec"]["containers"][0]
        listen = next(entry for entry in container["env"] if entry["name"] == "KEROSENE_NODE_LISTEN_ADDR")
        listen["value"] = "0.0.0.0:8800"
        with self.assertRaisesRegex(stack.ApplyBlockedError, "two-plane"):
            lifecycle.verify_node_runtime_contract(stack, self.artifact, self.summary)
        listen["value"] = "127.0.0.1:8800"
        container["readinessProbe"]["exec"]["command"] = ["sh", "-c", "exit 0"]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "two-plane"):
            lifecycle.verify_node_runtime_contract(stack, self.artifact, self.summary)
        container["readinessProbe"] = {"exec": {"command": ["/usr/local/bin/kerosene-node", "--health-probe"]}, "timeoutSeconds": 6}
        bank["spec"]["template"]["spec"]["containers"].pop()
        with self.assertRaisesRegex(stack.ApplyBlockedError, "two-plane"):
            lifecycle.verify_node_runtime_contract(stack, self.artifact, self.summary)

    def test_node_runtime_contract_requires_immutable_attested_state_files(self):
        lifecycle.verify_node_runtime_contract(stack, self.artifact, self.summary)
        changed = copy.deepcopy(self.artifact)
        state = next(r for r in changed["resources"] if r["metadata"].get("name") == "node-bank-state")
        state["immutable"] = False
        with self.assertRaisesRegex(stack.ApplyBlockedError, "two-plane"):
            lifecycle.verify_node_runtime_contract(stack, changed, self.summary)

    def test_node_runtime_contract_requires_immutable_plane_membership_file(self):
        lifecycle.verify_node_runtime_contract(stack, self.artifact, self.summary)
        for mutation in ("mutable", "wrong-plane", "directory-mount"):
            changed = copy.deepcopy(self.artifact)
            membership = next(r for r in changed["resources"]
                              if r["metadata"].get("name") == "node-bank-membership")
            bank = next(r for r in changed["resources"]
                        if r["metadata"].get("name") == "node-bank")
            node = bank["spec"]["template"]["spec"]["containers"][0]
            if mutation == "mutable":
                membership["immutable"] = False
            elif mutation == "wrong-plane":
                membership["data"]["manifest.json"] = json.dumps({
                    "network_id": self.release["network"]["id"], "plane": "vault"})
            else:
                mount = next(item for item in node["volumeMounts"]
                             if item["name"] == "initial-membership")
                mount.pop("subPath")
                mount["mountPath"] = "/etc/kerosene/node-membership"
            with self.subTest(mutation=mutation), \
                    self.assertRaisesRegex(stack.ApplyBlockedError, "two-plane"):
                lifecycle.verify_node_runtime_contract(stack, changed, self.summary)

    def test_node_runtime_contract_requires_external_identity_per_member(self):
        lifecycle.verify_node_runtime_contract(stack, self.artifact, self.summary)
        for mutation in ("shared-secret", "writable", "missing-key"):
            changed = copy.deepcopy(self.artifact)
            bank = next(r for r in changed["resources"]
                        if r["metadata"].get("name") == "node-bank")
            pod = bank["spec"]["template"]["spec"]
            node = pod["containers"][0]
            if mutation == "shared-secret":
                volume = next(item for item in pod["volumes"] if item["name"] == "node-identity")
                volume["secret"]["secretName"] = "shared-node-identity"
            elif mutation == "writable":
                mount = next(item for item in node["volumeMounts"] if item["name"] == "node-identity")
                mount["readOnly"] = False
            else:
                volume = next(item for item in pod["volumes"] if item["name"] == "node-identity")
                volume["secret"]["items"][0]["key"] = "other.key"
            with self.subTest(mutation=mutation), \
                    self.assertRaisesRegex(stack.ApplyBlockedError, "two-plane"):
                lifecycle.verify_node_runtime_contract(stack, changed, self.summary)

    def test_node_runtime_contract_requires_authorized_onion_identity(self):
        lifecycle.verify_node_runtime_contract(stack, self.artifact, self.summary)
        for mutation in ("shared-secret", "entrypoint-bypass", "unshared-state"):
            changed = copy.deepcopy(self.artifact)
            bank = next(r for r in changed["resources"]
                        if r["metadata"].get("name") == "node-bank")
            pod = bank["spec"]["template"]["spec"]
            tor = next(item for item in pod["containers"] if item["name"] == "tor")
            if mutation == "shared-secret":
                volume = next(item for item in pod["volumes"] if item["name"] == "tor-identity")
                volume["secret"]["secretName"] = "shared-onion-identity"
            elif mutation == "entrypoint-bypass":
                tor["command"] = ["tor"]
            else:
                mount = next(item for item in tor["volumeMounts"] if item["name"] == "identity-data")
                mount["name"] = "other-data"
            with self.subTest(mutation=mutation), \
                    self.assertRaisesRegex(stack.ApplyBlockedError, "two-plane"):
                lifecycle.verify_node_runtime_contract(stack, changed, self.summary)

    def test_node_runtime_contract_requires_genesis_mtls_and_tor_configuration(self):
        lifecycle.verify_node_runtime_contract(stack, self.artifact, self.summary)
        for mutation in ("mutable-genesis", "foreign-mtls", "changed-torrc"):
            changed = copy.deepcopy(self.artifact)
            bank = next(r for r in changed["resources"]
                        if r["metadata"].get("name") == "node-bank")
            pod = bank["spec"]["template"]["spec"]
            if mutation == "mutable-genesis":
                genesis = next(r for r in changed["resources"]
                               if r["metadata"].get("name") == "node-genesis" and
                               r["metadata"].get("namespace") == "kerosene-staging")
                genesis["immutable"] = False
            elif mutation == "foreign-mtls":
                volume = next(item for item in pod["volumes"] if item["name"] == "node-mtls")
                volume["secret"]["secretName"] = "shared-mtls"
            else:
                tor = next(r for r in changed["resources"]
                           if r["metadata"].get("name") == "node-bank-tor")
                tor["data"]["torrc"] += "SocksPort 9051\n"
            with self.subTest(mutation=mutation), \
                    self.assertRaisesRegex(stack.ApplyBlockedError, "two-plane"):
                lifecycle.verify_node_runtime_contract(stack, changed, self.summary)

        changed = copy.deepcopy(self.artifact)
        bank = next(r for r in changed["resources"] if r["metadata"].get("name") == "node-bank")
        node = bank["spec"]["template"]["spec"]["containers"][0]
        next(m for m in node["volumeMounts"] if m["name"] == "state-snapshot")["readOnly"] = False
        with self.assertRaisesRegex(stack.ApplyBlockedError, "two-plane"):
            lifecycle.verify_node_runtime_contract(stack, changed, self.summary)

        changed = copy.deepcopy(self.artifact)
        bank = next(r for r in changed["resources"] if r["metadata"].get("name") == "node-bank")
        node = bank["spec"]["template"]["spec"]["containers"][0]
        node["env"] = [entry for entry in node["env"]
                       if entry["name"] != "KEROSENE_STATE_SNAPSHOT_ATTESTATION_PATH"]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "two-plane"):
            lifecycle.verify_node_runtime_contract(stack, changed, self.summary)

    def test_node_runtime_contract_exposes_only_public_onion_hostname(self):
        lifecycle.verify_node_runtime_contract(stack, self.artifact, self.summary)

        changed = copy.deepcopy(self.artifact)
        bank = next(r for r in changed["resources"] if r["metadata"].get("name") == "node-bank")
        node = bank["spec"]["template"]["spec"]["containers"][0]
        onion_path = next(entry for entry in node["env"]
                          if entry["name"] == "KEROSENE_NODE_ONION_HOSTNAME_PATH")
        onion_path["value"] = "/var/lib/tor/node/hostname"
        with self.assertRaisesRegex(stack.ApplyBlockedError, "two-plane"):
            lifecycle.verify_node_runtime_contract(stack, changed, self.summary)

        changed = copy.deepcopy(self.artifact)
        bank = next(r for r in changed["resources"] if r["metadata"].get("name") == "node-bank")
        node = bank["spec"]["template"]["spec"]["containers"][0]
        next(m for m in node["volumeMounts"] if m["name"] == "onion-public")["readOnly"] = False
        with self.assertRaisesRegex(stack.ApplyBlockedError, "two-plane"):
            lifecycle.verify_node_runtime_contract(stack, changed, self.summary)

    def test_validator_roster_requires_independent_key_material(self):
        keys = [stack.TUF_ED25519_SPKI_PREFIX + bytes([index]) * 32 for index in range(4)]
        roster = {"schema": stack.ROSTER_SCHEMA, "networkId": self.summary["bft"]["networkId"],
                  "members": {f"member-{index}": base64.b64encode(key).decode() for index, key in enumerate(keys)}}
        path = self.root / "roster.json"
        path.write_text(json.dumps(roster))
        self.assertEqual(len(stack.validate_roster(str(path), self.summary)), 4)
        roster["members"]["member-3"] = roster["members"]["member-0"]
        path.write_text(json.dumps(roster))
        with self.assertRaisesRegex(stack.ReleaseValidationError, "distinct public keys"):
            stack.validate_roster(str(path), self.summary)

    def test_validator_roster_rejects_non_ed25519_or_noncanonical_keys(self):
        prefix = stack.TUF_ED25519_SPKI_PREFIX
        for key in [b"arbitrary", prefix + b"x" * 31, prefix + b"x" * 33, b"bad-prefix!!" + b"x" * 32]:
            roster = {"schema": stack.ROSTER_SCHEMA, "networkId": self.summary["bft"]["networkId"],
                      "members": {f"member-{index}": base64.b64encode(prefix + bytes([index]) * 32).decode() for index in range(4)}}
            roster["members"]["member-0"] = base64.b64encode(key).decode()
            self.path.write_text(json.dumps(roster))
            with self.subTest(key=key), self.assertRaisesRegex(stack.ReleaseValidationError, "canonical Ed25519"):
                stack.validate_roster(str(self.path), self.summary)

    def test_vault_compatibility_attestation_binds_all_sources_artifacts_and_threshold(self):
        now = dt.datetime.now(dt.timezone.utc)
        attestation = {"schema": stack.VAULT_COMPATIBILITY_SCHEMA, "releaseId": self.summary["releaseId"],
            "networkId": self.summary["networkId"], "sequence": self.summary["sequence"],
            "sourceBundleDigest": self.summary["sourceBundle"]["digest"],
            "repositoryCommits": self.summary["repositories"], "services": self.summary["services"],
            "migrationRecoveryEvidenceDigest": self.summary["migration"]["recoveryEvidenceDigest"],
            "rebuildEvidenceDigest": "sha256:" + "d" * 64, "sbomSetDigest": "sha256:" + "e" * 64,
            "provenanceSetDigest": "sha256:" + "f" * 64,
            "verifiedAt": (now - dt.timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "expiresAt": (now + dt.timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            "signatures": [{"synthetic": True}]}
        summary = copy.deepcopy(self.summary)
        summary["vaultCompatibility"]["attestationDigest"] = stack.canonical_digest(attestation)
        attestation_path = self.root / "vault-attestation.json"
        roster_path = self.root / "vault-roster.json"
        attestation_path.write_text(json.dumps(attestation))
        roster = {"schema": stack.VAULT_ROSTER_SCHEMA, "networkId": summary["networkId"],
                  "members": {f"vault-{index}": base64.b64encode(stack.TUF_ED25519_SPKI_PREFIX + bytes([index]) * 32).decode()
                              for index in range(summary["vaultCompatibility"]["members"])}}
        roster_path.write_text(json.dumps(roster))
        with patch.object(stack, "verify_signature_set", return_value=summary["vaultCompatibility"]["threshold"]) as verify:
            result = stack.verify_vault_compatibility_attestation(self.release, summary, str(attestation_path), str(roster_path))
        self.assertEqual(result["signaturesVerified"], summary["vaultCompatibility"]["threshold"])
        self.assertEqual(len(verify.call_args.args[2]), summary["vaultCompatibility"]["members"])
        changed = copy.deepcopy(attestation)
        changed["services"]["vault"]["image"] = changed["services"]["core"]["image"]
        attestation_path.write_text(json.dumps(changed))
        summary["vaultCompatibility"]["attestationDigest"] = stack.canonical_digest(changed)
        with self.assertRaises(stack.ReleaseValidationError):
            stack.verify_vault_compatibility_attestation(self.release, summary, str(attestation_path), str(roster_path))

    def test_vault_roster_requires_distinct_independent_keys(self):
        key = base64.b64encode(stack.TUF_ED25519_SPKI_PREFIX + b"x" * 32).decode()
        roster = {"schema": stack.VAULT_ROSTER_SCHEMA, "networkId": self.summary["networkId"],
                  "members": {f"vault-{index}": key for index in range(self.summary["vaultCompatibility"]["members"])}}
        self.path.write_text(json.dumps(roster))
        with self.assertRaisesRegex(stack.ReleaseValidationError, "distinct canonical"):
            stack.validate_vault_roster(str(self.path), self.summary)

    def test_signature_counter_cannot_count_aliases_of_one_key_as_quorum(self):
        key = stack.TUF_ED25519_SPKI_PREFIX + b"x" * 32
        with patch.object(stack, "verify_ed25519") as verify, self.assertRaisesRegex(stack.ReleaseValidationError, "duplicate public keys"):
            stack.verify_signature_set(b"unit", [], {"first": key, "second": key, "third": key}, 3, "unit signatures")
        verify.assert_not_called()

    def test_real_signature_from_one_key_cannot_be_relabelled_as_three_validators(self):
        private = self.root / "synthetic-test-key.pem"
        payload = self.root / "payload"
        signature = self.root / "signature"
        payload.write_bytes(b"synthetic quorum anti-alias test")
        subprocess.run(["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(private)], check=True, capture_output=True)
        key = subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-outform", "DER"], check=True, capture_output=True).stdout
        subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(private), "-in", str(payload), "-out", str(signature)], check=True, capture_output=True)
        record = {"memberId": "first", "publicKeyDerBase64": base64.b64encode(key).decode(),
                  "signatureBase64": base64.b64encode(signature.read_bytes()).decode()}
        self.assertEqual(stack.verify_signature_set(payload.read_bytes(), [record], {"first": key}, 1, "synthetic signatures"), 1)
        aliases = [{**record, "memberId": member} for member in ["first", "second", "third"]]
        with self.assertRaisesRegex(stack.ReleaseValidationError, "duplicate public keys"):
            stack.verify_signature_set(payload.read_bytes(), aliases, {member: key for member in ["first", "second", "third"]}, 3, "synthetic signatures")

    def test_initial_install_checks_both_bound_namespaces(self):
        replies = []
        for namespace in sorted(lifecycle.NAMESPACES):
            replies.extend([json.dumps({"kind": "Namespace", "metadata": {"name": namespace}}).encode(), b'{"items":[]}'])
        prefix = ["/kubectl", "--kubeconfig", "/cell.conf", "--context", "cell-a"]
        with patch.object(lifecycle, "run", side_effect=replies) as run:
            lifecycle.verify_empty_installation(stack, prefix)
            self.assertEqual(run.call_count, 4)
            for call in run.call_args_list:
                self.assertEqual(call.args[0][:5], prefix)

    def test_initial_install_accepts_absent_namespaces_without_creating_them(self):
        with patch.object(lifecycle, "run", return_value=b"") as run, patch.object(lifecycle, "apply_resource") as apply:
            lifecycle.verify_empty_installation(stack, ["/bound-kubectl"])
            self.assertEqual(run.call_count, 2)
            apply.assert_not_called()

    def test_initial_install_refuses_persisted_state_and_all_running_workloads(self):
        namespace = sorted(lifecycle.NAMESPACES)[0]
        for kind in ["Deployment", "StatefulSet", "Pod", "PersistentVolumeClaim"]:
            replies = [json.dumps({"kind": "Namespace", "metadata": {"name": namespace}}).encode(), json.dumps({"items": [{"kind": kind}]}).encode()]
            with self.subTest(kind=kind), patch.object(lifecycle, "run", side_effect=replies), self.assertRaisesRegex(stack.ApplyBlockedError, "existing workloads or persistent volumes"):
                lifecycle.verify_empty_installation(stack, ["/bound-kubectl"])

    def test_initial_install_does_not_treat_invalid_inventory_as_empty(self):
        namespace = sorted(lifecycle.NAMESPACES)[0]
        for inventory in [b"not-json", b"{}", b'{"items":null}', b'[]']:
            replies = [json.dumps({"kind": "Namespace", "metadata": {"name": namespace}}).encode(), inventory]
            with self.subTest(inventory=inventory), patch.object(lifecycle, "run", side_effect=replies), self.assertRaisesRegex(stack.ApplyBlockedError, "inventory is invalid"):
                lifecycle.verify_empty_installation(stack, ["/bound-kubectl"])

    def test_initial_admission_consumes_exact_cell_cluster_and_release(self):
        admission = {"cellId": "cell-a", "changeId": "change-a",
                     "clusterUid": "00000000-0000-0000-0000-000000000001",
                     "epoch": 1, "expiresAtUnixSeconds": 2000,
                     "issuedAtUnixSeconds": 1000, "networkId": "bank-a",
                     "nonce": "a" * 64, "operatorId": "operator-a",
                     "releaseApprovalDigest": "sha256:" + "b" * 64,
                     "schema": "kerosene.cell-admission/v1"}
        envelope = {"admission": admission, "signatures": []}
        admission_path = self.root / "admission.json"
        proof_path = self.root / "proof.json"
        admission_path.write_text(json.dumps(envelope))
        proof_path.write_text(json.dumps({"schema": "proof"}))
        summary = {"_canonicalDigest": "sha256:" + "c" * 64, "sequence": 3,
                   "bft": {"networkId": "bank-a", "epoch": 1}}
        args = SimpleNamespace(initial_admission=str(admission_path), admission_endpoint="https://bank.example:8443",
                               admission_ca="/ca", admission_cert="/cert", admission_key="/key",
                               consensus_proof=str(proof_path), operator_id="operator-a", change_id="change-a")
        consensus = {"schema": "kerosene.release-consensus-verification/v1",
                     "releaseLockCanonicalDigest": summary["_canonicalDigest"], "networkId": "bank-a",
                     "epoch": 1, "sequence": 3, "approvalDigest": "sha256:" + "d" * 64}
        response_body = lifecycle.canonical({"schema": "kerosene.cell-admission-verification/v1",
                                             "admissionDigest": "sha256:" + lifecycle.hashlib.sha256(lifecycle.canonical(admission)).hexdigest(),
                                             "consensus": consensus, "nonceConsumed": True, "installAuthorized": False})
        response = MagicMock()
        response.status = 200
        response.headers.get_content_type.return_value = "application/json"
        response.read.return_value = response_body
        response.__enter__.return_value = response
        tls = MagicMock()
        config = {"cellId": "cell-a", "cluster": {"systemNamespaceUid": admission["clusterUid"]}}
        opener = MagicMock()
        opener.open.return_value = response
        protected_read = lambda path, *_: Path(path).read_bytes() if Path(path).exists() else b"synthetic-pem"
        with patch.object(stack, "read_regular_file_bytes", side_effect=protected_read), \
                patch.object(lifecycle.ssl, "create_default_context", return_value=tls), \
                patch.object(lifecycle.urllib.request, "build_opener", return_value=opener):
            evidence = lifecycle.consume_initial_admission(stack, config, summary, args)
            recovered = lifecycle.consume_initial_admission(stack, config, summary, args, "inspect-recovery")
        self.assertTrue(evidence["nonceConsumed"])
        self.assertFalse(evidence["bankInstallAuthorized"])
        sent = json.loads(opener.open.call_args_list[0].args[0].data)
        self.assertEqual(sent["releaseLockCanonicalDigest"], summary["_canonicalDigest"])
        self.assertEqual(opener.open.call_args_list[0].args[0].full_url, "https://bank.example:8443/v1/cell/admissions/consume")
        self.assertEqual(recovered["operation"], "inspect-recovery")
        self.assertEqual(opener.open.call_args_list[1].args[0].full_url, "https://bank.example:8443/v1/cell/admissions/inspect-recovery")
        self.assertEqual(tls.load_cert_chain.call_count, 2)

    def test_initial_admission_recovery_requires_exact_prewrite_failure(self):
        digest = "sha256:" + "c" * 64
        summary = {"_canonicalDigest": digest, "sequence": 3}
        args = SimpleNamespace(environment="staging-cell", change_id="change-a", operator_id="operator-a",
                               resume_update_id=digest)
        state = {"schema": "kerosene.stack.update-state/v1", "updateId": digest, "sequence": 3,
                 "environment": "staging-cell", "changeId": "change-a", "operatorId": "operator-a",
                 "status": "failed", "phase": "failed", "manualRecoveryRequired": True,
                 "failure": "Bank initial admission failed or is uncertain; inspect recovery before retry",
                 "events": [{"phase": phase} for phase in ("snapshot-accepted", "rollout-started", "failed")]}
        evidence = lifecycle.verify_initial_admission_recovery_state(stack, state, summary, args)
        self.assertEqual(evidence["updateId"], digest)
        for phase in ("initial-admission-consumed", "admin-installed", "before:kerosene-staging/Deployment/core"):
            changed = copy.deepcopy(state)
            changed["events"].insert(-1, {"phase": phase})
            with self.subTest(phase=phase), self.assertRaises(stack.ApplyBlockedError):
                lifecycle.verify_initial_admission_recovery_state(stack, changed, summary, args)

    def test_postwrite_initial_recovery_requires_exact_failed_installation(self):
        digest = "sha256:" + "c" * 64
        summary = {"_canonicalDigest": digest, "sequence": 3}
        args = SimpleNamespace(environment="staging-cell", change_id="change-a", operator_id="operator-a",
                               resume_update_id=digest, recover_initial_install=False)
        state = {"schema": "kerosene.stack.update-state/v1", "updateId": digest, "sequence": 3,
                 "environment": "staging-cell", "changeId": "change-a", "operatorId": "operator-a",
                 "status": "failed", "phase": "failed", "manualRecoveryRequired": True,
                 "events": [{"phase": phase} for phase in
                            ("snapshot-accepted", "rollout-started", "initial-admission-consumed",
                             "admin-installed", "initial-database-migrations-validated", "failed")]}
        evidence = lifecycle.verify_postwrite_initial_install_recovery_state(stack, state, summary, args)
        self.assertEqual(evidence["priorAdmissionPhase"], "initial-admission-consumed")
        self.assertFalse(evidence["bankAdmissionReused"])
        ordinary = copy.deepcopy(state)
        ordinary["events"] = [{"phase": "snapshot-accepted"}, {"phase": "rollout-started"}, {"phase": "failed"}]
        self.assertIsNone(lifecycle.verify_postwrite_initial_install_recovery_state(stack, ordinary, summary, args))
        for mutation in ({"operatorId": "other"}, {"updateId": "sha256:" + "d" * 64}):
            changed = {**state, **mutation}
            with self.subTest(mutation=mutation), self.assertRaises(stack.ApplyBlockedError):
                lifecycle.verify_postwrite_initial_install_recovery_state(stack, changed, summary, args)
        duplicated = copy.deepcopy(state)
        duplicated["events"].insert(-1, {"phase": "initial-admission-retained"})
        with self.assertRaises(stack.ApplyBlockedError):
            lifecycle.verify_postwrite_initial_install_recovery_state(stack, duplicated, summary, args)

    def test_initial_admission_rejects_binding_mismatch_before_network(self):
        path = self.root / "admission.json"
        path.write_text(json.dumps({"admission": {"cellId": "wrong"}, "signatures": []}))
        proof = self.root / "proof.json"
        proof.write_text("{}")
        args = SimpleNamespace(initial_admission=str(path), admission_endpoint="https://bank.example",
                               admission_ca="/ca", admission_cert="/cert", admission_key="/key",
                               consensus_proof=str(proof), operator_id="operator-a", change_id="change-a")
        config = {"cellId": "cell-a", "cluster": {"systemNamespaceUid": "00000000-0000-0000-0000-000000000001"}}
        summary = {"_canonicalDigest": "sha256:" + "c" * 64, "sequence": 1,
                   "bft": {"networkId": "bank-a", "epoch": 1}}
        with patch.object(lifecycle.urllib.request, "build_opener") as request, self.assertRaises(stack.ApplyBlockedError):
            lifecycle.consume_initial_admission(stack, config, summary, args)
        request.assert_not_called()

    def test_install_rechecks_journal_under_lock_before_authorization(self):
        args = stack.build_parser().parse_args(["install", "--release", "unit.json", "--apply", "--state-dir", str(self.root)])
        for field in ["consensus_proof", "validator_roster", "bank_observer_report", "snapshot_attestation_request", "snapshot_receipt", "snapshot_provider_key"]:
            setattr(args, field, "unit-not-authority")
        stack.write_update_state(str(self.root), {"schema": "kerosene.stack.update-state/v1", "status": "committed"})
        summary = {"releaseSchemaVersion": 3, "sequence": 2}
        with patch.object(stack, "verify_tuf_authorization") as authorization, patch.object(stack, "run_staging_cell_deploy") as deploy:
            self.assertEqual(stack.apply_locked_release({}, summary, args), stack.EXIT_CANNOT_APPLY)
            authorization.assert_not_called()
            deploy.assert_not_called()
        self.assertEqual(stack.read_existing_update_state(str(self.root))["status"], "committed")

    def test_complete_cell_dependency_phases_independent_of_manifest_order(self):
        self.artifact["resources"].reverse()
        groups = lifecycle.workload_phases(stack, self.artifact, self.summary)
        self.assertEqual([{r["metadata"]["name"] for r in group} for group in groups],
                         [{"postgres", "redis", "tor", *self.node_names}, {"bitcoin"}, {"lnd"},
                          {"vault-1", "vault-2", "vault-3"}, {"core", "kfe"}, {"web-page"}])

    def test_multiple_vault_workloads_preserve_inventory(self):
        vault = next(r for r in self.artifact["resources"] if r["metadata"]["name"] == "vault-1")
        replica = copy.deepcopy(vault)
        replica["metadata"]["name"] = "vault-secondary"
        self.artifact["resources"].append(replica)
        groups = lifecycle.workload_phases(stack, self.artifact, self.summary)
        self.assertEqual([r["metadata"]["name"] for r in groups[3]],
                         ["vault-1", "vault-2", "vault-3", "vault-secondary"])

    def test_critical_topology_binds_independent_single_replica_storage(self):
        topology = lifecycle.critical_replica_topology(stack, self.artifact, self.summary)
        self.assertEqual([item["resource"]["metadata"]["name"] for item in topology["vault"]],
                         ["vault-1", "vault-2", "vault-3"])
        self.assertEqual(len({item["storage"] for members in topology.values() for item in members}), 9)
        vault_two = next(r for r in self.artifact["resources"] if r["metadata"].get("name") == "vault-2")
        vault_two["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] = "vault-1-data"
        with self.assertRaisesRegex(stack.ApplyBlockedError, "persistent identity is shared"):
            lifecycle.critical_replica_topology(stack, self.artifact, self.summary)

    def test_each_node_plane_requires_three_independent_controllers(self):
        for missing in ("node-bank-3", "node-vault-3"):
            changed = copy.deepcopy(self.artifact)
            changed["resources"] = [resource for resource in changed["resources"]
                                    if resource["metadata"].get("name") != missing]
            with self.subTest(missing=missing), self.assertRaisesRegex(
                    stack.ApplyBlockedError, "two-plane Tor/mTLS"):
                lifecycle.verify_node_runtime_contract(stack, changed, self.summary)
            with self.subTest(topology=missing), self.assertRaisesRegex(
                    stack.ApplyBlockedError, "exactly 6 independent Node"):
                lifecycle.critical_replica_topology(stack, changed, self.summary)

    def test_critical_topology_rejects_grouped_or_missing_vault_members(self):
        vault = next(r for r in self.artifact["resources"] if r["metadata"].get("name") == "vault-1")
        vault["spec"]["replicas"] = 2
        with self.assertRaisesRegex(stack.ApplyBlockedError, "one replica"):
            lifecycle.critical_replica_topology(stack, self.artifact, self.summary)
        vault["spec"]["replicas"] = 1
        self.artifact["resources"] = [r for r in self.artifact["resources"] if r["metadata"].get("name") != "vault-3"]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "exactly 3"):
            lifecycle.critical_replica_topology(stack, self.artifact, self.summary)

    def test_critical_apply_carries_uid_and_resource_version_preconditions(self):
        resource = next(r for r in self.artifact["resources"] if r["metadata"].get("name") == "vault-1")
        precondition = {"identity": list(lifecycle.identity(resource)), "uid": "vault-uid", "resourceVersion": "19"}
        with patch.object(lifecycle, "run") as run:
            lifecycle.apply_resource(["/bound/kubectl"], resource, False, precondition)
        submitted = json.loads(run.call_args.kwargs["input_bytes"])
        self.assertEqual(submitted["metadata"]["uid"], "vault-uid")
        self.assertEqual(submitted["metadata"]["resourceVersion"], "19")
        self.assertNotIn("uid", resource["metadata"])
        with self.assertRaisesRegex(RuntimeError, "precondition"):
            lifecycle.apply_resource(["/bound/kubectl"], resource, False,
                                     {**precondition, "identity": ["other", "Deployment", "vault-1"]})

    def test_critical_group_requires_every_member_stably_ready(self):
        members = lifecycle.critical_replica_topology(stack, self.artifact, self.summary)["vault"]
        live = []
        for index, member in enumerate(members, start=1):
            resource = copy.deepcopy(member["resource"])
            resource["metadata"].update(uid=f"vault-{index}-uid", resourceVersion=str(index), generation=2)
            resource["status"] = {"observedGeneration": 2, "readyReplicas": 1}
            live.append(json.dumps(resource).encode())
        with patch.object(lifecycle, "run", side_effect=live):
            observations = lifecycle.verify_critical_group_available(stack, ["/bound/kubectl"], "vault", members, 2)
        self.assertEqual(len(observations), 3)
        unavailable = json.loads(live[1])
        unavailable["status"]["readyReplicas"] = 0
        live[1] = json.dumps(unavailable).encode()
        with patch.object(lifecycle, "run", side_effect=live), self.assertRaisesRegex(stack.ApplyBlockedError, "not safe"):
            lifecycle.verify_critical_group_available(stack, ["/bound/kubectl"], "vault", members, 2)

    def test_complete_cell_acceptance_binds_every_component_and_safety_scenario(self):
        verifier = self.root / "acceptance-verifier"
        verifier.write_bytes(b"synthetic installed verifier")
        verifier.chmod(0o700)
        config = {"cellId": "cell-a", "cluster": {"systemNamespaceUid": "cluster-uid",
                  "kubeconfig": "/protected/kubeconfig", "context": "cell-a"},
                  "acceptanceVerifier": {"path": str(verifier),
                  "digest": "sha256:" + lifecycle.hashlib.sha256(verifier.read_bytes()).hexdigest()}}
        summary = copy.deepcopy(self.summary)
        summary["_canonicalDigest"] = "sha256:" + "b" * 64
        passed = {"passed": True, "evidenceDigest": "sha256:" + "a" * 64}
        report = {"schema": "kerosene.cell-acceptance/v1", "cellId": "cell-a", "clusterUid": "cluster-uid",
                  "releaseLockCanonicalDigest": summary["_canonicalDigest"], "sequence": summary["sequence"],
                  "changeId": "change-a", "operatorId": "operator-a",
                  "observedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                  "components": {name: dict(passed) for name in summary["services"]},
                  "scenarios": {name: dict(passed) for name in ("releaseObserved", "planReviewed",
                      "maintenanceDrained", "rolloutCompleted", "interruptionRecovered", "restoreQualified",
                      "nodeQuorumReady", "vaultQuorumReady", "operatorResumeGuarded")},
                  "financialReadinessVerified": True, "autoActivateVaultSigners": False,
                  "operatorResumeRequired": True, "operatorResumePerformed": False}
        process = SimpleNamespace(returncode=0, stdout=json.dumps(report).encode(), stderr=b"")
        args = SimpleNamespace(change_id="change-a", operator_id="operator-a")
        with patch.object(lifecycle.subprocess, "run", return_value=process) as run:
            result = lifecycle.verify_complete_cell_acceptance(stack, config, self.root, summary, args)
        self.assertTrue(result["financialReadinessVerified"])
        self.assertIn("--cluster-uid", run.call_args.args[0])
        report["components"].pop("admin")
        process.stdout = json.dumps(report).encode()
        with patch.object(lifecycle.subprocess, "run", return_value=process), self.assertRaisesRegex(stack.ApplyBlockedError, "coverage"):
            lifecycle.verify_complete_cell_acceptance(stack, config, self.root, summary, args)

    def test_canonical_node_tor_sidecar_topology_is_supported_in_both_planes(self):
        node = next(r for r in self.artifact["resources"] if r["metadata"]["name"] == "node-bank")
        tor = next(r for r in self.artifact["resources"] if r["metadata"]["name"] == "tor")
        tor["spec"]["template"]["spec"]["containers"].extend(node["spec"]["template"]["spec"]["containers"])
        self.artifact["resources"].remove(node)
        second = copy.deepcopy(tor)
        second["metadata"].update(name="vault-tor", namespace="kerosene-staging-vault")
        self.artifact["resources"].append(second)
        first = lifecycle.workload_phases(stack, self.artifact, self.summary)[0]
        self.assertEqual({r["metadata"]["name"] for r in first},
                         {"postgres", "redis", "tor", "vault-tor", *self.node_names - {"node-bank"}})

    def test_cross_phase_colocation_is_rejected_not_misordered(self):
        bitcoin = next(r for r in self.artifact["resources"] if r["metadata"]["name"] == "bitcoin")
        bitcoin["spec"]["template"]["spec"]["initContainers"] = [{"name": "lnd", "image": self.summary["services"]["lnd"]["image"]}]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "dependency phases"):
            lifecycle.workload_phases(stack, self.artifact, self.summary)

    def test_operator_cli_is_not_a_daemon(self):
        resource = next(r for r in self.artifact["resources"] if r["kind"] in lifecycle.WORKLOADS)
        resource["spec"]["template"]["spec"]["containers"][0]["image"] = self.summary["services"]["admin"]["image"]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "Admin CLI"):
            lifecycle.workload_phases(stack, self.artifact, self.summary)

    def test_init_container_does_not_satisfy_service_inventory(self):
        for node in (r for r in self.artifact["resources"] if r["metadata"]["name"] in self.node_names):
            pod = node["spec"]["template"]["spec"]
            pod["initContainers"] = pod["containers"]
            pod["containers"] = [{"name": "tor", "image": self.summary["services"]["tor"]["image"]}]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "missing runtime components: node"):
            lifecycle.workload_phases(stack, self.artifact, self.summary)

    def test_missing_or_ambiguous_component_identity_blocks(self):
        changed = copy.deepcopy(self.artifact)
        changed["resources"] = [r for r in changed["resources"] if r["metadata"]["name"] not in self.node_names]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "missing runtime components: node"):
            lifecycle.workload_phases(stack, changed, self.summary)
        summary = copy.deepcopy(self.summary)
        summary["services"]["node"]["image"] = summary["services"]["vault"]["image"]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "unique approved component"):
            lifecycle.workload_phases(stack, self.artifact, summary)

    def test_application_phase_is_submitted_before_readiness_waits(self):
        from types import SimpleNamespace
        events = []
        checkpoints = []
        maintenance_evidence = {"changeId": "change-update", "safeToUpdate": True}
        # Test orchestration only: capabilities and readiness are mocked. This
        # deliberately provides no qualification for live apply or quorum.
        config = {"cellId": "unit", "cluster": {"kubeconfig": "/protected/cell.conf", "context": "cell-a"}}
        with patch.object(lifecycle, "require_execution_capabilities"), patch.object(lifecycle, "load_config", return_value=config), patch.object(lifecycle, "verify_bootstrap_trust"), patch.object(lifecycle, "kubectl_command", return_value=["/bound-kubectl"]) as binding, patch.object(lifecycle, "verify_external_secrets"), patch.object(lifecycle, "verify_managed_configmaps", return_value=[]), patch.object(lifecycle, "verify_maintenance", return_value=maintenance_evidence), patch.object(lifecycle.admin_install, "install", return_value={}), patch.object(lifecycle, "verify_complete_cell_acceptance", return_value={}), patch.object(lifecycle, "verify_critical_group_available", side_effect=self.critical_observations) as critical_ready, patch.object(lifecycle, "apply_resource", side_effect=lambda cmd, r, dry, *precondition: events.append(("apply", r["metadata"]["name"]))), patch.object(lifecycle, "verify_running", side_effect=lambda cmd, r: events.append(("ready", r["metadata"]["name"])) or []), patch.object(lifecycle, "run") as run, patch.dict(lifecycle.os.environ, {}, clear=True):
            lifecycle.execute(stack, self.artifact, self.summary,
                              SimpleNamespace(dry_run=False, cell_dir="unit-only", _release={},
                                              change_id="change-update", operator_id="operator-update"),
                              lambda phase, details: checkpoints.append((phase, details)))
            self.assertEqual(binding.call_count, 2)
            smokes = [call.args[0] for call in run.call_args_list if call.args[0][0] == "bash"]
            self.assertEqual(len(smokes), 2)
            for command in smokes:
                self.assertEqual(command[-4:], ["--cell-binding", "/bound-kubectl", "/protected/cell.conf", "cell-a"])
            self.assertEqual(critical_ready.call_count, 18)
            self.assertEqual([call.args[4] for call in critical_ready.call_args_list], [6] * 12 + [2] * 6)
        self.assertIn(("kfe-maintenance-verified", maintenance_evidence), checkpoints)
        for name in ["core", "kfe"]:
            for consumer in ["core", "kfe"]:
                self.assertLess(events.index(("apply", name)), events.index(("ready", consumer)))
        self.assertLess(events.index(("ready", "bitcoin")), events.index(("apply", "lnd")))
        self.assertLess(events.index(("ready", "vault-3")), events.index(("apply", "core")))
        for first, second in (("vault-1", "vault-2"), ("vault-2", "vault-3")):
            self.assertLess(events.index(("apply", first)), events.index(("ready", first)))
            self.assertLess(events.index(("ready", first)), events.index(("apply", second)))

    def test_invalid_phase_blocks_before_any_kubernetes_write(self):
        from types import SimpleNamespace
        self.artifact["resources"] = [r for r in self.artifact["resources"] if r["metadata"]["name"] not in self.node_names]
        with patch.object(lifecycle, "load_config", return_value={}), patch.object(lifecycle, "verify_bootstrap_trust"), patch.object(lifecycle, "kubectl_command", return_value=["bound-kubectl"]), patch.object(lifecycle, "verify_external_secrets"), patch.object(lifecycle, "apply_resource") as apply, patch.dict(lifecycle.os.environ, {}, clear=True):
            with self.assertRaisesRegex(stack.ApplyBlockedError, "missing runtime components"):
                lifecycle.execute(stack, self.artifact, self.summary, SimpleNamespace(dry_run=True, cell_dir="unit-only"), lambda *_: None)
            apply.assert_not_called()

    def test_configmap_change_stops_before_next_runtime_phase(self):
        from types import SimpleNamespace
        baseline = [{"namespace": "kerosene-staging", "name": "config", "uid": "first", "contentDigest": "sha256:" + "a" * 64}]
        for mutation in ("uid", "contentDigest"):
            for completed_phases in (0, 1):
                changed = copy.deepcopy(baseline)
                changed[0][mutation] = "changed"
                observations = [baseline] * (1 + completed_phases) + [changed]
                applied = []
                config = {"cellId": "unit", "cluster": {"kubeconfig": "/protected/config", "context": "unit"}}
                with self.subTest(mutation=mutation, completed_phases=completed_phases), \
                     patch.object(lifecycle, "require_execution_capabilities"), \
                     patch.object(lifecycle, "load_config", return_value=config), \
                     patch.object(lifecycle, "verify_bootstrap_trust"), \
                     patch.object(lifecycle, "kubectl_command", return_value=["/bound/kubectl"]), \
                     patch.object(lifecycle, "verify_external_secrets"), \
                     patch.object(lifecycle.admin_install, "install", return_value={}), \
                     patch.object(lifecycle, "verify_managed_configmaps", side_effect=observations), \
                     patch.object(lifecycle, "verify_maintenance"), \
                     patch.object(lifecycle, "verify_critical_group_available", side_effect=self.critical_observations), \
                     patch.object(lifecycle, "verify_running", return_value=[]), \
                     patch.object(lifecycle, "run"), \
                     patch.object(lifecycle, "apply_resource", side_effect=lambda command, resource, dry, *precondition: applied.append(resource)), \
                     patch.dict(lifecycle.os.environ, {}, clear=True), \
                     self.assertRaisesRegex(stack.ApplyBlockedError, "before the next Cell phase"):
                    lifecycle.execute(stack, self.artifact, self.summary, SimpleNamespace(dry_run=False, cell_dir="unit-only", _release={}), lambda *_: None)
                runtime_names = {r["metadata"]["name"] for r in applied if r["kind"] in lifecycle.WORKLOADS}
                self.assertEqual(runtime_names, set() if completed_phases == 0 else
                                 {"postgres", "redis", "tor", *self.node_names})

    def test_initial_install_cannot_fall_through_to_update_maintenance(self):
        from types import SimpleNamespace
        with patch.object(lifecycle, "require_execution_capabilities"), \
             patch.object(lifecycle, "load_config", return_value={"cellId": "unit"}), \
             patch.object(lifecycle, "verify_bootstrap_trust"), \
             patch.object(lifecycle, "kubectl_command", return_value=["/bound-kubectl"]), \
             patch.object(lifecycle, "verify_empty_installation"), \
             patch.object(lifecycle, "initial_database_plan", return_value={"mode": "initial"}), \
             patch.object(lifecycle, "verify_external_secrets"), \
             patch.object(lifecycle, "required_secret_references", return_value=[]), \
             patch.object(lifecycle.admin_install, "install") as admin, \
             patch.object(lifecycle, "apply_resource") as apply, \
             patch.object(lifecycle, "verify_maintenance") as maintenance, \
             patch.dict(lifecycle.os.environ, {}, clear=True):
            with self.assertRaisesRegex(stack.ApplyBlockedError, "Bank initial admission"):
                lifecycle.execute(stack, self.artifact, self.summary,
                    SimpleNamespace(command="install", dry_run=False, cell_dir="unit-only"), lambda *_: None)
            admin.assert_not_called()
            apply.assert_not_called()
            maintenance.assert_not_called()

    def test_postwrite_initial_recovery_retains_admission_and_recovers_migrations(self):
        summary = copy.deepcopy(self.summary)
        summary["_canonicalDigest"] = "sha256:" + "c" * 64
        evidence = {"updateId": summary["_canonicalDigest"], "bankAdmissionReused": False}
        args = SimpleNamespace(command="recover", dry_run=False, cell_dir="unit-only", _release={},
                               change_id="change-recover", operator_id="operator-recover",
                               _postwrite_initial_recovery=True,
                               _postwrite_initial_recovery_evidence=evidence)
        checkpoints = []
        config = {"cellId": "unit", "cluster": {"kubeconfig": "/protected/cell.conf", "context": "cell-a"}}
        baseline = []
        with patch.object(lifecycle, "require_execution_capabilities"), \
             patch.object(lifecycle, "load_config", return_value=config), \
             patch.object(lifecycle, "verify_bootstrap_trust"), \
             patch.object(lifecycle, "kubectl_command", return_value=["/bound-kubectl"]), \
             patch.object(lifecycle, "verify_empty_installation") as empty, \
             patch.object(lifecycle, "initial_database_plan", return_value={"mode": "initial"}), \
             patch.object(lifecycle, "verify_external_secrets"), \
             patch.object(lifecycle, "required_secret_references", return_value=[]), \
             patch.object(lifecycle, "consume_initial_admission") as admission, \
             patch.object(lifecycle.admin_install, "install", return_value={}), \
             patch.object(lifecycle, "verify_managed_configmaps", return_value=baseline), \
             patch.object(lifecycle, "apply_resource"), \
             patch.object(lifecycle, "verify_running", return_value=[]), \
             patch.object(lifecycle, "execute_initial_database_migrations",
                          side_effect=stack.ApplyBlockedError("stop-after-argument-check")) as migrations, \
             patch.object(lifecycle, "verify_maintenance") as maintenance, \
             patch.object(lifecycle, "run"), \
             patch.dict(lifecycle.os.environ, {}, clear=True), \
             self.assertRaisesRegex(stack.ApplyBlockedError, "stop-after-argument-check"):
            lifecycle.execute(stack, self.artifact, summary, args,
                              lambda phase, details: checkpoints.append((phase, details)))
        empty.assert_not_called()
        admission.assert_not_called()
        maintenance.assert_not_called()
        self.assertIn(("initial-admission-retained", evidence), checkpoints)
        self.assertIn(("initial-install-no-prior-traffic",
                       {"changeId": "change-recover", "operation": "recover"}), checkpoints)
        self.assertIn(("release-plan-verified", {"releaseLockCanonicalDigest": summary["_canonicalDigest"],
                       "sequence": summary["sequence"], "changeId": "change-recover",
                       "operatorId": "operator-recover", "operation": "recover"}), checkpoints)
        self.assertTrue(migrations.call_args.kwargs["recover_existing"])

    def test_smoke_overrides_block_before_any_resource_write(self):
        from types import SimpleNamespace
        for variable in ["KEROSENE_STAGING_NAMESPACE", "KEROSENE_STAGING_VAULT_NAMESPACE",
                         "KEROSENE_STAGING_LOGIN_PORT", "KEROSENE_STAGING_VAULT_SMOKE_PORT"]:
            with self.subTest(variable=variable), patch.object(lifecycle, "load_config", return_value={}), patch.object(lifecycle, "verify_bootstrap_trust"), patch.object(lifecycle, "kubectl_command", return_value=["/bound-kubectl"]), patch.object(lifecycle, "apply_resource") as apply, patch.dict(lifecycle.os.environ, {variable: "unapproved"}, clear=True):
                with self.assertRaisesRegex(stack.ApplyBlockedError, "override environment"):
                    lifecycle.execute(stack, self.artifact, self.summary, SimpleNamespace(dry_run=True, cell_dir="unit-only"), lambda *_: None)
                apply.assert_not_called()

    def test_missing_external_secret_blocks_before_admin_install_or_resource_apply(self):
        from types import SimpleNamespace
        with patch.object(lifecycle, "require_execution_capabilities"), patch.object(lifecycle, "load_config", return_value={"cellId": "unit"}), patch.object(lifecycle, "verify_bootstrap_trust"), patch.object(lifecycle, "kubectl_command", return_value=["/bound-kubectl"]), patch.object(lifecycle, "verify_external_secrets", side_effect=stack.ApplyBlockedError("required external Secret unavailable")), patch.object(lifecycle, "apply_resource") as apply, patch.object(lifecycle.admin_install, "install") as admin, patch.dict(lifecycle.os.environ, {}, clear=True):
            with self.assertRaisesRegex(stack.ApplyBlockedError, "external Secret unavailable"):
                lifecycle.execute(stack, self.artifact, self.summary, SimpleNamespace(dry_run=False, cell_dir="unit-only"), lambda *_: None)
            apply.assert_not_called()
            admin.assert_not_called()

    def test_v3_matches_actual_static_consensus_capabilities_and_integer_range(self):
        release = copy.deepcopy(self.release)
        release.update(schema="kerosene.release-lock/v3", schemaVersion=3)
        release["authorization"]["bft"] = {"networkId": "bank-governance", "epoch": 1, "members": 4, "threshold": 3}
        self.assertEqual(stack.validate_release(release)["releaseSchemaVersion"], 3)
        release["authorization"]["bft"].update(members=5, threshold=4)
        with self.assertRaisesRegex(stack.ReleaseValidationError, "static four-member"):
            stack.validate_release(release)
        release["authorization"]["bft"].update(members=4, threshold=3)
        release["sequence"] = 9007199254740992
        with self.assertRaisesRegex(stack.ReleaseValidationError, "interoperable JSON"):
            stack.validate_release(release)

    def test_changes_to_shared_input_block_every_service(self):
        changed = copy.deepcopy(self.artifact)
        changed["resources"][0]["metadata"]["labels"] = {"changed": "true"}
        with self.assertRaisesRegex(stack.ApplyBlockedError, "configuration digest mismatch"):
            self.verify(changed)

    def test_extra_runtime_image_rejected(self):
        changed = copy.deepcopy(self.artifact)
        resource = next(r for r in changed["resources"] if r["kind"] in lifecycle.WORKLOADS)
        resource["spec"]["template"]["spec"]["initContainers"] = [{"name": "injected", "image": "evil.invalid/untrusted:latest"}]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "not an approved"):
            self.verify(changed)

    def test_inline_secret_and_foreign_namespace_rejected(self):
        changed = copy.deepcopy(self.artifact)
        changed["resources"].append({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "forbidden", "namespace": "kerosene-staging"}, "data": {"value": "encoded"}})
        with self.assertRaisesRegex(stack.ApplyBlockedError, "cannot create Secrets"):
            self.verify(changed)
        changed = copy.deepcopy(self.artifact)
        namespaced = next(r for r in changed["resources"] if r["kind"] != "Namespace")
        namespaced["metadata"]["namespace"] = "other-bank"
        with self.assertRaisesRegex(stack.ApplyBlockedError, "Cell namespace"):
            self.verify(changed)

    def test_privilege_and_admin_tamper(self):
        changed = copy.deepcopy(self.artifact)
        resource = next(r for r in changed["resources"] if r["kind"] in lifecycle.WORKLOADS)
        resource["spec"]["template"]["spec"]["hostNetwork"] = True
        with self.assertRaisesRegex(stack.ApplyBlockedError, "host namespaces"):
            self.verify(changed)
        changed = copy.deepcopy(self.artifact)
        changed["admin"]["image"] = self.release["services"]["core"]["image"]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "Admin artifact"):
            self.verify(changed)

    def test_fifo_evidence_fails_without_blocking(self):
        import os
        os.mkfifo(self.root / "evidence")
        with self.assertRaisesRegex(stack.ReleaseValidationError, "regular file"):
            stack.read_regular_file_bytes(self.root / "evidence", "FIFO", 100)

    def test_hpa_blocks_before_any_apply(self):
        changed = copy.deepcopy(self.artifact)
        changed["resources"].append({"apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler", "metadata": {"name": "auto", "namespace": "kerosene-staging"}, "spec": {"minReplicas": 1, "maxReplicas": 4}})
        with patch.object(lifecycle, "apply_resource") as apply:
            with self.assertRaisesRegex(stack.ApplyBlockedError, "HPA requires"):
                self.verify(changed)
            apply.assert_not_called()

    def test_cluster_binding_never_uses_the_active_context(self):
        config = {"cluster": {"kubeconfig": "/protected/cell.conf", "context": "cell-a", "systemNamespaceUid": "expected-uid"}}
        with patch.object(lifecycle.shutil, "which", return_value="/usr/bin/kubectl"), patch.object(lifecycle, "run", return_value=b'{"metadata":{"uid":"expected-uid"}}') as run:
            command = lifecycle.kubectl_command(stack, config)
            self.assertEqual(command[:5], ["/usr/bin/kubectl", "--kubeconfig", "/protected/cell.conf", "--context", "cell-a"])
            self.assertEqual(run.call_args.args[0][:5], command[:5])
        with patch.object(lifecycle.shutil, "which", return_value="/usr/bin/kubectl"), patch.object(lifecycle, "run", return_value=b'{"metadata":{"uid":"foreign-uid"}}'):
            with self.assertRaisesRegex(stack.ApplyBlockedError, "identity differs"):
                lifecycle.kubectl_command(stack, config)
        with self.assertRaisesRegex(stack.ApplyBlockedError, "explicit Kubernetes"):
            lifecycle.kubectl_command(stack, {})

    def test_single_protected_bootstrap_directory_initializes_complete_cell_binding(self):
        bootstrap = self.root / "bootstrap"
        bootstrap.mkdir(mode=0o700)
        files = {"cell-id": b"cell-a\n", "kube-context": b"staging-cell-a\n", "kubeconfig": b"synthetic-kubeconfig",
                 "tuf-root.json": b"{}", "validator-roster.json": b"{}", "vault-roster.json": b"{}",
                 "snapshot-provider.pub": b"synthetic-public-key", "consensus-anchor.json": b"{}",
                 "kerosene-release-consensus": b"#!/bin/sh\nexit 1\n",
                 "kerosene-cell-acceptance": b"#!/bin/sh\nexit 1\n"}
        for name, raw in files.items():
            path = bootstrap / name
            path.write_bytes(raw)
            path.chmod(0o700 if name.startswith("kerosene-") else 0o600)
        cell = self.root / "cell-a"
        args = stack.build_parser().parse_args(["init", "--cell-dir", str(cell), "--bootstrap-dir", str(bootstrap)])
        with patch.object(stack, "parse_tuf_root"), patch.object(lifecycle, "run", return_value=b'{"metadata":{"uid":"cluster-uid"}}'), patch("builtins.print"):
            lifecycle.command_init(stack, args)
        config = stack.read_json_document(str(cell / "cell.json"), "Cell configuration")
        self.assertEqual(config["cellId"], "cell-a")
        self.assertEqual(config["cluster"]["context"], "staging-cell-a")
        self.assertEqual(config["cluster"]["systemNamespaceUid"], "cluster-uid")
        self.assertEqual(config["acceptanceVerifier"]["path"], str(bootstrap / "kerosene-cell-acceptance"))
        self.assertEqual(config["consensusVerifier"]["path"], str(bootstrap / "kerosene-release-consensus"))
        self.assertFalse(config["autoActivateVaultSigners"])
        mixed = stack.build_parser().parse_args(["init", "--cell-dir", str(self.root / "other"),
                                                 "--bootstrap-dir", str(bootstrap), "--cell-id", "other"])
        with self.assertRaisesRegex(stack.ReleaseValidationError, "cannot be mixed"):
            lifecycle.resolve_bootstrap_directory(stack, mixed)
        bootstrap.chmod(0o755)
        unsafe = stack.build_parser().parse_args(["init", "--cell-dir", str(self.root / "unsafe"),
                                                  "--bootstrap-dir", str(bootstrap)])
        with self.assertRaisesRegex(stack.ReleaseValidationError, "owner-only"):
            lifecycle.resolve_bootstrap_directory(stack, unsafe)

    def test_single_operation_directory_expands_private_references_without_reading_secrets(self):
        operation = self.root / "operation"
        operation.mkdir(mode=0o700)
        text = {"confirm-release": "release-a", "change-id": "change-a", "operator-id": "operator-a",
                "admission-endpoint": "https://bank.invalid", "maintenance-endpoint": "https://kfe.invalid/api/admin/kfe/maintenance/status"}
        private = ("admission-ca.pem", "admission-cert.pem", "admission-key.pem", "maintenance-ca.pem",
                   "maintenance-cert.pem", "maintenance-key.pem", "maintenance-token", "recovery-evidence.json")
        for name, value in text.items():
            (operation / name).write_text(value + "\n")
            (operation / name).chmod(0o600)
        for name in private:
            (operation / name).write_text("private-reference-content")
            (operation / name).chmod(0o600)
        args = stack.build_parser().parse_args(["update", "--operation-dir", str(operation), "--release", "unit.json"])
        lifecycle.resolve_operation_directory(stack, args)
        self.assertEqual(args.change_id, "change-a")
        self.assertEqual(args.operator_id, "operator-a")
        self.assertEqual(args.confirm_release, "release-a")
        self.assertEqual(args.maintenance_token_file, str(operation / "maintenance-token"))
        self.assertNotIn("private-reference-content", vars(args).values())
        mixed = stack.build_parser().parse_args(["update", "--operation-dir", str(operation), "--release", "unit.json",
                                                 "--change-id", "different"])
        with self.assertRaisesRegex(stack.ReleaseValidationError, "cannot be mixed"):
            lifecycle.resolve_operation_directory(stack, mixed)

    def test_absent_safety_capabilities_cannot_be_replaced_by_evidence(self):
        from types import SimpleNamespace
        with patch.object(lifecycle, "run") as run:
            with self.assertRaisesRegex(stack.ApplyBlockedError, "not qualified"):
                lifecycle.execute(stack, self.artifact, self.summary, SimpleNamespace(dry_run=False), lambda *_: None)
            run.assert_not_called()

    def test_preserves_delegated_role_highwater(self):
        state = self.root / "state"
        stack.prepare_state_dir(str(state))
        stack.persist_tuf_state(str(state), {"metadataVersions": {"targets": 7, "delegated": 9}, "metadataDigests": {"targets": "sha256:" + "a" * 64, "delegated": "sha256:" + "b" * 64}})
        stack.persist_tuf_state(str(state), {"metadataVersions": {"targets": 8}, "metadataDigests": {"targets": "sha256:" + "c" * 64}})
        self.assertEqual(stack.read_tuf_state(str(state))["delegated"], 9)
        with self.assertRaisesRegex(stack.ReleaseValidationError, "equivocation"):
            stack.persist_tuf_state(str(state), {"metadataVersions": {"targets": 8}, "metadataDigests": {"targets": "sha256:" + "d" * 64}})

    def test_tuf_pins_verified_document_without_rereading_an_untrusted_path(self):
        signed = {"version": 7, "targets": {"approved.json": {}}}
        role = {"document": {"signed": signed, "signatures": []}, "name": "targets", "signed": signed, "targets": signed["targets"]}
        digests = {}
        # Unit test of file-read ordering, not a substitute for signature tests.
        with patch.object(stack, "verify_tuf_role"), patch.object(stack, "verify_tuf_target_binding"), patch.object(stack, "read_tuf_metadata_file") as read:
            result = stack.resolve_tuf_target(self.root, {}, role, [], 1, {}, "approved.json", {}, b"", {}, {}, set(), digests=digests)
            self.assertEqual(result, "targets")
            self.assertEqual(digests["targets"], stack.canonical_digest(signed))
            read.assert_not_called()

    def test_update_notice_distinguishes_available_evidence_from_execution_and_interruption(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from types import SimpleNamespace
        summary = copy.deepcopy(self.summary)
        summary["_releaseBytes"] = b"unit-only"
        args = SimpleNamespace(release="unit.json", bft_receipt="receipt.json", validator_roster="roster.json",
                               vault_roster="vault-roster.json", vault_compatibility_attestation="vault.json",
                               bank_observer_report="bank.json", state_dir="unit-state", environment="staging-cell", json=True)
        # Notice routing unit test; crypto and real consensus are tested separately.
        with patch.object(stack, "load_and_validate", return_value=(self.release, summary)), patch.object(stack, "verify_tuf_authorization", return_value={"signatureVerified": True}), patch.object(stack, "verify_consensus_authorization", return_value={"signaturesVerified": 3}), patch.object(stack, "verify_vault_compatibility_attestation", return_value={"signaturesVerified": 2}), patch.object(stack, "verify_bank_observer_report", return_value={"compatibleObservers": 3}), patch.object(stack, "read_existing_update_state") as state:
            state.return_value = None
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(stack.command_check_update(args), 0)
            notice = json.loads(output.getvalue())
            self.assertTrue(notice["updateRequired"])
            self.assertFalse(notice["applyQualified"])
            self.assertFalse(notice["orderedConsensusVerified"])
            self.assertEqual(notice["nextAction"], "review-update-plan-execution-not-qualified")
            state.return_value = {"schema": "kerosene.stack.update-state/v1", "environment": "staging-cell", "status": "rollout-started", "sequence": summary["sequence"]}
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(stack.command_check_update(args), 0)
            self.assertEqual(json.loads(output.getvalue())["nextAction"], "manual-recovery-required")
            state.return_value.update(status="committed", updateId="sha256:" + "f" * 64)
            with redirect_stdout(StringIO()):
                self.assertEqual(stack.command_check_update(args), stack.EXIT_CANNOT_APPLY)

    def test_maintenance_requires_actual_phase_revision_change_and_explicit_coverage(self):
        now = dt.datetime.now(dt.timezone.utc)
        status = {"schema": "kerosene.kfe-maintenance/v1", "mode": "DRAINING", "changeId": "change-one", "revision": 1,
                  "observedAt": now.isoformat(), "safeToUpdate": True,
                  "blockers": {"mutationCoverageUnknown": 0, "callbackCoverageUnknown": 0, "readSideEffectsUnknown": 0}}
        # Validator unit fixture; the actual KFE still has conservative coverage blockers.
        self.assertEqual(lifecycle.validate_maintenance(stack, status, "change-one", now), status)
        changes = [{"mode": "ACTIVE"}, {"mode": "DRAINED"}, {"changeId": "other"}, {"revision": 0},
                   {"revision": True}, {"revision": 9007199254740992}, {"blockers": {}},
                   {"blockers": {"mutationCoverageUnknown": 0, "callbackCoverageUnknown": 0}},
                   {"observedAt": (now - dt.timedelta(seconds=31)).isoformat()}]
        for changeset in changes:
            with self.subTest(changeset=changeset), self.assertRaises((stack.ApplyBlockedError, stack.ReleaseValidationError)):
                lifecycle.validate_maintenance(stack, {**status, **changeset}, "change-one", now)
        for invalid in [1, False, -1, 0.0]:
            changed = copy.deepcopy(status)
            changed["blockers"]["callbackCoverageUnknown"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(stack.ApplyBlockedError):
                lifecycle.validate_maintenance(stack, changed, "change-one", now)


class ManagedConfigurationTest(unittest.TestCase):
    def fixture(self):
        resource = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"namespace": "kerosene-staging", "name": "config"},
            "data": {"setting": "approved"}, "binaryData": {"blob": "YQ=="}}
        live = copy.deepcopy(resource)
        live["metadata"]["uid"] = "config-uid"
        return {"resources": [resource]}, live

    def test_exact_content_records_identity_and_digest_without_values(self):
        artifact, live = self.fixture()
        with patch.object(lifecycle, "run", return_value=json.dumps(live).encode()) as run:
            records = lifecycle.verify_managed_configmaps(stack, ["/bound/kubectl"], artifact)
        self.assertEqual(records[0]["uid"], "config-uid")
        self.assertNotIn("approved", json.dumps(records))
        run.assert_called_once_with(["/bound/kubectl", "-n", "kerosene-staging", "get", "configmap", "config", "-o", "json"])

    def test_modified_text_binary_or_identity_blocks_without_echoing_values(self):
        for mutation in ("data", "binaryData", "name", "uid", "deletion"):
            artifact, live = self.fixture()
            if mutation in ("data", "binaryData"): live[mutation] = {"private-marker": "private-value"}
            elif mutation == "uid": live["metadata"].pop("uid")
            elif mutation == "deletion": live["metadata"]["deletionTimestamp"] = "now"
            else: live["metadata"]["name"] = "foreign"
            with self.subTest(mutation=mutation), patch.object(lifecycle, "run", return_value=json.dumps(live).encode()), self.assertRaises(stack.ApplyBlockedError) as error:
                lifecycle.verify_managed_configmaps(stack, ["/bound/kubectl"], artifact)
            self.assertNotIn("private", str(error.exception))

    def test_secrets_are_never_collected(self):
        with patch.object(lifecycle, "run") as run:
            self.assertEqual(lifecycle.verify_managed_configmaps(stack, ["/bound/kubectl"], {"resources": [{"kind": "Secret"}]}), [])
            run.assert_not_called()


class RuntimeOwnershipTest(unittest.TestCase):
    def fixture(self, kind):
        image = "registry.example.invalid/service@sha256:" + "a" * 64
        resource = {"kind": kind, "metadata": {"namespace": "kerosene-staging", "name": "service"},
            "spec": {"replicas": 1, "template": {"spec": {"containers": [{"name": "service", "image": image}]}}}}
        live = copy.deepcopy(resource)
        live["metadata"].update(uid="workload-uid", generation=1)
        live["metadata"]["annotations"] = {"deployment.kubernetes.io/revision": "2"}
        live["spec"]["selector"] = {"matchLabels": {"app": "service"}}
        live["status"] = {"observedGeneration": 1, "readyReplicas": 1, "updatedReplicas": 1}
        live["status"].update(currentRevision="service-revision", updateRevision="service-revision")
        rs = {"metadata": {"uid": "replicaset-uid", "namespace": "kerosene-staging",
            "annotations": {"deployment.kubernetes.io/revision": "2"},
            "ownerReferences": [{"kind": "Deployment", "uid": "workload-uid", "controller": True}]}}
        pod = {"metadata": {"uid": "pod-uid", "namespace": "kerosene-staging", "labels": {"controller-revision-hash": "service-revision"}, "ownerReferences": [{
            "kind": "ReplicaSet" if kind == "Deployment" else "StatefulSet",
            "uid": "replicaset-uid" if kind == "Deployment" else "workload-uid", "controller": True}]},
            "spec": copy.deepcopy(resource["spec"]["template"]["spec"]), "status": {
            "conditions": [{"type": "Ready", "status": "True"}], "containerStatuses": [{"name": "service", "imageID": image}]}}
        return resource, live, rs, pod

    def verify(self, resource, live, rs, pod, final=None):
        responses = [live]
        if resource["kind"] == "Deployment": responses.append({"items": [rs]})
        responses.append({"items": [pod]})
        responses.append(live if final is None else final)
        with patch.object(lifecycle, "run", side_effect=[json.dumps(r).encode() for r in responses]):
            return lifecycle.verify_running(["/bound/kubectl"], resource)

    def test_deployment_and_statefulset_record_owned_runtime(self):
        for kind in ("Deployment", "StatefulSet"):
            with self.subTest(kind=kind):
                self.assertEqual(self.verify(*self.fixture(kind))[0]["workloadUid"], "workload-uid")

    def test_ready_foreign_pods_are_rejected_even_with_approved_image(self):
        for kind in ("Deployment", "StatefulSet"):
            for mutation in ("uid", "controller", "namespace"):
                resource, live, rs, pod = self.fixture(kind)
                if mutation == "namespace": pod["metadata"]["namespace"] = "foreign"
                elif mutation == "uid": pod["metadata"]["ownerReferences"][0]["uid"] = "foreign"
                else: pod["metadata"]["ownerReferences"][0]["controller"] = False
                with self.subTest(kind=kind, mutation=mutation), self.assertRaisesRegex(RuntimeError, "does not belong"):
                    self.verify(resource, live, rs, pod)

    def test_foreign_replicaset_cannot_claim_pod_for_deployment(self):
        resource, live, rs, pod = self.fixture("Deployment")
        rs["metadata"]["ownerReferences"][0]["uid"] = "another-deployment"
        with self.assertRaisesRegex(RuntimeError, "does not belong"):
            self.verify(resource, live, rs, pod)

    def test_deleting_or_identity_changed_workload_is_rejected(self):
        for mutation in ("uid", "name", "deletionTimestamp"):
            resource, live, rs, pod = self.fixture("Deployment")
            live["metadata"][mutation] = "" if mutation == "uid" else "changed"
            with self.subTest(mutation=mutation), self.assertRaisesRegex(RuntimeError, "workload identity"):
                self.verify(resource, live, rs, pod)

    def test_ready_old_replicaset_is_not_current_rollout(self):
        resource, live, rs, pod = self.fixture("Deployment")
        rs["metadata"]["annotations"]["deployment.kubernetes.io/revision"] = "1"
        with self.assertRaisesRegex(RuntimeError, "does not belong"):
            self.verify(resource, live, rs, pod)

    def test_statefulset_requires_completed_revision_and_current_pod(self):
        for mutation in ("current", "pod", "missing"):
            resource, live, rs, pod = self.fixture("StatefulSet")
            if mutation == "current": live["status"]["currentRevision"] = "old"
            elif mutation == "pod": pod["metadata"]["labels"]["controller-revision-hash"] = "old"
            else: live["status"].pop("updateRevision")
            with self.subTest(mutation=mutation), self.assertRaisesRegex(RuntimeError, "revision"):
                self.verify(resource, live, rs, pod)

    def test_deployment_missing_or_malformed_revision_is_not_accepted(self):
        for revision in (None, "0", "-1", 2, "2x"):
            resource, live, rs, pod = self.fixture("Deployment")
            live["metadata"]["annotations"]["deployment.kubernetes.io/revision"] = revision
            with self.subTest(revision=revision), self.assertRaisesRegex(RuntimeError, "revision"):
                self.verify(resource, live, rs, pod)

    def test_replica_target_drift_is_not_hidden_by_ready_pods(self):
        for kind in ("Deployment", "StatefulSet"):
            for replicas in (0, 2, True, "1", None):
                resource, live, rs, pod = self.fixture(kind)
                live["spec"]["replicas"] = replicas
                with self.subTest(kind=kind, replicas=replicas), self.assertRaisesRegex(RuntimeError, "replica target"):
                    self.verify(resource, live, rs, pod)

    def test_boolean_ready_counters_do_not_prove_one_replica(self):
        for field in ("readyReplicas", "updatedReplicas"):
            resource, live, rs, pod = self.fixture("Deployment")
            live["status"][field] = True
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "not all desired"):
                self.verify(resource, live, rs, pod)

    def test_workload_change_during_collection_invalidates_receipt(self):
        for kind in ("Deployment", "StatefulSet"):
            for mutation in ("uid", "generation", "replicas", "readiness", "revision", "deletion"):
                resource, live, rs, pod = self.fixture(kind)
                final = copy.deepcopy(live)
                if mutation == "uid": final["metadata"]["uid"] = "replacement"
                elif mutation == "generation": final["metadata"]["generation"] = 2
                elif mutation == "replicas": final["spec"]["replicas"] = 2
                elif mutation == "readiness": final["status"]["readyReplicas"] = 0
                elif mutation == "deletion": final["metadata"]["deletionTimestamp"] = "now"
                elif kind == "Deployment": final["metadata"]["annotations"]["deployment.kubernetes.io/revision"] = "3"
                else: final["status"]["updateRevision"] = "next"
                with self.subTest(kind=kind, mutation=mutation), self.assertRaisesRegex(RuntimeError, "changed while"):
                    self.verify(resource, live, rs, pod, final)

    def test_unrelated_metadata_update_does_not_invalidate_readiness(self):
        resource, live, rs, pod = self.fixture("Deployment")
        final = copy.deepcopy(live)
        final["metadata"]["annotations"]["diagnostic"] = "updated"
        final["metadata"]["resourceVersion"] = "new-status-version"
        self.assertEqual(len(self.verify(resource, live, rs, pod, final)), 1)

    def test_changed_workload_or_pod_startup_configuration_is_rejected(self):
        for target in ("workload", "pod"):
            for field, value in (("command", ["sh"]), ("args", ["--unsafe"]),
                    ("env", [{"name": "SPRING_DATASOURCE_URL", "value": "foreign"}]),
                    ("envFrom", [{"secretRef": {"name": "foreign"}}])):
                resource, live, rs, pod = self.fixture("Deployment")
                container = live["spec"]["template"]["spec"]["containers"][0] if target == "workload" else pod["spec"]["containers"][0]
                container[field] = value
                with self.subTest(target=target, field=field), self.assertRaisesRegex(RuntimeError, "startup configuration"):
                    self.verify(resource, live, rs, pod)

    def test_approved_startup_inputs_are_accepted_with_omitted_empty_defaults(self):
        resource, live, rs, pod = self.fixture("Deployment")
        values = {"command": ["java"], "args": ["-jar", "/app/app.jar"], "env": [{"name": "SETTING", "value": "approved"}]}
        for container in (resource["spec"]["template"]["spec"]["containers"][0],
                live["spec"]["template"]["spec"]["containers"][0], pod["spec"]["containers"][0]):
            container.update(copy.deepcopy(values))
        pod["spec"]["containers"][0]["envFrom"] = []
        self.assertEqual(len(self.verify(resource, live, rs, pod)), 1)


if __name__ == "__main__":
    unittest.main()
