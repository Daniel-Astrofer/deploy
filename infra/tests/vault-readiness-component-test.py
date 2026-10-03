#!/usr/bin/env python3
"""Render-only readiness contract, never image/runtime qualification."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class VaultReadinessComponentTest(unittest.TestCase):
    def render(self, target):
        result = subprocess.run(["kustomize", "build", str(target)], check=True, capture_output=True, timeout=30)
        return list(yaml.safe_load_all(result.stdout))

    def test_component_changes_only_vault_readiness_and_explicit_url_reference(self):
        base = ROOT / "kubernetes/overlays/staging-vault"
        original = self.render(base)
        with tempfile.TemporaryDirectory(dir=ROOT / "kubernetes/overlays", prefix="probe-render-") as temporary:
            directory = Path(temporary)
            (directory / "kustomization.yaml").write_text(
                "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
                "resources: [../staging-vault]\ncomponents: [../../components/vault-authenticated-readiness]\n")
            changed = self.render(directory)
        self.assertEqual(len(original), len(changed))
        for old, new in zip(original, changed):
            if old["kind"] == "Deployment" and old["metadata"]["name"] == "vault":
                container = new["spec"]["template"]["spec"]["containers"][0]
                probe = container["readinessProbe"]
                self.assertNotIn("httpGet", probe)
                self.assertNotIn("tcpSocket", probe)
                self.assertEqual(probe["exec"]["command"], ["/usr/local/bin/kerosene-vault", "--health-probe"])
                self.assertGreater(probe["timeoutSeconds"], 4)
                entry = next(e for e in container["env"] if e["name"] == "VAULT_HEALTH_PROBE_URL")
                self.assertEqual(entry["valueFrom"]["configMapKeyRef"], {"name": "vault-health-probe", "key": "local-health-url", "optional": False})
                container["env"].remove(entry)
                container["readinessProbe"] = old["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]
            self.assertEqual(old, new)


if __name__ == "__main__":
    unittest.main()
