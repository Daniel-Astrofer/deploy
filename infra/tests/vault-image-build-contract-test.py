#!/usr/bin/env python3
"""Static workspace build regression; does not qualify an OCI image."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VaultImageBuildContractTest(unittest.TestCase):
    def setUp(self):
        self.lines = (ROOT / "docker/images/kerosene-vault/Dockerfile").read_text().splitlines()

    def test_workspace_application_sources_are_copied(self):
        for source in ("src", "apps", "crates"):
            self.assertIn(f"COPY {source} ./{source}", self.lines)

    def test_every_build_selects_executable_package_and_locked_dependencies(self):
        builds = [line for line in self.lines if "cargo build " in line]
        self.assertEqual(len(builds), 2)
        for line in builds:
            self.assertIn("--locked", line)
            self.assertIn("-p kerosene-vault-app", line)
            self.assertIn("--bin kerosene-vault", line)
        self.assertTrue(any('--no-default-features --features "$CARGO_FEATURES"' in line for line in builds))
        self.assertTrue(any("--no-default-features" not in line for line in builds))


if __name__ == "__main__":
    unittest.main()
