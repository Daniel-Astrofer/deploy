#!/usr/bin/env python3
"""Real local/offline release data tests; uses only disposable synthetic inputs."""

from dataclasses import replace
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

STACK = Path(__file__).resolve().parents[1] / "stack"
sys.dont_write_bytecode = True
sys.path.insert(0, str(STACK))
import archive

spec = importlib.util.spec_from_file_location("package_release", STACK / "package-release.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def tar_bytes(members, *, compressed=False):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as stream:
        for name, content, kind in members:
            header = tarfile.TarInfo(name)
            header.type = kind
            header.mode = 0o777
            header.mtime = 0
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                header.linkname = "../../outside"
            header.size = len(content)
            stream.addfile(header, io.BytesIO(content))
    data = buffer.getvalue()
    return gzip.compress(data, mtime=0) if compressed else data


class LocalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kerosene-archive-test-")
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"

    def tearDown(self):
        self.temp.cleanup()

    def store(self, data, mirror="mirror"):
        digest = archive.bytes_digest(data)
        path = self.root / mirror / "sha256" / digest[7:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return digest, len(data), (self.root / mirror).as_uri()

    def extract(self, data, **options):
        path = self.root / "input.tar"
        path.write_bytes(data)
        return archive.extract(path, self.root / "installed", archive.bytes_digest(data), len(data), **options)

    def assert_clean_failure(self, data, **options):
        with self.assertRaises((archive.ArchiveError, OSError)):
            self.extract(data, **options)
        self.assertFalse((self.root / "installed").exists())
        self.assertEqual(list(self.root.glob(".extract-*")), [])


class CacheTest(LocalTest):
    def test_real_offline_import_cache_and_data_only_install(self):
        data = tar_bytes([("deployment.json", b"{}", tarfile.REGTYPE),
                          ("postinstall.sh", b"touch /NEVER-RUN\n", tarfile.REGTYPE)])
        digest, size, mirror = self.store(data)
        path = archive.fetch(self.cache, digest, size, [mirror], allow_file=True)
        self.assertEqual(path, self.cache / "sha256" / digest[7:])
        shutil.rmtree(self.root / "mirror")
        with patch.object(archive, "_mirror_stream", side_effect=AssertionError("unexpected transport")):
            result = archive.install(self.cache, self.root / "installed", digest, size, offline=True)
        self.assertEqual((result / "deployment.json").read_bytes(), b"{}")
        self.assertEqual((result / "postinstall.sh").stat().st_mode & 0o777, 0o444)
        self.assertFalse((result / "postinstall.sh").stat().st_mode & 0o111)

    def test_corrupt_mirror_fallback_and_tampered_cache_repair(self):
        digest, size, good = self.store(b"correct-data", "good")
        corrupt = self.root / "bad" / "sha256" / digest[7:]
        corrupt.parent.mkdir(parents=True)
        corrupt.write_bytes(b"wrong---data")
        missing = (self.root / "missing").as_uri()
        path = archive.fetch(self.cache, digest, size,
                             [missing, (self.root / "bad").as_uri(), good], allow_file=True)
        self.assertEqual(path.read_bytes(), b"correct-data")
        path.chmod(0o600)
        path.write_bytes(b"tamper")
        with patch.object(archive, "_mirror_stream", side_effect=AssertionError("unexpected transport")):
            with self.assertRaisesRegex(archive.ArchiveError, "offline.*corrupt"):
                archive.fetch(self.cache, digest, size, offline=True)
        self.assertEqual(archive.fetch(self.cache, digest, size, [good], allow_file=True).read_bytes(), b"correct-data")
        self.assertEqual(list(path.parent.glob(".download-*")), [])

    def test_missing_offline_fails_and_wrong_size_never_caches(self):
        digest, size, mirror = self.store(b"12345")
        with patch.object(archive, "_mirror_stream", side_effect=AssertionError("unexpected transport")):
            with self.assertRaisesRegex(archive.ArchiveError, "offline.*missing"):
                archive.install(self.cache, self.root / "installed", digest, size, offline=True)
        for wrong_size in (size - 1, size + 1):
            with self.assertRaises(archive.ArchiveError):
                archive.fetch(self.cache, digest, wrong_size, [mirror], allow_file=True)
        self.assertFalse((self.cache / "sha256" / digest[7:]).exists())
        self.assertFalse((self.root / "installed").exists())

    def test_transport_and_digest_policy(self):
        digest = "sha256:" + "a" * 64
        for mirror in ("http://example.invalid", "ftp://example.invalid", "file:///tmp",
                       "https://user:pass@example.invalid", "https://example.invalid/?q=1",
                       "https://example.invalid/#fragment", "https://example.invalid/\n"):
            with self.subTest(mirror=mirror), self.assertRaises(archive.ArchiveError):
                archive.fetch(self.cache, digest, 1, [mirror])
        with self.assertRaises(archive.ArchiveError):
            archive.fetch(self.cache, digest, 1, ["https://example.invalid"], allow_file=True)
        for invalid in ("a" * 64, "sha256:" + "A" * 64, "../../bad", None):
            with self.assertRaises(archive.ArchiveError):
                archive.fetch(self.cache, invalid, 1, offline=True)
        with self.assertRaises(archive.ArchiveError):
            archive._NoRedirect().redirect_request(None, None, 302, None, None, "http://example.invalid")

    def test_limits_and_symlinks(self):
        digest, size, mirror = self.store(b"12345")
        with self.assertRaises(archive.ArchiveError):
            archive.fetch(self.cache, digest, size, [mirror], allow_file=True,
                          limits=replace(archive.DEFAULT_LIMITS, max_archive_bytes=4))
        self.cache.mkdir(mode=0o700)
        namespace = self.cache / "sha256"
        namespace.mkdir(mode=0o700)
        outside = self.root / "outside"
        outside.write_bytes(b"untouched")
        (namespace / digest[7:]).symlink_to(outside)
        with self.assertRaises(archive.ArchiveError):
            archive.fetch(self.cache, digest, size, offline=True)
        archive.fetch(self.cache, digest, size, [mirror], allow_file=True)
        self.assertEqual(outside.read_bytes(), b"untouched")
        (namespace / (digest[7:] + ".lock")).unlink()
        (namespace / (digest[7:] + ".lock")).symlink_to(outside)
        with self.assertRaises(OSError):
            archive.fetch(self.cache, digest, size, offline=True)

    def test_cli_offline_missing_is_failure(self):
        result = subprocess.run([sys.executable, str(STACK / "archive.py"), "install",
                                 "--cache", str(self.cache), "--digest", "sha256:" + "0" * 64,
                                 "--size", "12", "--offline", "--destination", str(self.root / "installed")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("offline cache object missing or corrupt", result.stderr)


class ExtractTest(LocalTest):
    def test_traversal_and_noncanonical_paths(self):
        for name in ("../escape", "/absolute", "a/../../escape", "a/./b", "a//b", "C:/escape", "a\\b", "a\nb"):
            with self.subTest(name=name):
                self.assert_clean_failure(tar_bytes([(name, b"payload", tarfile.REGTYPE)]))
        self.assertFalse((self.root.parent / "escape").exists())

    def test_links_devices_extensions_sparse_and_duplicate_members(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE,
                     tarfile.BLKTYPE, tarfile.GNUTYPE_SPARSE, tarfile.XHDTYPE,
                     tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            with self.subTest(kind=kind):
                self.assert_clean_failure(tar_bytes([("evil", b"", kind)]))
        self.assert_clean_failure(tar_bytes([("duplicate", b"one", tarfile.REGTYPE),
                                            ("duplicate", b"two", tarfile.REGTYPE)]))
        self.assert_clean_failure(tar_bytes([("parent", b"file", tarfile.REGTYPE),
                                            ("parent/child", b"file", tarfile.REGTYPE)]))

    def test_file_members_path_depth_and_expansion_limits(self):
        single = tar_bytes([("safe", b"12345", tarfile.REGTYPE)])
        self.assert_clean_failure(single, limits=replace(archive.DEFAULT_LIMITS, max_file_bytes=4))
        two = tar_bytes([("one", b"1", tarfile.REGTYPE), ("two", b"2", tarfile.REGTYPE)])
        self.assert_clean_failure(two, limits=replace(archive.DEFAULT_LIMITS, max_members=1))
        self.assert_clean_failure(single, limits=replace(archive.DEFAULT_LIMITS, max_expanded_bytes=513))
        deep = tar_bytes([("a/b/c", b"x", tarfile.REGTYPE)])
        self.assert_clean_failure(deep, limits=replace(archive.DEFAULT_LIMITS, max_depth=2))
        self.assert_clean_failure(deep, limits=replace(archive.DEFAULT_LIMITS, max_path_bytes=4))
        self.assert_clean_failure(deep, limits=replace(archive.DEFAULT_LIMITS, max_members=2))
        bomb = tar_bytes([("bomb", b"0" * (1024 * 1024), tarfile.REGTYPE)], compressed=True)
        self.assert_clean_failure(bomb, limits=replace(archive.DEFAULT_LIMITS, max_ratio=2))

    def test_valid_gzip_integrity_truncation_and_trailing_data(self):
        data = tar_bytes([("nested/file", b"data", tarfile.REGTYPE)], compressed=True)
        result = self.extract(data)
        self.assertEqual((result / "nested/file").read_bytes(), b"data")
        shutil.rmtree(result)
        self.assert_clean_failure(data[:-6])
        plain = tar_bytes([("nested/file", b"data", tarfile.REGTYPE)])
        self.assert_clean_failure(plain + b"nonzero trailing bytes")
        self.assert_clean_failure(plain[:700])
        damaged = bytearray(plain)
        damaged[0] ^= 1
        self.assert_clean_failure(bytes(damaged))

    def test_exact_digest_snapshot_and_existing_destination(self):
        path = self.root / "input"
        data = tar_bytes([("file", b"x", tarfile.REGTYPE)])
        path.write_bytes(data)
        with self.assertRaises(archive.ArchiveError):
            archive.extract(path, self.root / "installed", "sha256:" + "0" * 64, len(data))
        result = self.extract(data)
        with self.assertRaises(archive.ArchiveError):
            self.extract(data)
        self.assertEqual((result / "file").read_bytes(), b"x")
        staged = self.root / "staged"
        staged.mkdir()
        with self.assertRaises(FileExistsError):
            archive.publish_directory(staged, result)


class PackageTest(LocalTest):
    def setUp(self):
        super().setUp()
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.run_git("init", "--initial-branch=fixture")
        self.run_git("config", "user.name", "Synthetic Fixture")
        self.run_git("config", "user.email", "synthetic@example.invalid")
        (self.repository / "data.txt").write_text("synthetic selected source\n")
        (self.repository / "never-run.sh").write_text("#!/bin/sh\ntouch /NEVER-RUN\n")
        self.run_git("add", "data.txt", "never-run.sh")
        self.run_git("commit", "-m", "synthetic fixture")
        self.commit = self.run_git("rev-parse", "HEAD")
        self.image = "registry.example.invalid/admin@sha256:" + "a" * 64
        self.deployment = {"schema": package.DEPLOYMENT_SCHEMA, "environment": "staging-cell",
                           "resources": [{"apiVersion": "v1", "kind": "ConfigMap",
                                          "metadata": {"name": "synthetic", "namespace": "kerosene-staging"},
                                          "data": {"mode": "test"}}],
                           "admin": {"image": self.image, "config": {"apiBaseUrl": "https://synthetic-core.invalid"}}}
        # Noncanonical whitespace must be preserved exactly.
        raw = json.dumps(self.deployment, indent=2).encode() + b"\n"
        (self.root / "deployment.json").write_bytes(raw)
        sbom = archive.canonical_bytes({"spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT",
                                       "name": "Synthetic fixture", "dataLicense": "CC0-1.0", "packages": []})
        (self.root / "sbom.json").write_bytes(sbom)
        self.selection = {"schema": package.INPUT_SCHEMA,
                          "repositories": {"deploy": {"path": "repository", "commit": self.commit}},
                          "services": {"admin": {"image": self.image}},
                          "deployment": {"path": "deployment.json", "digest": archive.bytes_digest(raw)},
                          "sbom": {"path": "sbom.json", "digest": archive.bytes_digest(sbom)}}
        self.selection_path = self.root / "candidate-input.json"
        self.save_selection()

    def run_git(self, *args):
        env = package.git_env()
        env.update(GIT_AUTHOR_DATE="2000-01-01T00:00:00Z", GIT_COMMITTER_DATE="2000-01-01T00:00:00Z")
        return subprocess.run(["git", "-C", str(self.repository), *args], env=env,
                              check=True, capture_output=True, text=True).stdout.strip()

    def save_selection(self):
        self.selection_path.write_bytes(archive.canonical_bytes(self.selection))

    def assemble(self, output="candidate", **options):
        self.save_selection()
        return package.assemble(self.selection_path, self.root / output, **options)

    def test_reproducibility_exact_manifest_and_branch_independence(self):
        first = self.assemble("first", input_root=self.root)
        (self.repository / "data.txt").write_text("later branch tip\n")
        self.run_git("commit", "-am", "later commit must not enter candidate")
        (self.repository / "untracked-secret-synthetic.txt").write_text("never included\n")
        second = self.assemble("second", input_root=self.root)
        self.assertEqual(first, second)
        self.assertEqual((self.root / "first/candidate.tar").read_bytes(), (self.root / "second/candidate.tar").read_bytes())
        result = archive.extract(self.root / "first/candidate.tar", self.root / "installed",
                                 first["archive"]["digest"], first["archive"]["size"])
        self.assertEqual((result / "deployment.json").read_bytes(), (self.root / "deployment.json").read_bytes())
        self.assertEqual(first["schema"], "kerosene.deployment-bundle.v1")
        self.assertFalse(first["trusted"])
        self.assertEqual(first["canonicalRenderedManifestsDigest"], archive.bytes_digest(archive.canonical_bytes(self.deployment)))
        for entry in first["entries"]:
            self.assertEqual(archive.file_record(result / entry["path"], entry["path"]), entry)
        provenance = archive.strict_json((result / "provenance.json").read_bytes())
        self.assertFalse(provenance["predicate"]["signed"])
        self.assertNotIn(str(self.root), json.dumps(provenance))
        self.assertEqual(first["repositories"]["deploy"]["commit"], self.commit)
        # Existing full-history bundles reproduce identical output without the source tree.
        record = archive.file_record(result / "sources/deploy.bundle")
        self.selection["repositories"]["deploy"] = {"commit": self.commit,
            "bundle": {"path": "installed/sources/deploy.bundle", "digest": record["digest"], "size": record["size"]}}
        shutil.rmtree(self.repository)
        third = self.assemble("third", input_root=self.root)
        self.assertEqual(first, third)

    def test_full_commits_material_digests_and_pins_required(self):
        original = json.loads(json.dumps(self.selection))
        changes = [lambda s: s["repositories"]["deploy"].update(commit="HEAD"),
                   lambda s: s["repositories"]["deploy"].update(commit="0" * 40),
                   lambda s: s["repositories"]["deploy"].update(bundleDigest="sha256:" + "0" * 64),
                   lambda s: s["deployment"].update(digest="sha256:" + "0" * 64),
                   lambda s: s["sbom"].update(digest="sha256:" + "0" * 64),
                   lambda s: s["services"]["admin"].update(image="registry.example.invalid/admin:latest"),
                   lambda s: s.update(autoActivate=True)]
        for change in changes:
            self.selection = json.loads(json.dumps(original))
            change(self.selection)
            with self.assertRaises(archive.ArchiveError):
                self.assemble()
            self.assertFalse((self.root / "candidate").exists())
        self.assertEqual(list(self.root.glob(".candidate-*")), [])
        self.assertEqual(list(self.root.glob(".pack-work-*")), [])

    def test_source_hooks_not_run_and_existing_output_not_changed(self):
        marker = self.root / "hook-ran"
        hookdir = self.root / "hooks"
        hookdir.mkdir()
        for hook in ("reference-transaction", "post-checkout", "post-merge", "fsmonitor"):
            path = hookdir / hook
            path.write_text("#!/bin/sh\ntouch '" + str(marker) + "'\n")
            path.chmod(0o700)
        self.run_git("config", "core.hooksPath", str(hookdir))
        self.run_git("config", "core.fsmonitor", str(hookdir / "fsmonitor"))
        first = self.assemble()
        self.assertFalse(marker.exists())
        with self.assertRaises(archive.ArchiveError):
            self.assemble()
        self.assertEqual(archive.file_record(self.root / "candidate/candidate.tar")["digest"], first["archive"]["digest"])

    def test_input_confinement_and_json_ambiguity(self):
        with self.assertRaises(archive.ArchiveError):
            self.assemble(input_root=self.repository)
        for data in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'not json'):
            with self.assertRaises(archive.ArchiveError):
                archive.strict_json(data)
        self.assertEqual(archive.strict_json(b'{"finite":9.8}'), {"finite": 9.8})

    def test_installed_lifecycle_computes_and_checks_config_digest(self):
        first = self.assemble("computed")
        import lifecycle
        expected = lifecycle.digest(lifecycle.component_config(self.deployment, self.image, "admin"))
        self.assertEqual(first["services"]["admin"]["configDigest"], expected)
        self.assertEqual(first["configurationVerification"], "verified-installed-lifecycle")
        self.assertEqual(first["archive"]["type"], "file")
        self.assertEqual(first["status"], "unsigned-candidate")
        with tarfile.open(self.root / "computed/candidate.tar", "r:") as bundle:
            provenance = json.load(bundle.extractfile("provenance.json"))
        tools = provenance["predicate"]["tools"]
        self.assertEqual(tools["admin_install.py"], archive.file_record(STACK / "admin_install.py")["digest"])
        self.selection["services"]["admin"]["configDigest"] = expected
        self.assertEqual(first, self.assemble("verified"))
        self.selection["services"]["admin"]["configDigest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(archive.ArchiveError, "configuration digest mismatch"):
            self.assemble("rejected")

    def test_foreign_cached_controller_dependency_is_rejected_before_loading(self):
        for module in ["lifecycle", "admin_install"]:
            with self.subTest(module=module), patch.dict(sys.modules, {module: SimpleNamespace(__file__="/foreign/candidate/" + module + ".py")}), patch.object(package.importlib.machinery.SourceFileLoader, "exec_module") as load:
                with self.assertRaisesRegex(archive.ArchiveError, "cached controller dependency"):
                    self.assemble("foreign-" + module)
                load.assert_not_called()
                self.assertFalse((self.root / ("foreign-" + module)).exists())

    def test_controller_dependency_change_during_validation_rejects_candidate(self):
        original = package.file_record
        reads = 0
        def changing(path, *args, **kwargs):
            nonlocal reads
            record = original(path, *args, **kwargs)
            if Path(path) == STACK / "admin_install.py":
                reads += 1
                if reads > 1:
                    record = {**record, "digest": "sha256:" + "0" * 64}
            return record
        with patch.object(package, "file_record", side_effect=changing), self.assertRaisesRegex(archive.ArchiveError, "changed during"):
            self.assemble("changed-controller")
        self.assertFalse((self.root / "changed-controller").exists())

    def test_installed_lifecycle_rejects_unsafe_candidate_resources(self):
        for kind in ("Secret", "ClusterRole", "Job"):
            self.deployment["resources"][0]["kind"] = kind
            raw = archive.canonical_bytes(self.deployment)
            (self.root / "deployment.json").write_bytes(raw)
            self.selection["deployment"]["digest"] = archive.bytes_digest(raw)
            with self.assertRaisesRegex(archive.ArchiveError, "cannot create"):
                self.assemble()

    def test_shared_lifecycle_runtime_and_init_images(self):
        core_image = "registry.example.invalid/core@sha256:" + "c" * 64
        self.selection["services"]["core"] = {"image": core_image}
        workload = {"apiVersion": "apps/v1", "kind": "Deployment",
                    "metadata": {"name": "synthetic-core", "namespace": "kerosene-staging"},
                    "spec": {"replicas": 1, "template": {"spec": {
                        "containers": [{"name": "core", "image": core_image}],
                        "initContainers": [{"name": "init", "image": core_image}]}}}}
        self.deployment["resources"].append(workload)
        raw = archive.canonical_bytes(self.deployment)
        (self.root / "deployment.json").write_bytes(raw)
        self.selection["deployment"]["digest"] = archive.bytes_digest(raw)
        index = self.assemble("runtime")
        import lifecycle
        for name, service in index["services"].items():
            self.assertEqual(service["configDigest"],
                             lifecycle.digest(lifecycle.component_config(self.deployment, service["image"], name)))
        workload["spec"]["template"]["spec"]["initContainers"][0]["image"] = "evil.invalid/init:latest"
        raw = archive.canonical_bytes(self.deployment)
        (self.root / "deployment.json").write_bytes(raw)
        self.selection["deployment"]["digest"] = archive.bytes_digest(raw)
        with self.assertRaisesRegex(archive.ArchiveError, "not an approved service image"):
            self.assemble("unsafe-init")


if __name__ == "__main__":
    unittest.main(verbosity=2)
