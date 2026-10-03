#!/usr/bin/env python3
"""Render-only database wiring contract; no cluster access or secret values."""
from pathlib import Path
import importlib.machinery
import importlib.util
import subprocess
import sys
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def render(overlay):
    return subprocess.run(["kubectl", "kustomize", str(ROOT / "overlays" / overlay)],
                          check=True, capture_output=True, text=True, timeout=30).stdout


def resources(overlay):
    result = {}
    for resource in yaml.safe_load_all(render(overlay)):
        key = (resource["kind"], resource["metadata"].get("namespace"), resource["metadata"]["name"])
        if key in result:
            raise AssertionError("duplicate rendered resource")
        # Strategic merge keys env by name; list ordering is not configuration.
        for container in resource.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []):
            if "env" in container:
                names = [entry["name"] for entry in container["env"]]
                if len(names) != len(set(names)):
                    raise AssertionError("duplicate env variables")
                container["env"].sort(key=lambda entry: entry["name"])
        result[key] = resource
    return result


class SeparateDatabasesTest(unittest.TestCase):
    def test_render_changes_only_service_bindings_and_postgres_bootstrap_credentials(self):
        baseline = resources("staging")
        separated = resources("staging-separated-databases")
        variables = {"SPRING_DATASOURCE_URL", "SPRING_DATASOURCE_USERNAME", "SPRING_DATASOURCE_PASSWORD"}
        for name, secret in (("server", "kerosene-core-db-secrets"), ("kfe-service", "kerosene-kfe-db-secrets")):
            workload = baseline[("Deployment", "kerosene-staging", name)]
            container = next(c for c in workload["spec"]["template"]["spec"]["containers"] if c["name"] == name)
            changed = set()
            for env in container["env"]:
                if env["name"] in variables:
                    reference = env["valueFrom"]["secretKeyRef"]
                    self.assertEqual(reference["name"], "kerosene-db-secrets")
                    reference["name"] = secret
                    changed.add(env["name"])
            self.assertEqual(changed, variables)
        postgres = baseline[("StatefulSet", "kerosene-staging", "staging-postgres")]
        container = next(c for c in postgres["spec"]["template"]["spec"]["containers"] if c["name"] == "postgres")
        changed = set()
        for env in container["env"]:
            if env["name"] in ("POSTGRES_USER", "POSTGRES_PASSWORD"):
                reference = env["valueFrom"]["secretKeyRef"]
                self.assertEqual(reference["name"], "kerosene-db-secrets")
                reference["name"] = "kerosene-postgres-bootstrap"
                reference["key"] = "bootstrap-user" if env["name"] == "POSTGRES_USER" else "bootstrap-password"
                changed.add(env["name"])
        self.assertEqual(changed, {"POSTGRES_USER", "POSTGRES_PASSWORD"})
        self.assertEqual(separated, baseline)
        self.assertFalse(any(key[0] == "Secret" for key in separated))

    def test_component_matches_by_container_and_env_names_not_array_indexes(self):
        path = ROOT / "components" / "separate-service-databases" / "database-bindings.yaml"
        patches = list(yaml.safe_load_all(path.read_text()))
        self.assertEqual({patch["metadata"]["name"] for patch in patches}, {"server", "kfe-service"})
        for patch in patches:
            containers = patch["spec"]["template"]["spec"]["containers"]
            self.assertEqual(len(containers), 1)
            self.assertEqual(containers[0]["name"], patch["metadata"]["name"])
            self.assertEqual(len(containers[0]["env"]), 3)
            self.assertTrue(all("value" not in env for env in containers[0]["env"]))

    def test_controller_preflight_inventories_both_database_secrets(self):
        loader = importlib.machinery.SourceFileLoader("separated_stack", str(ROOT.parent / "kerosene-stack"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        stack = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = stack
        loader.exec_module(stack)
        manifest = {"resources": list(resources("staging-separated-databases").values())}
        references = stack.lifecycle.required_secret_references(stack, manifest)
        required = {"jdbc-url", "application-user", "application-password"}
        self.assertEqual(references[("kerosene-staging", "kerosene-core-db-secrets")], required)
        self.assertEqual(references[("kerosene-staging", "kerosene-kfe-db-secrets")], required)
        self.assertEqual(references[("kerosene-staging", "kerosene-postgres-bootstrap")],
                         {"bootstrap-user", "bootstrap-password"})
        self.assertNotIn(("kerosene-staging", "kerosene-db-secrets"), references)


if __name__ == "__main__":
    unittest.main()
