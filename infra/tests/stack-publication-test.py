#!/usr/bin/env python3
"""Real offline publication tests. Fresh disposable lab keys, never authority.

Optionally set JCTL_PUBLICATION_TEST_BIN to an absolute already installed jctl.
This harness never builds Admin or alters an existing Cell/trust directory.
"""

import base64
import copy
from dataclasses import replace
import datetime as dt
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
INFRA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INFRA / "stack"))
import publication as pub
import archive


def command(argv):
    return subprocess.run(argv, capture_output=True, check=True, timeout=30)


def signature(directory, payload, key):
    source, destination = directory / "lab-payload", directory / "lab-signature"
    source.write_bytes(payload)
    command(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(key),
             "-in", str(source), "-out", str(destination)])
    return destination.read_bytes()


class PublicationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = tempfile.TemporaryDirectory(prefix="kerosene-publication-lab-keys-")
        cls.labdir = Path(cls.lab.name)
        cls.stack = pub.controller()
        cls.keys, cls.signers, key_objects, roles = {}, {}, {}, {}
        for role in ("root", *pub.ROLES, "packaging"):
            cls.keys[role] = []
            for number in range(1 if role == "packaging" else 2):
                path = cls.labdir / f"{role}-{number}.pem"
                command(["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(path)])
                path.chmod(0o600)
                der = command(["openssl", "pkey", "-in", str(path), "-pubout", "-outform", "DER"]).stdout
                key = {"keytype": "ed25519", "scheme": "ed25519", "keyval": {"public": der[-32:].hex()}}
                keyid = hashlib.sha256(archive.canonical_bytes(key)).hexdigest()
                cls.keys[role].append((path, keyid, der))
                if role != "packaging":
                    key_objects[keyid] = key
            if role != "packaging":
                roles[role] = {"keyids": [k[1] for k in cls.keys[role]], "threshold": 2}
            if role in pub.ROLES:
                cls.signers[role] = {k[1]: k[0] for k in cls.keys[role]}
        cls.now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        cls.root_signed = {"_type": "root", "spec_version": "1.0.0", "version": 1,
                           "expires": cls.date(60), "consistent_snapshot": False,
                           "keys": key_objects, "roles": roles}

    @classmethod
    def tearDownClass(cls):
        cls.lab.cleanup()

    @classmethod
    def date(cls, days):
        return (cls.now + dt.timedelta(days=days)).isoformat().replace("+00:00", "Z")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kerosene-publication-test-")
        self.root = Path(self.temp.name)
        self.store = self.root / "store"
        self.store.mkdir(mode=0o700)
        self.output = self.store / "published"
        self.release_path, self.descriptor_path = self.root / "release.json", self.root / "descriptor.json"
        self.root_path = self.root / "trusted-root.json"
        self.artifacts = self.root / "materials"
        self.artifacts.mkdir()
        self.release = json.loads((INFRA / "stack/examples/release-lock-v2.example.json").read_bytes())
        self.release.update(schema="kerosene.release-lock/v3", schemaVersion=3, releaseId="synthetic-lab-release-1", sequence=1)
        self.release["network"]["id"] = "synthetic-lab-bank"
        self.release["authorization"]["tuf"] = {"targetPath": "releases/synthetic-lab-release-1.json"}
        self.release["authorization"]["bft"] = {"networkId": "synthetic-lab-governance", "epoch": 1, "threshold": 3, "members": 4}
        resources = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "kerosene-staging"}},
                     {"apiVersion": "v1", "kind": "ConfigMap",
                      "metadata": {"name": "vault-probe", "namespace": "kerosene-staging"},
                      "data": {"url": "https://localhost:7801/v1/health"}}]
        for name, service in self.release["services"].items():
            if name != "admin":
                instances = [name] if name != "vault" else ["vault-1", "vault-2", "vault-3"]
                for instance in instances:
                    container = {"name": name, "image": service["image"]}
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
                            {"name": "VAULT_ATTESTATION_ROOT", "valueFrom": {"secretKeyRef": {"name": instance + "-runtime", "key": "attestation-root"}}},
                            {"name": "VAULT_DATA_PASSPHRASE", "valueFrom": {"secretKeyRef": {"name": instance + "-runtime", "key": "data-passphrase"}}},
                            {"name": "VAULT_HEALTH_PROBE_URL", "valueFrom": {"configMapKeyRef": {"name": "vault-probe", "key": "url"}}}])
                        container["readinessProbe"] = {"exec": {"command": ["/usr/local/bin/kerosene-vault", "--health-probe"]},
                                                       "timeoutSeconds": 6}
                    containers = [container]
                    if name == "vault":
                        containers.append({"name": "tor", "image": self.release["services"]["tor"]["image"]})
                    resources.append({"apiVersion": "apps/v1", "kind": "Deployment",
                                      "metadata": {"name": instance, "namespace": "kerosene-staging"},
                                      "spec": {"replicas": 1, "template": {"spec": {"containers": containers}}}})
        self.deployment = {"schema": "kerosene.stack.deployment/v1", "environment": "staging-cell",
                           "resources": resources, "admin": {"image": self.release["services"]["admin"]["image"], "config": {"apiBaseUrl": "https://synthetic-core.invalid"}}}
        for name, service in self.release["services"].items():
            service["configDigest"] = self.stack.lifecycle.digest(self.stack.lifecycle.component_config(self.deployment, service["image"], name))
        # Preserve whitespace: neither release lock nor deployment may be rewritten.
        self.deployment_raw = json.dumps(self.deployment, indent=2).encode() + b"\n"
        (self.artifacts / "deployment.json").write_bytes(self.deployment_raw)
        self.marker = self.root / "MUST-NOT-EXECUTE"
        (self.artifacts / "build.sh").write_text("#!/bin/sh\ntouch " + str(self.marker) + "\n")
        (self.artifacts / "images").mkdir()
        (self.artifacts / "images/core.tar").write_bytes(b"synthetic inert image bytes, not an OCI provenance claim")
        self.descriptor = {"schema": "kerosene.cell-package/v1", "releaseId": self.release["releaseId"],
                           "targetSequence": 1, "releaseLockCanonicalDigest": "", "deploymentManifestDigest": archive.bytes_digest(self.deployment_raw),
                           "artifacts": [{"path": p.relative_to(self.artifacts).as_posix(), "size": p.stat().st_size,
                                          "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                                         for p in sorted(self.artifacts.rglob("*")) if p.is_file()]}
        self.signers = copy.deepcopy(self.signers)
        self.versions = {role: 1 for role in pub.ROLES}
        self.expires = {"timestamp": self.date(1), "snapshot": self.date(7), "targets": self.date(30)}
        self.write_root(copy.deepcopy(self.root_signed))
        self.save_inputs()

    def tearDown(self):
        self.temp.cleanup()

    def write_root(self, signed, count=2):
        document = {"signed": signed, "signatures": [
            {"keyid": keyid, "sig": signature(self.labdir, archive.canonical_bytes(signed), key).hex()}
            for key, keyid, der in self.keys["root"][:count]]}
        self.root_path.write_bytes(archive.canonical_bytes(document))

    def save_inputs(self):
        self.release_path.write_bytes(json.dumps(self.release, indent=2).encode() + b"\n")
        self.descriptor.update(releaseId=self.release["releaseId"], targetSequence=self.release["sequence"],
                               releaseLockCanonicalDigest=archive.bytes_digest(archive.canonical_bytes(self.release)))
        self.save_descriptor()

    def save_descriptor(self):
        self.descriptor_path.write_bytes(json.dumps(self.descriptor, indent=2).encode())

    def publish(self, **options):
        defaults = dict(trusted_root=self.root_path, signers=self.signers,
                        publication_store=self.store,
                        packaging_key=self.keys["packaging"][0][0], versions=self.versions,
                        expires=self.expires, initial=True)
        defaults.update(options)
        return pub.publish(self.release_path, self.descriptor_path, self.artifacts,
                           self.output, **defaults)

    def rejected(self, pattern=None, **options):
        errors = (ValueError, OSError, RuntimeError)
        if pattern:
            with self.assertRaisesRegex(errors, pattern):
                self.publish(**options)
        else:
            with self.assertRaises(errors):
                self.publish(**options)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.store.glob(".publication-*")), [])

    def verify(self, output=None, state=None):
        output = output or self.output
        release, raw = self.stack.read_release(str(output / "targets" / self.release["authorization"]["tuf"]["targetPath"]))
        return self.stack.verify_tuf_metadata_bundle(release, raw, self.stack.validate_release(release),
                                                     str(output / "metadata"), str(self.root_path), state)

    def advance(self):
        self.advance_to(2)

    def advance_to(self, sequence):
        self.release.update(sequence=sequence, releaseId=f"synthetic-lab-release-{sequence}")
        self.release["authorization"]["tuf"]["targetPath"] = f"releases/synthetic-lab-release-{sequence}.json"
        self.save_inputs()
        self.versions = {role: sequence for role in pub.ROLES}
        self.expires = {"timestamp": self.date(sequence), "snapshot": self.date(6 + sequence), "targets": self.date(29 + sequence)}

    def test_actual_deploy_verifier_roundtrip_and_exact_bytes(self):
        result = self.publish()
        verified = self.verify()
        self.assertTrue(verified["signatureVerified"])
        self.assertEqual(verified["metadataVersions"], result["metadataVersions"])
        self.assertFalse(result["releaseAuthorized"])
        self.assertFalse(result["deploymentExecuted"])
        self.assertEqual((self.output / "targets" / self.release["authorization"]["tuf"]["targetPath"]).read_bytes(), self.release_path.read_bytes())
        self.assertEqual((self.output / "manifest.json").read_bytes(), archive.canonical_bytes(self.descriptor))
        self.assertEqual((self.output / "targets/artifacts" / self.release["releaseId"] / "deployment.json").read_bytes(), self.deployment_raw)
        self.assertFalse(self.marker.exists())
        for path in self.output.rglob("*"):
            self.assertFalse(path.is_symlink())
            if path.is_dir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            if path.is_file():
                self.assertEqual(path.stat().st_mode & 0o777, 0o444)
                self.assertNotIn(b"BEGIN PRIVATE KEY", path.read_bytes())
        raw_sig = (self.output / "manifest.sig.raw").read_bytes()
        self.assertEqual(len(raw_sig), 64)
        self.assertEqual(base64.b64decode((self.output / "manifest.sig").read_bytes(), validate=True), raw_sig)
        self.stack.verify_ed25519((self.output / "manifest.json").read_bytes(), raw_sig,
                                  self.keys["packaging"][0][2], "lab packaging signature")
        target_doc = json.loads((self.output / "metadata/targets.json").read_bytes())
        for name, target in target_doc["signed"]["targets"].items():
            data = (self.output / "targets" / name).read_bytes()
            self.assertEqual(target["length"], len(data))
            self.assertEqual(target["hashes"]["sha256"], hashlib.sha256(data).hexdigest())

    def test_reproducible_with_same_explicit_inputs(self):
        self.publish()
        first = self.output
        # Identical laboratory inputs may be tested in two independent stores;
        # the first store itself must reject an initial replay.
        self.store = self.root / "independent-store"
        self.store.mkdir(mode=0o700)
        self.output = self.store / "second"
        self.publish()
        a = {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}
        b = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob("*") if p.is_file()}
        self.assertEqual(a, b)

    def test_supported_v2_release_is_also_verified(self):
        self.release.update(schema="kerosene.release-lock/v2", schemaVersion=2)
        self.release["authorization"]["bft"] = {
            "networkId": "synthetic-lab-governance", "height": 1, "commitDigest": "sha256:" + "b" * 64,
            "threshold": 3, "members": 4}
        self.save_inputs()
        self.publish()
        self.assertTrue(self.verify()["signatureVerified"])

    def test_every_role_requires_full_distinct_threshold(self):
        for role in pub.ROLES:
            with self.subTest(role=role):
                selected = copy.deepcopy(self.signers)
                selected[role].pop(next(iter(selected[role])))
                self.rejected("threshold", signers=selected)

    def test_root_threshold_is_verified(self):
        self.write_root(copy.deepcopy(self.root_signed), count=1)
        self.rejected("distinct valid TUF")

    def test_root_keyid_threshold_duplicate_and_extra_role_policy(self):
        root = copy.deepcopy(self.root_signed)
        keyid = next(iter(root["keys"]))
        root["keys"]["0" * 64] = root["keys"].pop(keyid)
        self.write_root(root)
        self.rejected("key ID does not match")
        root = copy.deepcopy(self.root_signed)
        root["roles"]["targets"]["threshold"] = 3
        self.write_root(root)
        self.rejected("cannot exceed")
        root = copy.deepcopy(self.root_signed)
        root["roles"]["targets"]["keyids"].append(root["roles"]["targets"]["keyids"][0])
        self.write_root(root)
        self.rejected("duplicate key ID")
        root = copy.deepcopy(self.root_signed)
        root["roles"]["mirrors"] = copy.deepcopy(root["roles"]["targets"])
        self.write_root(root)
        self.rejected("unexpected/missing")

    def test_wrong_key_role_and_keyid(self):
        for role in pub.ROLES:
            with self.subTest(role=role):
                selected = copy.deepcopy(self.signers)
                selected[role][next(iter(selected[role]))] = self.keys["packaging"][0][0]
                self.rejected("does not match", signers=selected)
        selected = copy.deepcopy(self.signers)
        selected["targets"] = {k[1]: k[0] for k in self.keys["root"]}
        self.rejected("not authorized", signers=selected)

    def test_packaging_authority_cannot_be_reused(self):
        self.rejected("separate", packaging_key=self.keys["root"][0][0])

    def test_explicit_history_and_bootstrap_versions(self):
        self.rejected("choose explicit", initial=False)
        self.rejected("choose explicit", previous=self.root)
        self.rejected("initial publication", versions={r: 2 for r in pub.ROLES})

    def test_versions_expiries_and_sequence_advance_with_prior_verifier_state(self):
        self.publish()
        prior, verified = self.output, self.verify()
        state = self.root / "verifier-state"
        state.mkdir()
        self.stack.persist_tuf_state(str(state), verified)
        before = (state / self.stack.TUF_STATE_FILENAME).read_bytes()
        self.output = self.store / "next"
        self.advance()
        self.publish(initial=False, previous=prior)
        self.assertEqual(self.verify(state=str(state))["metadataVersions"]["timestamp"], 2)
        self.assertEqual((state / self.stack.TUF_STATE_FILENAME).read_bytes(), before)

    def test_rollback_or_same_version_or_expiry_fails(self):
        self.publish()
        prior = self.output
        self.output = self.store / "next"
        self.advance()
        for role in pub.ROLES:
            with self.subTest(role=role):
                bad = dict(self.versions, **{role: 1})
                self.rejected("strictly advance", initial=False, previous=prior, versions=bad)
                bad_expiry = dict(self.expires, **{role: {"timestamp": self.date(1), "snapshot": self.date(7), "targets": self.date(30)}[role]})
                self.rejected("strictly advance", initial=False, previous=prior, expires=bad_expiry)
        self.release["sequence"] = 1
        self.save_inputs()
        self.rejected("advance sequence", initial=False, previous=prior)

    def test_expired_and_inverted_new_expiries(self):
        for value in (self.date(-1), "not-a-date", "2026-99-99T00:00:00Z"):
            self.rejected(expires=dict(self.expires, timestamp=value))
        self.rejected("require timestamp", expires=dict(self.expires, timestamp=self.date(10)))
        self.rejected("require timestamp", expires=dict(self.expires, targets=self.date(61)))

    def test_root_rotation_and_consistent_snapshot_policy(self):
        root = copy.deepcopy(self.root_signed)
        root["consistent_snapshot"] = True
        self.write_root(root)
        self.rejected("consistent_snapshot")
        root = copy.deepcopy(self.root_signed)
        root["expires"] = self.date(-1)
        self.write_root(root)
        self.rejected("expired")

    def test_prior_tamper_delegation_rotation_extra_file_fail_closed(self):
        self.publish()
        prior = self.output
        self.output = self.store / "next"
        self.advance()
        path = prior / "metadata/targets.json"
        original = path.read_bytes()
        doc = json.loads(original)
        doc["signed"]["delegations"] = {"keys": {}, "roles": []}
        path.chmod(0o600)
        path.write_bytes(archive.canonical_bytes(doc))
        self.rejected("unexpected/missing", initial=False, previous=prior)
        path.write_bytes(original.replace(b'"version":1', b'"version":8'))
        self.rejected(initial=False, previous=prior)
        path.write_bytes(original)
        extra = prior / "metadata/2.root.json"
        extra.write_bytes(self.root_path.read_bytes())
        self.rejected("rotation", initial=False, previous=prior)
        extra.unlink()
        changed = copy.deepcopy(self.root_signed)
        changed["expires"] = self.date(59)
        self.write_root(changed)
        self.rejected("changed trust anchor", initial=False, previous=prior)

    def test_malformed_prior_signature_array_and_expired_prior_fail_closed(self):
        self.publish()
        prior = self.output
        self.output = self.store / "next"
        self.advance()
        path = prior / "metadata/timestamp.json"
        original = json.loads(path.read_bytes())
        path.chmod(0o600)
        malformed = copy.deepcopy(original)
        malformed["signatures"] = 5
        path.write_bytes(archive.canonical_bytes(malformed))
        self.rejected("signature count", initial=False, previous=prior)
        expired = copy.deepcopy(original)
        expired["signed"]["expires"] = self.date(-1)
        path.write_bytes(archive.canonical_bytes(expired))
        self.rejected("expired", initial=False, previous=prior)

    def test_mismatched_network_or_reused_release_id_fail_closed(self):
        self.publish()
        prior = self.output
        self.output = self.store / "next"
        self.advance()
        self.release["network"]["id"] = "different-synthetic-bank"
        self.save_inputs()
        self.rejected("same network", initial=False, previous=prior)
        self.release["network"]["id"] = "synthetic-lab-bank"
        self.release["releaseId"] = "synthetic-lab-release-1"
        self.release["authorization"]["tuf"]["targetPath"] = "releases/synthetic-lab-release-1.json"
        self.save_inputs()
        self.rejected("new release ID", initial=False, previous=prior)

    def test_descriptor_bindings_are_exact(self):
        for field, value in (("schema", "unknown"), ("releaseId", "other-lab-release"),
                             ("targetSequence", True), ("releaseLockCanonicalDigest", "sha256:" + "a" * 64),
                             ("deploymentManifestDigest", "sha256:" + "a" * 64)):
            with self.subTest(field=field):
                original = self.descriptor[field]
                self.descriptor[field] = value
                self.save_descriptor()
                self.rejected()
                self.descriptor[field] = original

    def test_hash_size_and_bounds_fail_without_output(self):
        entry = self.descriptor["artifacts"][0]
        original = copy.deepcopy(entry)
        for field, value in (("sha256", "0" * 64), ("sha256", "F" * 64), ("size", entry["size"] + 1),
                             ("size", -1), ("size", True), ("size", 9007199254740992)):
            entry.update(original)
            entry[field] = value
            self.save_descriptor()
            self.rejected()
        entry.update(original)
        self.save_descriptor()
        self.rejected("aggregate", limits=replace(pub.Limits(), total_artifact_bytes=1))
        self.rejected("integer", limits=replace(pub.Limits(), artifact_bytes=1))
        self.rejected("file byte", limits=replace(pub.Limits(), metadata_bytes=128))
        self.rejected("count", limits=replace(pub.Limits(), artifacts=1))

    def test_noncanonical_paths_duplicates_and_collisions(self):
        original = self.descriptor["artifacts"][0]["path"]
        for path in ("../escape", "/absolute", "a//b", "a/./b", "a\\b", "https://remote.invalid/x", "a\x00b", "a/" + "x" * 241):
            self.descriptor["artifacts"][0]["path"] = path
            self.save_descriptor()
            self.rejected()
        self.descriptor["artifacts"][0]["path"] = original
        self.descriptor["artifacts"].append(copy.deepcopy(self.descriptor["artifacts"][0]))
        self.save_descriptor()
        self.rejected("duplicate")
        self.descriptor["artifacts"][-1]["path"] = original + "/child"
        self.save_descriptor()
        self.rejected("collision")

    def test_duplicates_nonfinite_and_deep_json(self):
        original = self.descriptor_path.read_bytes()
        for raw in (b'{"schema":1,"schema":2}', b'{"n":NaN}', b'{"n":1e999}', b"[" * 1200 + b"]" * 1200):
            self.descriptor_path.write_bytes(raw)
            self.rejected()
        self.descriptor_path.write_bytes(original)

    def test_symlink_inputs_and_parents_fifo_and_output(self):
        source = self.artifacts / "build.sh"
        raw = source.read_bytes()
        external = self.root / "script"
        external.write_bytes(raw)
        source.unlink()
        source.symlink_to(external)
        self.rejected()
        source.unlink()
        os.mkfifo(source)
        self.rejected()
        source.unlink()
        source.write_bytes(raw)
        alias = self.root / "alias"
        alias.symlink_to(self.artifacts, target_is_directory=True)
        real = self.artifacts
        self.artifacts = alias
        self.rejected()
        self.artifacts = real
        trusted = self.root_path
        root_alias = self.root / "root-alias"
        root_alias.symlink_to(trusted)
        self.rejected(trusted_root=root_alias)
        output_alias = self.root / "output-alias"
        output_alias.symlink_to(self.root, target_is_directory=True)
        self.output = output_alias / "no-output"
        self.rejected()

    def test_private_key_reference_links_modes_and_wrong_algorithms(self):
        key = self.root / "signer.pem"
        key.write_bytes(self.keys["packaging"][0][0].read_bytes())
        key.chmod(0o644)
        self.rejected("private signer", packaging_key=key)
        key.chmod(0o600)
        alias = self.root / "signer-alias"
        alias.symlink_to(key)
        self.rejected(packaging_key=alias)
        alias.unlink()
        os.link(key, alias)
        self.rejected("single-link", packaging_key=key)
        alias.unlink()
        command(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", str(key)])
        self.rejected("Ed25519", packaging_key=key)

    def test_signer_material_cannot_enter_artifacts(self):
        raw = self.keys["packaging"][0][0].read_bytes()
        (self.artifacts / "oops.pem").write_bytes(raw)
        self.descriptor["artifacts"].append({"path": "oops.pem", "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        self.save_descriptor()
        self.rejected("private signer material")

    def test_deployment_config_and_policy_are_checked_by_installed_validator(self):
        self.release["services"]["core"]["configDigest"] = "sha256:" + "0" * 64
        self.save_inputs()
        self.rejected("configuration digest mismatch")

    def test_existing_destination_and_atomic_race_are_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "existing"
        sentinel.write_bytes(b"keep")
        with self.assertRaisesRegex(pub.PublicationError, "already exists"):
            self.publish()
        self.assertEqual(sentinel.read_bytes(), b"keep")
        sentinel.unlink()
        self.output.rmdir()
        real_publish = archive.publish_directory
        def race(staged, destination, **options):
            destination.mkdir()
            (destination / "race-winner").write_bytes(b"keep")
            return real_publish(staged, destination)
        with patch.object(pub, "publish_directory", side_effect=race):
            with self.assertRaises(FileExistsError):
                self.publish()
        self.assertEqual((self.output / "race-winner").read_bytes(), b"keep")
        self.assertEqual(list(self.store.glob(".publication-*")), [])

    def test_existing_output_file_or_dangling_symlink_never_replaced(self):
        self.output.write_bytes(b"keep")
        with self.assertRaisesRegex(pub.PublicationError, "already exists"):
            self.publish()
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.output.unlink()
        self.output.symlink_to(self.root / "does-not-exist")
        with self.assertRaisesRegex(pub.PublicationError, "already exists"):
            self.publish()
        self.assertTrue(self.output.is_symlink())

    def test_atomic_publication_failure_and_signer_failure_cleanup(self):
        with patch.object(pub.SigningSession, "sign", side_effect=pub.PublicationError("synthetic signer failure")):
            self.rejected()
        with patch.object(pub, "publish_directory", side_effect=OSError("synthetic rename failure")):
            self.rejected()
        self.assertTrue((self.store / pub.RESERVATION_FILE).exists())

    def test_subprocess_timeouts_fail_closed_and_clean_up(self):
        real_run = subprocess.run
        def signing_timeout(argv, **kwargs):
            if "pkey" in argv:
                raise subprocess.TimeoutExpired("synthetic-lab-openssl", 1)
            return real_run(argv, **kwargs)
        with patch.object(pub.subprocess, "run", side_effect=signing_timeout):
            self.rejected("signing operation exceeded")
        with patch.object(pub.subprocess, "run", side_effect=subprocess.TimeoutExpired("synthetic-lab-openssl", 1)):
            self.rejected("verification exceeded")
        bounded = pub.controller(pub.Limits(), 0)
        with self.assertRaisesRegex(pub.PublicationError, "deadline"):
            bounded.verify_ed25519(b"synthetic", b"0" * 64, self.keys["packaging"][0][2], "lab timeout")

    def test_artifact_growth_during_read_remains_bounded(self):
        real_copy = pub.copy_artifact
        def grow(source, destination, entry, limits, deadline):
            if source.name == "build.sh":
                with open(source, "ab") as stream:
                    stream.write(b"extra")
            return real_copy(source, destination, entry, limits, deadline)
        with patch.object(pub, "copy_artifact", side_effect=grow):
            self.rejected("size mismatch")

    def test_consumer_rejects_release_bytes_or_metadata_tamper(self):
        self.publish()
        release = self.output / "targets" / self.release["authorization"]["tuf"]["targetPath"]
        raw = release.read_bytes()
        release.chmod(0o600)
        release.write_bytes(raw + b" ")
        with self.assertRaisesRegex(ValueError, "release-lock bytes"):
            self.verify()
        release.write_bytes(raw)
        path = self.output / "metadata/timestamp.json"
        doc = json.loads(path.read_bytes())
        doc["signatures"] = doc["signatures"][:1]
        path.chmod(0o600)
        path.write_bytes(archive.canonical_bytes(doc))
        with self.assertRaisesRegex(ValueError, "distinct valid TUF"):
            self.verify()

    def cli_args(self):
        args = [sys.executable, "-B", str(INFRA / "stack/publish-release.py"),
                "--release-lock", str(self.release_path), "--package-descriptor", str(self.descriptor_path),
                "--artifacts", str(self.artifacts), "--output", str(self.output), "--trusted-root", str(self.root_path),
                "--publication-store", str(self.store),
                "--packaging-key", str(self.keys["packaging"][0][0]), "--initial-publication"]
        for role in pub.ROLES:
            args.extend(["--" + role + "-version", "1", "--" + role + "-expires", self.expires[role]])
            for keyid, path in self.signers[role].items():
                args.extend(["--tuf-signer", f"{role}:{keyid}={path}"])
        return args

    def test_operator_cli_success_and_duplicate_references_fail(self):
        args = self.cli_args()
        result = json.loads(command(args).stdout)
        self.assertTrue(result["tufSignatureVerified"])
        self.verify()
        self.output = self.store / "duplicate-test"
        args = self.cli_args() + ["--tuf-signer", self.cli_args()[-1]]
        failure = subprocess.run(args, capture_output=True, timeout=30)
        self.assertNotEqual(failure.returncode, 0)
        self.assertIn(b"duplicate", failure.stderr)
        self.assertNotIn(b"BEGIN PRIVATE KEY", failure.stderr)
        self.assertFalse(self.output.exists())

    def test_store_ledger_binds_root_domain_high_water_and_entire_publication(self):
        result = self.publish()
        path = self.store / pub.LEDGER_FILE
        state = json.loads(path.read_bytes())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(state["generation"], 1)
        self.assertEqual(state["trustedRootDigest"], archive.bytes_digest(self.root_path.read_bytes()))
        self.assertEqual(state["networkId"], self.release["network"]["id"])
        last = state["lastPublication"]
        self.assertEqual(last["name"], self.output.name)
        self.assertEqual(last["sequence"], 1)
        self.assertEqual(last["releaseId"], self.release["releaseId"])
        self.assertEqual(last["metadataVersions"], result["metadataVersions"])
        self.assertEqual(last["expires"], self.expires)
        self.assertEqual(last["releaseLockCanonicalDigest"], self.descriptor["releaseLockCanonicalDigest"])
        self.assertEqual(last["bytesDigest"], pub.bundle_bytes_digest(self.output, pub.Limits(), time.monotonic() + 10))
        self.assertEqual(last["bytesDigest"], result["publicationBytesDigest"])
        self.assertFalse((self.store / pub.RESERVATION_FILE).exists())
        prior = self.output
        self.output = self.store / "advanced"
        self.advance()
        self.publish(initial=False, previous=prior)
        advanced = json.loads(path.read_bytes())
        self.assertEqual(advanced["generation"], 2)
        self.assertEqual(advanced["lastPublication"]["sequence"], 2)
        self.assertEqual(advanced["lastPublication"]["metadataVersions"]["timestamp"], 2)
        self.assertFalse((self.store / pub.RESERVATION_FILE).exists())

    def test_store_rejects_stale_baseline_and_byte_identical_alias(self):
        self.publish()
        first = self.output
        self.output = self.store / "second"
        self.advance()
        self.publish(initial=False, previous=first)
        second = self.output
        ledger = (self.store / pub.LEDGER_FILE).read_bytes()
        self.output = self.store / "third"
        self.advance_to(3)
        self.rejected("exact last verified", initial=False, previous=first)
        alias = self.store / "baseline-copy"
        shutil.copytree(second, alias)
        self.rejected("exact last verified", initial=False, previous=alias)
        self.assertEqual((self.store / pub.LEDGER_FILE).read_bytes(), ledger)

    def test_store_refuses_initial_replay_and_missing_ledger_reset(self):
        self.publish()
        self.output = self.store / "replayed"
        self.rejected("empty store", initial=True)
        path = self.store / pub.LEDGER_FILE
        original = path.read_bytes()
        path.unlink()  # Only this test's disposable ledger, simulating loss.
        self.rejected("missing ledger in nonempty", initial=True)
        path.write_bytes(original)
        path.chmod(0o600)

    def test_initial_requires_a_preprovisioned_empty_private_store(self):
        (self.store / "untracked-data").write_bytes(b"synthetic")
        self.rejected("nonempty store")
        (self.store / "untracked-data").unlink()
        self.store.chmod(0o755)
        self.rejected("0700")
        self.store.chmod(0o700)
        alias = self.root / "store-alias"
        alias.symlink_to(self.store, target_is_directory=True)
        self.rejected(publication_store=alias)
        self.rejected(publication_store=self.root / "missing-store")
        self.assertFalse((self.root / "missing-store").exists())

    def test_output_and_baseline_are_confined_to_selected_store_direct_children(self):
        desired = self.output
        for destination in (self.root / "outside", self.store / "nested" / "output"):
            self.output = destination
            self.rejected("direct children")
        self.output = desired
        self.publish()
        prior = self.output
        self.output = self.store / "next"
        self.advance()
        other = self.root / "other-store"
        other.mkdir(mode=0o700)
        outside = other / "copied-baseline"
        shutil.copytree(prior, outside)
        self.rejected("direct children", initial=False, previous=outside)

    def test_ledger_binds_all_baseline_bytes_before_reading_private_signers(self):
        self.publish()
        prior = self.output
        self.output = self.store / "next"
        self.advance()
        for relative in ("manifest.json", "manifest.sig", "manifest.sig.raw", "targets/artifacts/synthetic-lab-release-1/images/core.tar"):
            path = prior / relative
            original = path.read_bytes()
            path.chmod(0o600)
            path.write_bytes(original + b"changed")
            with patch.object(pub.SigningSession, "key", side_effect=AssertionError("private signer read before baseline check")):
                self.rejected("exact last publication bytes", initial=False, previous=prior)
            path.write_bytes(original)
        (prior / "extra-empty-directory").mkdir()
        self.rejected("exact last publication bytes", initial=False, previous=prior)

    def test_ledger_json_schema_bounds_and_high_water_tamper_fail_closed(self):
        self.publish()
        prior = self.output
        self.output = self.store / "next"
        self.advance()
        path = self.store / pub.LEDGER_FILE
        original = path.read_bytes()
        for raw in (b'{"schema":1,"schema":2}', b'{"generation":NaN}', b"x" * (64 * 1024 + 1),
                    b"[" * 1200 + b"]" * 1200):
            path.write_bytes(raw)
            self.rejected(initial=False, previous=prior)
        for field, value in (("generation", True), ("generation", 1.0), ("generation", 9007199254740992),
                             ("extra", "unsupported")):
            bad = json.loads(original)
            bad[field] = value
            path.write_bytes(archive.canonical_bytes(bad))
            self.rejected(initial=False, previous=prior)
        for field, value in (("sequence", 9), ("metadataVersions", {"root": 1, "targets": 9, "snapshot": 1, "timestamp": 1}),
                             ("name", "../outside"), ("bytesDigest", "sha256:" + "0" * 64)):
            bad = json.loads(original)
            bad["lastPublication"][field] = value
            path.write_bytes(archive.canonical_bytes(bad))
            self.rejected(initial=False, previous=prior)
        path.write_bytes(original)

    def test_ledger_symlinks_hardlinks_fifo_and_modes_fail_closed(self):
        self.publish()
        prior = self.output
        self.output = self.store / "next"
        self.advance()
        path = self.store / pub.LEDGER_FILE
        original = path.read_bytes()
        path.chmod(0o644)
        self.rejected("private operator-owned", initial=False, previous=prior)
        path.chmod(0o600)
        linked = self.root / "ledger-hardlink"
        os.link(path, linked)
        self.rejected("single-link", initial=False, previous=prior)
        linked.unlink()
        path.unlink()
        os.mkfifo(path, 0o600)
        self.rejected("regular file", initial=False, previous=prior)
        path.unlink()
        linked.write_bytes(original)
        linked.chmod(0o600)
        path.symlink_to(linked)
        self.rejected(initial=False, previous=prior)

    def run_children(self, workers):
        for process in workers:
            process.start()
        try:
            for process in workers:
                process.join(20)
                self.assertFalse(process.is_alive(), "owned lab publisher exceeded deadline")
        finally:
            for process in workers:
                if process.is_alive():
                    process.terminate()
                    process.join(5)

    def test_concurrent_cooperating_publishers_cannot_fork_different_destinations(self):
        self.publish()
        prior = self.output
        self.advance()
        context = multiprocessing.get_context("fork")
        ready, go, results = context.Queue(), context.Event(), context.Queue()
        def worker(name):
            self.output = self.store / name
            ready.put(name)
            if not go.wait(5):
                return
            try:
                result = self.publish(initial=False, previous=prior)
                results.put((name, "published", result["publicationStoreGeneration"]))
            except pub.PublicationError as error:
                results.put((name, "rejected", str(error)))
        workers = [context.Process(target=worker, args=(name,)) for name in ("concurrent-a", "concurrent-b")]
        for process in workers:
            process.start()
        try:
            ready.get(timeout=5)
            ready.get(timeout=5)
            go.set()
            for process in workers:
                process.join(20)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)
            outcomes = [results.get(timeout=2) for _ in workers]
            self.assertEqual(sorted(item[1] for item in outcomes), ["published", "rejected"])
            winner = next(item for item in outcomes if item[1] == "published")
            loser = next(item for item in outcomes if item[1] == "rejected")
            self.assertEqual(winner[2], 2)
            self.assertIn("exact last verified", loser[2])
            self.assertTrue((self.store / winner[0]).is_dir())
            self.assertFalse((self.store / loser[0]).exists())
            self.assertEqual(json.loads((self.store / pub.LEDGER_FILE).read_bytes())["lastPublication"]["name"], winner[0])
            self.verify(output=self.store / winner[0])
        finally:
            go.set()
            for process in workers:
                if process.is_alive():
                    process.terminate()
                    process.join(5)

    def test_pinned_store_flock_has_bounded_contention(self):
        context = multiprocessing.get_context("fork")
        results = context.Queue()
        def worker():
            try:
                self.publish(limits=replace(pub.Limits(), lock_seconds=1, total_seconds=3))
                results.put("unexpected success")
            except pub.PublicationError as error:
                results.put(str(error))
        with pub.PublicationStore(self.store, pub.Limits(), time.monotonic() + 10):
            process = context.Process(target=worker)
            self.run_children([process])
            self.assertEqual(process.exitcode, 0)
            self.assertIn("lock timeout", results.get(timeout=2))
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.store.iterdir()), [])

    def test_injected_process_crash_after_visibility_preserves_unresolved_reservation(self):
        self.publish()
        prior = self.output
        old_ledger = (self.store / pub.LEDGER_FILE).read_bytes()
        self.output = self.store / "crashed"
        self.advance()
        context = multiprocessing.get_context("fork")
        def worker():
            # Exact failure point: the output rename+fsync finished, commit has
            # not written high-water state. _exit models no finally/cleanup.
            with patch.object(pub.PublicationStore, "commit", side_effect=lambda: os._exit(91)):
                self.publish(initial=False, previous=prior)
        process = context.Process(target=worker)
        self.run_children([process])
        self.assertEqual(process.exitcode, 91)
        self.assertTrue(self.output.is_dir())
        self.verify()
        self.assertEqual((self.store / pub.LEDGER_FILE).read_bytes(), old_ledger)
        reservation = json.loads((self.store / pub.RESERVATION_FILE).read_bytes())
        self.assertEqual(reservation["baseLedgerDigest"], archive.bytes_digest(old_ledger))
        self.assertEqual(reservation["candidate"]["lastPublication"]["name"], "crashed")
        self.assertEqual(reservation["candidate"]["lastPublication"]["bytesDigest"],
                         pub.bundle_bytes_digest(self.output, pub.Limits(), time.monotonic() + 10))
        self.output = self.store / "retry"
        for initial in (True, False):
            with self.assertRaisesRegex(pub.PublicationError, "unresolved publication reservation"):
                self.publish(initial=initial, previous=None if initial else prior)
        self.assertFalse(self.output.exists())
        self.assertEqual((self.store / pub.LEDGER_FILE).read_bytes(), old_ledger)

    def test_first_publication_commit_failure_cannot_silently_bootstrap_again(self):
        with patch.object(pub.PublicationStore, "commit", side_effect=OSError("injected ledger commit failure")):
            with self.assertRaises(OSError):
                self.publish()
        self.verify()
        self.assertFalse((self.store / pub.LEDGER_FILE).exists())
        self.assertTrue((self.store / pub.RESERVATION_FILE).exists())
        self.output = self.store / "retry"
        self.rejected("manual re-verification")

    def test_failure_after_atomic_ledger_commit_still_requires_manual_reverification(self):
        real = pub.PublicationStore.atomic_json
        def fail_after_commit(store, name, value, **options):
            real(store, name, value, **options)
            if name == pub.LEDGER_FILE:
                raise OSError("injected interruption after durable state write")
        with patch.object(pub.PublicationStore, "atomic_json", new=fail_after_commit):
            with self.assertRaises(OSError):
                self.publish()
        self.verify()
        state = json.loads((self.store / pub.LEDGER_FILE).read_bytes())
        self.assertEqual(state["generation"], 1)
        self.assertTrue((self.store / pub.RESERVATION_FILE).exists())
        self.output = self.store / "retry"
        self.rejected("unresolved publication reservation")

    @unittest.skipUnless(os.environ.get("JCTL_PUBLICATION_TEST_BIN"), "no explicit installed jctl reference")
    def test_actual_installed_jctl_accepts_and_rejects_artifact_and_signature_tamper(self):
        binary = Path(os.environ["JCTL_PUBLICATION_TEST_BIN"])
        self.assertTrue(binary.is_absolute() and binary.is_file() and os.access(binary, os.X_OK),
                        "JCTL_PUBLICATION_TEST_BIN must identify an absolute installed executable")
        self.publish()
        public = self.root / "packaging-public.b64"
        public.write_bytes(base64.b64encode(self.keys["packaging"][0][2]))
        artifacts = self.output / "targets/artifacts" / self.release["releaseId"]
        args = [str(binary), "--output", "json", "cell", "package", "verify",
                "--manifest", str(self.output / "manifest.json"), "--signature", str(self.output / "manifest.sig"),
                "--trusted-key", str(public), "--artifacts", str(artifacts), "--deployment-manifest", str(artifacts / "deployment.json")]
        verified = json.loads(command(args).stdout)
        self.assertEqual(verified["verification"], "VERIFIED")
        self.assertFalse(verified["releaseAuthorized"])
        self.assertFalse(verified["deploymentExecuted"])
        path = artifacts / "images/core.tar"
        original = path.read_bytes()
        path.chmod(0o600)
        path.write_bytes(b"tamper")
        self.assertNotEqual(subprocess.run(args, capture_output=True, timeout=30).returncode, 0)
        path.write_bytes(original)
        sigfile = self.output / "manifest.sig"
        raw = bytearray(base64.b64decode(sigfile.read_bytes(), validate=True))
        raw[0] ^= 1
        sigfile.chmod(0o600)
        sigfile.write_bytes(base64.b64encode(raw))
        self.assertNotEqual(subprocess.run(args, capture_output=True, timeout=30).returncode, 0)


if __name__ == "__main__":
    unittest.main()
