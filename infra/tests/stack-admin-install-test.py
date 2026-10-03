#!/usr/bin/env python3
"""Operator artifact installation tests; not full-Cell live qualification."""
import copy
from contextlib import ExitStack
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("tested_admin_stack", str(ROOT / "kerosene-stack"))
spec = importlib.util.spec_from_loader(loader.name, loader)
stack = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stack
loader.exec_module(stack)
admin = stack.lifecycle.admin_install
UPDATE = "sha256:" + "a" * 64
IMAGE = "registry.example.invalid/kerosene/admin@sha256:" + "b" * 64
LOCAL_IMAGE = "sha256:" + "c" * 64


def archive(entries=None):
    entries = entries or [("kerosene-jctl/bin/kerosene-jctl", b"#!/bin/sh\nexit 0\n", tarfile.REGTYPE),
                          ("kerosene-jctl/lib/test.jar", b"synthetic-not-a-real-jar", tarfile.REGTYPE)]
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as handle:
        for name, data, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(data) if kind == tarfile.REGTYPE else 0
            member.linkname = "/outside"
            handle.addfile(member, io.BytesIO(data) if member.size else None)
    return output.getvalue()


def oci_layout_archive(path, entries=None, compressed=False):
    entries = entries or [("opt/kerosene-jctl/bin/kerosene-jctl", b"#!/bin/sh\nexit 0\n"),
                          ("opt/kerosene-jctl/lib/test.jar", b"synthetic-not-a-real-jar")]
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode="w") as handle:
        for name, data in entries:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            handle.addfile(member, io.BytesIO(data))
    layer_raw = layer.getvalue()
    layer_blob = admin.gzip.compress(layer_raw, mtime=0) if compressed else layer_raw
    layer_digest = hashlib.sha256(layer_blob).hexdigest()
    diff_id = hashlib.sha256(layer_raw).hexdigest()
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(admin.platform.machine(), admin.platform.machine())
    config_raw = json.dumps({"architecture": architecture, "os": "linux",
                             "rootfs": {"type": "layers", "diff_ids": ["sha256:" + diff_id]}},
                            separators=(",", ":")).encode()
    config_digest = hashlib.sha256(config_raw).hexdigest()
    manifest_raw = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                               "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                                          "digest": "sha256:" + config_digest, "size": len(config_raw)},
                               "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip" if compressed else "application/vnd.oci.image.layer.v1.tar",
                                           "digest": "sha256:" + layer_digest, "size": len(layer_blob)}]},
                              separators=(",", ":")).encode()
    manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
    index_raw = json.dumps({"schemaVersion": 2, "manifests": [{
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "digest": "sha256:" + manifest_digest, "size": len(manifest_raw)}]}, separators=(",", ":")).encode()
    blobs = {"oci-layout": b'{"imageLayoutVersion":"1.0.0"}', "index.json": index_raw,
             "blobs/sha256/" + config_digest: config_raw,
             "blobs/sha256/" + layer_digest: layer_blob,
             "blobs/sha256/" + manifest_digest: manifest_raw}
    with tarfile.open(path, mode="w") as handle:
        for name, data in blobs.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            handle.addfile(member, io.BytesIO(data))
    return "registry.example.invalid/kerosene/admin@sha256:" + manifest_digest


class InstallationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cell = Path(self.tmp.name)
        self.summary = {"services": {"admin": {"image": IMAGE, "configDigest": "sha256:" + "d" * 64}}}
        self.config = {"apiBaseUrl": "https://core.invalid", "kfeBaseUrl": "https://kfe.invalid"}
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, argv):
        self.calls.append(argv)
        if argv[-1] == "--version":
            return b"openjdk 21.0.8 2025-07-15\n"
        if "inspect" in argv:
            return json.dumps([{"Id": LOCAL_IMAGE, "RepoDigests": [IMAGE]}]).encode()
        return b"synthetic-container-id\n"

    def install(self, raw=None):
        with patch.dict(os.environ, {}, clear=True), patch.object(admin.shutil, "which", side_effect=lambda name: "/trusted/" + name), patch.object(admin, "capture_archive", return_value=raw or archive()):
            return admin.install(stack, self.cell, "cell-test", self.summary, self.config, UPDATE, self.run_tool)

    def target(self):
        return self.cell / "admin/installations" / UPDATE[7:]

    def test_installs_without_starting_candidate_and_pins_local_image(self):
        receipt = self.install()
        self.assertEqual(receipt["image"], IMAGE)
        self.assertEqual(receipt["updateId"], UPDATE)
        self.assertEqual(receipt["cellId"], "cell-test")
        create = next(argv for argv in self.calls if "create" in argv)
        self.assertEqual(create[-1], LOCAL_IMAGE)
        self.assertIn("--read-only", create)
        self.assertEqual(create[create.index("--network") + 1], "none")
        self.assertFalse(any("start" in argv or "run" in argv or "pull" in argv or "build" in argv for argv in self.calls))
        remove = next(argv for argv in self.calls if "rm" in argv)
        self.assertEqual(remove[-1], create[create.index("--name") + 1])
        self.assertNotIn("--force", remove)
        self.assertEqual(admin.verify_installation(stack, self.target(), receipt), self.target() / "bin/kerosene-jctl")
        for path in [self.target(), self.target() / "bin", self.target() / "lib", self.target() / "installation.json"]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0)
        self.assertEqual(list(self.target().parent.glob(".admin-stage-*")), [])

    def test_installs_exact_offline_oci_layout_without_docker(self):
        oci = self.cell / "admin-image.oci.tar"
        self.summary["services"]["admin"]["image"] = oci_layout_archive(oci, compressed=True)
        with patch.dict(os.environ, {}, clear=True), patch.object(admin.shutil, "which", side_effect=lambda name: "/trusted/" + name):
            receipt = admin.install(stack, self.cell, "cell-test", self.summary, self.config, UPDATE,
                                    self.run_tool, str(oci))
        self.assertEqual(receipt["image"], self.summary["services"]["admin"]["image"])
        self.assertFalse(any("inspect" in call or "create" in call for call in self.calls))
        self.assertEqual(admin.verify_installation(stack, self.target(), receipt), self.target() / "bin/kerosene-jctl")

    def test_offline_oci_rejects_manifest_layer_and_path_tampering(self):
        oci = self.cell / "admin-image.oci.tar"
        image = oci_layout_archive(oci)
        wrong = image[:-1] + ("0" if image[-1] != "0" else "1")
        with self.assertRaisesRegex(RuntimeError, "approved Admin manifest"):
            admin.oci_admin_distribution(stack, oci, wrong)
        bad = self.cell / "bad-path.oci.tar"
        bad_image = oci_layout_archive(bad, [("opt/kerosene-jctl/bin/kerosene-jctl", b"x"),
                                             ("opt/kerosene-jctl/lib/test.jar", b"x"),
                                             ("opt/kerosene-jctl/secrets/token", b"secret")])
        with self.assertRaisesRegex(RuntimeError, "invalid entry"):
            admin.oci_admin_distribution(stack, bad, bad_image)

    def test_same_target_recovery_rechecks_bytes_without_creating_another_container(self):
        first = self.install()
        self.calls.clear()
        self.assertEqual(self.install(), first)
        self.assertFalse(any("create" in argv for argv in self.calls))
        (self.target() / "lib/test.jar").write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "digest changed"):
            self.install()

    def test_same_digest_with_changed_configuration_is_not_reused(self):
        self.install()
        self.summary["services"]["admin"]["configDigest"] = "sha256:" + "e" * 64
        with self.assertRaisesRegex(RuntimeError, "another release"):
            self.install()

    def test_wrong_oci_digest_does_not_create_container(self):
        def wrong(argv):
            if "inspect" in argv:
                return json.dumps([{"Id": LOCAL_IMAGE, "RepoDigests": ["unapproved"]}]).encode()
            return self.run_tool(argv)
        with patch.dict(os.environ, {}, clear=True), patch.object(admin.shutil, "which", return_value="/trusted/tool"), patch.object(admin, "capture_archive") as transfer:
            with self.assertRaisesRegex(RuntimeError, "OCI image identity"):
                admin.install(stack, self.cell, "cell-test", self.summary, self.config, UPDATE, wrong)
            transfer.assert_not_called()

    def test_transfer_failure_removes_only_disposable_container_and_keeps_no_installation(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(admin.shutil, "which", return_value="/trusted/tool"), patch.object(admin, "capture_archive", side_effect=RuntimeError("transfer failed")):
            with self.assertRaisesRegex(RuntimeError, "transfer failed"):
                admin.install(stack, self.cell, "cell-test", self.summary, self.config, UPDATE, self.run_tool)
        self.assertFalse(self.target().exists())
        self.assertEqual(self.calls[-1][-2], "rm")

    def test_bad_archive_never_becomes_visible_and_can_be_retried(self):
        with self.assertRaisesRegex(RuntimeError, "invalid"):
            self.install(b"not-a-tar")
        self.assertFalse(self.target().exists())
        self.assertEqual(list(self.target().parent.glob(".admin-stage-*")), [])
        self.install()

    def test_archive_rejects_traversal_links_devices_duplicates_and_arbitrary_files(self):
        entries = [("kerosene-jctl/../outside", b"x", tarfile.REGTYPE),
                   ("/kerosene-jctl/lib/a.jar", b"x", tarfile.REGTYPE),
                   ("kerosene-jctl/lib/a.jar", b"", tarfile.SYMTYPE),
                   ("kerosene-jctl/lib/a.jar", b"", tarfile.LNKTYPE),
                   ("kerosene-jctl/lib/a.jar", b"", tarfile.CHRTYPE),
                   ("kerosene-jctl/lib/a.jar", b"", tarfile.FIFOTYPE),
                   ("kerosene-jctl/secrets/session", b"x", tarfile.REGTYPE),
                   ("kerosene-jctl//lib/a.jar", b"x", tarfile.REGTYPE),
                   ("kerosene-jctl/lib/a\\b.jar", b"x", tarfile.REGTYPE)]
        for entry in entries:
            with self.subTest(entry=entry[0]), tempfile.TemporaryDirectory(dir=self.cell) as dest:
                with self.assertRaises(RuntimeError):
                    admin.extract_distribution(archive([entry]), Path(dest))
        duplicate = ("kerosene-jctl/lib/a.jar", b"x", tarfile.REGTYPE)
        with tempfile.TemporaryDirectory(dir=self.cell) as dest, self.assertRaisesRegex(RuntimeError, "duplicate"):
            admin.extract_distribution(archive([duplicate, duplicate]), Path(dest))

    def test_archive_limits_and_required_files(self):
        with tempfile.TemporaryDirectory(dir=self.cell) as dest:
            with patch.object(admin, "MAX_ARCHIVE", 10), self.assertRaisesRegex(RuntimeError, "transfer limit"):
                admin.extract_distribution(archive(), Path(dest))
            with patch.object(admin, "MAX_FILES", 1), self.assertRaisesRegex(RuntimeError, "invalid"):
                admin.extract_distribution(archive(), Path(dest))
        with tempfile.TemporaryDirectory(dir=self.cell) as dest, self.assertRaisesRegex(RuntimeError, "lacks launcher"):
            admin.extract_distribution(archive([("kerosene-jctl/lib/a.jar", b"x", tarfile.REGTYPE)]), Path(dest))

    def test_file_permission_extra_file_and_symlink_tampering_block_reuse(self):
        receipt = self.install()
        jar = self.target() / "lib/test.jar"
        jar.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, "private and regular"):
            admin.verify_installation(stack, self.target(), receipt)
        jar.chmod(0o600)
        extra = self.target() / "lib/extra.jar"
        extra.write_bytes(b"extra")
        with self.assertRaisesRegex(RuntimeError, "inventory changed"):
            admin.verify_installation(stack, self.target(), receipt)
        extra.unlink()
        jar.unlink()
        jar.symlink_to(self.cell / "external")
        with self.assertRaisesRegex(RuntimeError, "link or special file"):
            admin.verify_installation(stack, self.target(), receipt)

    def test_receipt_permissions_and_extra_directory_links_block(self):
        receipt = self.install()
        record = self.target() / "installation.json"
        record.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, "receipt is not private"):
            admin.verify_installation(stack, self.target(), receipt)
        record.chmod(0o600)
        extra = self.target() / "extra"
        extra.mkdir(mode=0o700)
        with self.assertRaisesRegex(RuntimeError, "unexpected directory"):
            admin.verify_installation(stack, self.target(), receipt)
        extra.rmdir()
        extra.symlink_to(self.cell, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "link or special file"):
            admin.verify_installation(stack, self.target(), receipt)

    def test_malformed_inspect_and_corrupted_receipt_are_normalized_failures(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(admin.shutil, "which", return_value="/trusted/tool"):
            for output in [b"not-json", b"{}", b"[7]", b'[{"Id":7,"RepoDigests":[]}]']:
                with self.subTest(output=output), self.assertRaises(RuntimeError):
                    admin.install(stack, self.cell, "cell-test", self.summary, self.config, UPDATE,
                                  lambda argv: output if "inspect" in argv else self.run_tool(argv))
        self.install()
        (self.target() / "installation.json").write_text("not-json")
        with self.assertRaisesRegex(RuntimeError, "input validation failed"):
            self.install()

    def test_approved_configuration_is_applied_and_cannot_be_overridden(self):
        self.assertEqual(admin.command_prefix(self.config, "core", ["cell", "status"]), ["--endpoint", "https://core.invalid"])
        self.assertEqual(admin.command_prefix(self.config, "kfe", ["kfe", "maintenance", "status"]), ["--kfe-endpoint", "https://kfe.invalid"])
        for arguments in [["--endpoint", "https://other.invalid"], ["--endpoint=https://other.invalid"],
                          ["--profile", "unapproved"], ["--kfe-endpoint=https://other.invalid"]]:
            with self.subTest(arguments=arguments), self.assertRaisesRegex(RuntimeError, "overrides"):
                admin.command_prefix(self.config, "core", arguments)
        with self.assertRaisesRegex(RuntimeError, "argument files"):
            admin.command_prefix(self.config, "core", ["@/unapproved/arguments"])
        with self.assertRaisesRegex(RuntimeError, "not configured"):
            admin.command_prefix({"apiBaseUrl": "https://core.invalid"}, "kfe", [])
        for config in [{}, {"apiBaseUrl": "http://core.invalid"}, {"apiBaseUrl": "https://user:secret@core.invalid"},
                       {"apiBaseUrl": "https://core.invalid/api"}, {"apiBaseUrl": "https://core.invalid?token=secret"},
                       {"apiBaseUrl": "https://core.invalid", "unknown": "value"}, {"apiBaseUrl": "https://core.invalid:99999"}]:
            with self.subTest(config=config), self.assertRaises(RuntimeError):
                admin.validate_config(config)

    def test_changed_config_is_not_reused_even_if_image_matches(self):
        self.install()
        self.config = {"apiBaseUrl": "https://other.invalid"}
        with self.assertRaisesRegex(RuntimeError, "another release"):
            self.install()

    def test_protected_directories_and_install_lock(self):
        self.cell.chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, "owner-only"):
            self.install()
        self.cell.chmod(0o700)
        linked = self.cell / "linked"
        linked.symlink_to(self.cell, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlinks"):
            admin.private_directory(linked)
        lock = os.open(self.cell / "admin-install.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.install()
        finally:
            os.close(lock)

    def test_java_version_and_remote_engine_override_block(self):
        for version in [b"openjdk 17.0.1\n", b"unknown"]:
            with self.subTest(version=version), patch.dict(os.environ, {}, clear=True), patch.object(admin.shutil, "which", return_value="/trusted/java"):
                with self.assertRaisesRegex(RuntimeError, "Java 21"):
                    admin.require_java(lambda _: version)
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://remote.invalid:2375"}, clear=True), patch.object(admin.shutil, "which", return_value="/trusted/tool"):
            with self.assertRaisesRegex(RuntimeError, "without overrides"):
                admin.install(stack, self.cell, "cell-test", self.summary, self.config, UPDATE, self.run_tool)

    def journal(self, receipt, **changes):
        state_dir = self.cell / "state"
        state_dir.mkdir(mode=0o700, exist_ok=True)
        state = {"schema": "kerosene.stack.update-state/v1", "status": "committed", "environment": "staging-cell", "updateId": UPDATE,
                 "events": [{"phase": "admin-installed", "evidence": receipt}]}
        state.update(changes)
        stack.atomic_write_json(str(state_dir / "update-state.json"), state, mode=0o600)
        return state_dir

    def launch(self, arguments):
        with patch.object(stack.lifecycle, "load_config", return_value={"cellId": "cell-test", "environment": "staging-cell"}), patch.object(stack.lifecycle, "verify_bootstrap_trust"), patch.object(admin, "require_java"):
            return stack.lifecycle.command_admin(stack, SimpleNamespace(cell_dir=str(self.cell), state_dir=None, target="core", admin_args=arguments))

    def test_launch_uses_committed_installation_and_holds_shared_update_lock(self):
        receipt = self.install()
        state_dir = self.journal(receipt)
        def execute(argv, **kwargs):
            self.assertEqual(argv, [str(self.target() / "bin/kerosene-jctl"), "--endpoint", "https://core.invalid", "cell", "status"])
            self.assertTrue((state_dir / "update.lock").exists())
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["env"]["KEROSENE_ENVIRONMENT"], "production")
            return SimpleNamespace(returncode=4)
        with patch.object(stack.lifecycle.subprocess, "run", side_effect=execute):
            self.assertEqual(self.launch(["--", "cell", "status"]), 4)
        self.assertFalse((state_dir / "update.lock").exists())

    def test_incomplete_foreign_or_ambiguous_journal_never_launches(self):
        receipt = self.install()
        changes = [{"status": "failed"}, {"status": "rollout-started"}, {"status": "dry-run-passed"},
                   {"manualRecoveryRequired": True}, {"environment": "another-cell"},
                   {"updateId": "sha256:" + "f" * 64}, {"events": []},
                   {"events": [{"phase": "admin-installed", "evidence": receipt}] * 2}]
        for change in changes:
            with self.subTest(change=change):
                self.journal(receipt, **change)
                with patch.object(stack.lifecycle.subprocess, "run") as execute, self.assertRaises(stack.ApplyBlockedError):
                    self.launch(["--version"])
                execute.assert_not_called()
                self.assertFalse((self.cell / "state/update.lock").exists())

    def test_abandoned_update_lock_and_changed_bytes_block_launch(self):
        receipt = self.install()
        state_dir = self.journal(receipt)
        lock = state_dir / "update.lock"
        lock.write_text("synthetic-abandoned-lock")
        with self.assertRaisesRegex(stack.ApplyBlockedError, "abandoned"):
            self.launch(["--version"])
        self.assertTrue(lock.exists())
        lock.unlink()
        (self.target() / "bin/kerosene-jctl").write_text("tampered")
        with self.assertRaisesRegex(stack.ApplyBlockedError, "digest changed"):
            self.launch(["--version"])

    def test_alternate_committed_journal_cannot_override_failed_cell_state(self):
        receipt = self.install()
        self.journal(receipt, status="failed", manualRecoveryRequired=True)
        alternate = self.cell / "old-state"
        alternate.mkdir(mode=0o700)
        stack.atomic_write_json(str(alternate / "update-state.json"), {"status": "committed", "events": [{"phase": "admin-installed", "evidence": receipt}]}, mode=0o600)
        args = SimpleNamespace(cell_dir=str(self.cell), state_dir=str(alternate), target="core", admin_args=["--version"])
        with patch.object(stack.lifecycle, "load_config", return_value={"cellId": "cell-test", "environment": "staging-cell"}), patch.object(stack.lifecycle, "verify_bootstrap_trust"), patch.object(stack.lifecycle.subprocess, "run") as execute:
            with self.assertRaisesRegex(stack.ApplyBlockedError, "alternate journals"):
                stack.lifecycle.command_admin(stack, args)
            execute.assert_not_called()
        # Apply must reject the same alternate directory before acquiring any
        # update lock or mutating trust/journal state.
        apply_args = SimpleNamespace(confirm_release="unit", environment="staging-cell", state_dir=str(alternate), tuf_state_dir=str(alternate), cell_dir=str(self.cell))
        with patch.object(stack, "apply_locked_release") as apply:
            self.assertEqual(stack.apply_release({}, {"releaseId": "unit"}, apply_args), stack.EXIT_CANNOT_APPLY)
            apply.assert_not_called()
        self.assertFalse((alternate / "update.lock").exists())

    def test_parser_preserves_operator_arguments_after_separator(self):
        args = stack.build_parser().parse_args(["admin", "--cell-dir", str(self.cell), "--", "--output", "json", "cell", "status"])
        self.assertEqual(args.admin_args, ["--", "--output", "json", "cell", "status"])

    def test_bounded_transfer_real_process_success_failure_and_size_limit(self):
        self.assertEqual(admin.capture_archive([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'archive')"]), b"archive")
        with self.assertRaisesRegex(RuntimeError, "transfer failed"):
            admin.capture_archive([sys.executable, "-c", "raise SystemExit(3)"])
        with patch.object(admin, "MAX_ARCHIVE", 10), self.assertRaisesRegex(RuntimeError, "transfer limit"):
            admin.capture_archive([sys.executable, "-c", "print('x'*100)"])

    def test_transfer_timeout_kills_real_child(self):
        with patch.object(admin.time, "monotonic", side_effect=[0, 61]), self.assertRaisesRegex(RuntimeError, "timed out"):
            admin.capture_archive([sys.executable, "-c", "import time; time.sleep(5)"])

    def test_installer_validation_failure_is_recorded_as_failed_update(self):
        state_dir = self.cell / "state"
        state_dir.mkdir(mode=0o700)
        args = stack.build_parser().parse_args(["update", "--release", "unit.json", "--apply", "--environment", "staging-cell",
                                               "--state-dir", str(state_dir), "--tuf-state-dir", str(state_dir),
                                               "--deployment-manifest", "unit-deployment.json"])
        for name in ["consensus_proof", "validator_roster", "vault_roster", "vault_compatibility_attestation",
                     "bank_observer_report", "snapshot_attestation_request", "snapshot_receipt", "snapshot_provider_key"]:
            setattr(args, name, "unit-evidence-not-authority")
        summary = {"releaseSchemaVersion": 3, "releaseId": "unit-release", "sequence": 1, "_releaseBytes": b"{}", **self.summary}
        def rejected_inspection(*_):
            with patch.dict(os.environ, {}, clear=True), patch.object(admin.shutil, "which", return_value="/trusted/tool"):
                return admin.install(stack, self.cell, "cell-test", summary, self.config, UPDATE,
                                     lambda argv: b"not-json" if "inspect" in argv else self.run_tool(argv))
        # Only exercise failure journaling: all authorization/external gates
        # are mocks, not valid evidence or a way to run the installed CLI.
        with ExitStack() as mocks:
            for name in ["verify_deployment", "require_execution_capabilities", "verify_maintenance", "verify_recovery_plan"]:
                mocks.enter_context(patch.object(stack.lifecycle, name))
            mocks.enter_context(patch.object(stack, "verify_tuf_authorization", return_value={}))
            mocks.enter_context(patch.object(stack, "verify_consensus_authorization", return_value={}))
            mocks.enter_context(patch.object(stack, "verify_vault_compatibility_attestation", return_value={}))
            mocks.enter_context(patch.object(stack, "verify_bank_observer_report", return_value={}))
            mocks.enter_context(patch.object(stack, "validate_snapshot_receipt", return_value={}))
            mocks.enter_context(patch.object(stack, "persist_tuf_state", return_value="unit-state"))
            mocks.enter_context(patch.object(stack, "run_staging_cell_deploy", side_effect=rejected_inspection))
            self.assertEqual(stack.apply_locked_release({}, summary, args), stack.EXIT_CANNOT_APPLY)
        state = stack.read_existing_update_state(str(state_dir))
        self.assertEqual(state["status"], "failed")
        self.assertTrue(state["manualRecoveryRequired"])
        self.assertEqual(state["phase"], "failed")
        self.assertEqual(state["failure"], "Admin installation input validation failed")
        self.assertFalse(self.target().exists())

    @unittest.skipUnless(os.environ.get("JCTL_ADMIN_INSTALL_TEST_DIST"), "explicit built Admin distribution required")
    def test_actual_built_admin_distribution_installs_and_runs_help(self):
        distribution = Path(os.environ["JCTL_ADMIN_INSTALL_TEST_DIST"])
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as handle:
            handle.add(distribution, arcname="kerosene-jctl")
        receipt = self.install(output.getvalue())
        self.journal(receipt)
        with patch.object(stack.lifecycle, "load_config", return_value={"cellId": "cell-test", "environment": "staging-cell"}), patch.object(stack.lifecycle, "verify_bootstrap_trust"):
            result = stack.lifecycle.command_admin(stack, SimpleNamespace(cell_dir=str(self.cell), state_dir=None, target="core", admin_args=["--", "--help"]))
            self.assertEqual(result, 0)

    @unittest.skipUnless(os.environ.get("JCTL_ADMIN_INSTALL_TEST_DIST"), "explicit built Admin distribution required")
    def test_actual_built_admin_distribution_installs_from_offline_oci(self):
        distribution = Path(os.environ["JCTL_ADMIN_INSTALL_TEST_DIST"])
        entries = [("opt/kerosene-jctl/" + str(path.relative_to(distribution)), path.read_bytes())
                   for path in sorted(distribution.rglob("*")) if path.is_file()]
        oci = self.cell / "actual-admin-image.oci.tar"
        self.summary["services"]["admin"]["image"] = oci_layout_archive(oci, entries, compressed=True)
        with patch.dict(os.environ, {}, clear=True), patch.object(admin.shutil, "which", side_effect=lambda name: "/trusted/" + name):
            receipt = admin.install(stack, self.cell, "cell-test", self.summary, self.config, UPDATE,
                                    self.run_tool, str(oci))
        launcher = admin.verify_installation(stack, self.target(), receipt)
        completed = subprocess.run([str(launcher), "--help"], capture_output=True, check=False, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.journal(receipt)
        with patch.object(stack.lifecycle, "load_config", return_value={"cellId": "cell-test", "environment": "staging-cell"}), patch.object(stack.lifecycle, "verify_bootstrap_trust"):
            result = stack.lifecycle.command_admin(stack, SimpleNamespace(cell_dir=str(self.cell), state_dir=None, target="kfe", admin_args=["--", "kfe", "maintenance", "--help"]))
        self.assertEqual(result, 0)
        self.assertEqual(len(receipt["files"]), len([path for path in distribution.rglob("*") if path.is_file()]))


if __name__ == "__main__":
    unittest.main()
