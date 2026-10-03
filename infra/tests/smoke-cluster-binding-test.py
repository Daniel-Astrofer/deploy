#!/usr/bin/env python3
"""Execute shell binding parser; no Kubernetes/API or real credentials."""
import os
from pathlib import Path
import subprocess
import unittest

HELPER = Path(__file__).resolve().parents[1] / "kubernetes/scripts/smoke-cluster-binding.sh"


class BindingTest(unittest.TestCase):
    def invoke(self, arguments, overrides=None):
        env = {"PATH": os.environ["PATH"], **(overrides or {})}
        # Positional values are passed as arguments, never interpolated into code.
        return subprocess.run(["bash", "-c", 'helper="$1"; shift; source "$helper" "$@"; printf "%s\\0" "${KUBECTL_COMMAND[@]}"', "test", str(HELPER), *arguments],
                              env=env, capture_output=True, timeout=5)

    def test_explicit_binding_preserves_spaces_and_shell_metacharacters(self):
        result = self.invoke(["--cell-binding", "/tool path/kubectl", "/private path/cell.conf", "cell;$(false)"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(b"\0")[:-1],
                         [b"/tool path/kubectl", b"--kubeconfig", b"/private path/cell.conf", b"--context", b"cell;$(false)", b"--request-timeout=30s"])

    def test_incomplete_or_invalid_binding_never_falls_back(self):
        for args in [["--cell-binding"], ["--cell-binding", "kubectl", "/cell.conf", "ctx"],
                     ["--cell-binding", "/kubectl", "relative.conf", "ctx"],
                     ["--cell-binding", "/kubectl", "/cell.conf", ""],
                     ["--unknown", "/kubectl", "/cell.conf", "ctx"]]:
            with self.subTest(args=args):
                result = self.invoke(args)
                self.assertEqual(result.returncode, 64)
                self.assertEqual(result.stdout, b"")

    def test_bound_path_rejects_ambient_overrides(self):
        for name in ["KUBECTL", "KEROSENE_STAGING_NAMESPACE", "KEROSENE_STAGING_VAULT_NAMESPACE",
                     "KEROSENE_STAGING_LOGIN_PORT", "KEROSENE_STAGING_VAULT_SMOKE_PORT"]:
            with self.subTest(name=name):
                result = self.invoke(["--cell-binding", "/kubectl", "/cell.conf", "ctx"], {name: "unapproved"})
                self.assertEqual(result.returncode, 64)
                self.assertEqual(result.stdout, b"")

    def test_legacy_manual_default_remains_compatible(self):
        self.assertEqual(self.invoke([]).stdout, b"kubectl\0")
        self.assertEqual(self.invoke([], {"KUBECTL": "/manual/tool"}).stdout, b"/manual/tool\0")


if __name__ == "__main__":
    unittest.main()
