#!/usr/bin/env python3
"""Approved initial database plan validation, never database execution."""
import copy
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("database_plan_fixture", str(ROOT / "tests/stack-lifecycle-test.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
fixture = importlib.util.module_from_spec(spec)
loader.exec_module(fixture)
stack, lifecycle = fixture.stack, fixture.lifecycle


class DatabasePlanTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.DeploymentTest(methodName="test_exact_configuration")
        self.fixture.setUp()
        self.artifact = self.fixture.artifact
        self.plan = {"schema": "kerosene.cell.initial-databases/v1", "mode": "initial",
                     "postgres": {"workload": {"kind": "Deployment", "name": "postgres", "container": "postgres"},
                                  "bootstrapSecret": {"name": "postgres-bootstrap", "usernameKey": "user", "passwordKey": "password"}},
                     "services": {}, "scriptDigests": {name: "sha256:" + hashlib.sha256(
                         (ROOT / "runtime/postgres" / name).read_bytes()).hexdigest() for name in lifecycle.DATABASE_SCRIPTS}}
        for name in ("core", "kfe"):
            reference = {"name": name + "-runtime", "urlKey": "jdbc-url", "usernameKey": "user", "passwordKey": "password"}
            self.plan["services"][name] = {"database": "cell_" + name, "migrationRole": name + "_migration", "runtimeRole": name + "_runtime",
                "workload": {"kind": "Deployment", "name": name, "container": name},
                "runtimeSecret": reference, "migrationSecret": dict(reference, name=name + "-migration")}
        for resource in self.artifact["resources"]:
            if resource["kind"] != "Deployment" or resource["metadata"]["name"] not in ("core", "kfe", "postgres"):
                continue
            name = resource["metadata"]["name"]
            reference = self.plan["postgres"]["bootstrapSecret"] if name == "postgres" else self.plan["services"][name]["runtimeSecret"]
            mappings = (("POSTGRES_USER", "usernameKey"), ("POSTGRES_PASSWORD", "passwordKey")) if name == "postgres" else (
                ("SPRING_DATASOURCE_URL", "urlKey"), ("SPRING_DATASOURCE_USERNAME", "usernameKey"), ("SPRING_DATASOURCE_PASSWORD", "passwordKey"))
            resource["spec"]["template"]["spec"]["containers"][0]["env"] = [
                {"name": variable, "valueFrom": {"secretKeyRef": {"name": reference["name"], "key": reference[key]}}} for variable, key in mappings]
        self.configmap = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": lifecycle.DATABASE_PLAN_NAME, "namespace": "kerosene-staging"}, "data": {}}
        self.artifact["resources"].append(self.configmap)

    def tearDown(self):
        self.fixture.tearDown()

    def refresh(self):
        self.configmap["data"] = {"plan.json": json.dumps(self.plan)}

    def verify(self):
        self.refresh()
        return lifecycle.initial_database_plan(stack, self.artifact, self.fixture.summary)

    def test_valid_plan_is_inert_and_binds_migration_and_runtime_secrets(self):
        self.assertEqual(self.verify(), self.plan)
        references = lifecycle.required_secret_references(stack, self.artifact)
        self.assertEqual(references[("kerosene-staging", "core-migration")], {"jdbc-url", "user", "password"})
        self.assertEqual(references[("kerosene-staging", "kfe-migration")], {"jdbc-url", "user", "password"})

    def test_full_manifest_validation_checks_plan_and_signed_configuration_digest(self):
        self.refresh()
        for name, service in self.fixture.summary["services"].items():
            value = lifecycle.digest(lifecycle.component_config(self.artifact, service["image"], name))
            service["configDigest"] = value
            self.fixture.release["services"][name]["configDigest"] = value
        self.assertEqual(self.fixture.verify(), self.artifact)
        self.plan["services"]["core"]["database"] = "cell_core_substitution"
        self.refresh()
        with self.assertRaisesRegex(stack.ApplyBlockedError, "configuration digest mismatch"):
            self.fixture.verify()

    def test_install_without_plan_cannot_write_even_if_other_capabilities_are_mocked(self):
        self.artifact["resources"].remove(self.configmap)
        args = SimpleNamespace(dry_run=False, cell_dir="/synthetic/cell", command="install")
        with patch.dict(lifecycle.os.environ, {}, clear=True), \
             patch.object(lifecycle, "require_execution_capabilities"), \
             patch.object(lifecycle, "load_config", return_value={"cellId": "synthetic"}), \
             patch.object(lifecycle, "verify_bootstrap_trust"), \
             patch.object(lifecycle, "kubectl_command", return_value=["/bound-kubectl"]), \
             patch.object(lifecycle, "verify_empty_installation"), \
             patch.object(lifecycle, "apply_resource") as apply, \
             patch.object(lifecycle.admin_install, "install") as admin, \
             patch.object(lifecycle, "run") as run:
            with self.assertRaisesRegex(stack.ApplyBlockedError, "requires an approved database plan"):
                lifecycle.execute(stack, self.artifact, self.fixture.summary, args, lambda *_: None)
            apply.assert_not_called()
            admin.assert_not_called()
            run.assert_not_called()

    def test_plan_bytes_change_every_component_configuration_digest(self):
        self.refresh()
        before = {name: lifecycle.digest(lifecycle.component_config(self.artifact, service["image"], name)) for name, service in self.fixture.summary["services"].items()}
        self.plan["services"]["core"]["database"] = "cell_core_other"
        self.refresh()
        self.assertTrue(all(before[name] != lifecycle.digest(lifecycle.component_config(self.artifact, service["image"], name)) for name, service in self.fixture.summary["services"].items()))

    def test_missing_plan_preserves_legacy_inspection_without_claiming_execution(self):
        self.artifact["resources"].remove(self.configmap)
        self.assertIsNone(lifecycle.initial_database_plan(stack, self.artifact))
        self.assertIn("migration-executor-and-tested-recovery-not-integrated", lifecycle.EXECUTION_BLOCKERS)

    def test_aliases_and_invalid_sql_names_rejected(self):
        original = copy.deepcopy(self.plan)
        mutations = [("database", self.plan["services"]["kfe"]["database"]), ("migrationRole", "core_runtime"),
                     ("database", "postgres"), ("database", "x;drop_database"), ("database", "x" * 64)]
        for key, value in mutations:
            self.plan = copy.deepcopy(original)
            self.plan["services"]["core"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(stack.ApplyBlockedError):
                self.verify()
        self.plan = copy.deepcopy(original)
        self.plan["services"]["core"]["migrationSecret"] = self.plan["services"]["core"]["runtimeSecret"]
        with self.assertRaises(stack.ApplyBlockedError):
            self.verify()

    def test_wrong_script_hash_and_unknown_executable_fields_rejected(self):
        self.plan["scriptDigests"][lifecycle.DATABASE_SCRIPTS[0]] = "sha256:" + "0" * 64
        with self.assertRaises(stack.ApplyBlockedError):
            self.verify()
        self.setUp_plan_hashes()
        self.plan["command"] = ["sh", "-c", "forbidden"]
        with self.assertRaises(stack.ApplyBlockedError):
            self.verify()

    def setUp_plan_hashes(self):
        self.plan["scriptDigests"] = {name: "sha256:" + hashlib.sha256((ROOT / "runtime/postgres" / name).read_bytes()).hexdigest() for name in lifecycle.DATABASE_SCRIPTS}

    def test_example_script_hashes_match_current_installed_primitives(self):
        example = json.loads((ROOT / "stack/examples/initial-database-plan.example.json").read_text())
        self.assertEqual(example["scriptDigests"], self.plan["scriptDigests"])

    def test_migration_jobs_are_inert_fixed_commands_with_only_migration_credentials(self):
        self.refresh()
        update = "sha256:" + "a" * 64
        with patch.object(lifecycle, "run") as run:
            jobs = lifecycle.initial_database_migration_jobs(stack, self.artifact, self.fixture.summary, update)
            run.assert_not_called()
        self.assertEqual(len(jobs), 2)
        for component, job in zip(("core", "kfe"), jobs):
            self.assertLessEqual(len(job["metadata"]["name"]), 63)
            self.assertEqual(job["metadata"]["annotations"]["kerosene.io/update-id"], update)
            self.assertEqual(job["spec"]["backoffLimit"], 0)
            self.assertNotIn("ttlSecondsAfterFinished", job["spec"])
            pod = job["spec"]["template"]["spec"]
            self.assertEqual(pod["restartPolicy"], "Never")
            self.assertIs(pod["automountServiceAccountToken"], False)
            self.assertNotIn("initContainers", pod)
            container = pod["containers"][0]
            self.assertEqual(container["image"], self.fixture.summary["services"][component]["image"])
            self.assertEqual(container["command"], ["java", "-XX:+ExitOnOutOfMemoryError", "-XX:MaxRAMPercentage=75.0", "-jar", "/app/app.jar"])
            self.assertEqual(container["args"], ["--cell-migration=migrate"])
            self.assertEqual(len(container["env"]), 3)
            self.assertTrue(all(entry["valueFrom"]["secretKeyRef"]["name"] == component + "-migration" for entry in container["env"]))
            self.assertNotIn("ports", container)
            self.assertNotIn("envFrom", container)
            self.assertIs(container["securityContext"]["readOnlyRootFilesystem"], True)
            self.assertEqual(job["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"], "cell-database-migration")

    def test_capabilities_jobs_have_no_credentials_and_short_deadline(self):
        self.refresh()
        update = "sha256:" + "a" * 64
        with patch.object(lifecycle, "run") as run:
            jobs = lifecycle.initial_database_migration_jobs(stack, self.artifact, self.fixture.summary, update, "capabilities")
            run.assert_not_called()
        migrate = lifecycle.initial_database_migration_jobs(stack, self.artifact, self.fixture.summary, update)
        for probe, migration in zip(jobs, migrate):
            self.assertLessEqual(len(probe["metadata"]["name"]), 63)
            self.assertNotEqual(probe["metadata"]["name"], migration["metadata"]["name"])
            self.assertEqual(probe["spec"]["activeDeadlineSeconds"], 30)
            pod = probe["spec"]["template"]["spec"]
            self.assertIs(pod["automountServiceAccountToken"], False)
            container = pod["containers"][0]
            self.assertEqual(container["args"], ["--cell-migration=capabilities"])
            self.assertEqual(container["env"], [])
            self.assertNotIn("envFrom", container)
            self.assertTrue(all("emptyDir" in volume for volume in pod["volumes"]))

    def test_migration_job_identity_and_operation_are_not_operator_overrides(self):
        self.refresh()
        update = "sha256:" + "a" * 64
        first = lifecycle.initial_database_migration_jobs(stack, self.artifact, self.fixture.summary, update)
        self.assertEqual(first, lifecycle.initial_database_migration_jobs(stack, self.artifact, self.fixture.summary, update))
        for operation in ("clean", "repair", "baseline", "resume", "migrate;sh"):
            with self.subTest(operation=operation), self.assertRaises(stack.ApplyBlockedError):
                lifecycle.initial_database_migration_jobs(stack, self.artifact, self.fixture.summary, update, operation)
        validate = lifecycle.initial_database_migration_jobs(stack, self.artifact, self.fixture.summary, update, "validate")
        self.assertNotEqual(first[0]["metadata"]["name"], validate[0]["metadata"]["name"])
        other = lifecycle.initial_database_migration_jobs(stack, self.artifact, self.fixture.summary, "sha256:" + "b" * 64)
        self.assertNotEqual(first[0]["metadata"]["name"], other[0]["metadata"]["name"])
        self.plan["services"]["core"]["database"] = "changed_core_database"
        self.refresh()
        changed = lifecycle.initial_database_migration_jobs(stack, self.artifact, self.fixture.summary, update)
        self.assertNotEqual(first[0]["metadata"]["name"], changed[0]["metadata"]["name"])

    def test_wrong_workload_image_and_environment_contradictions_rejected(self):
        target = next(r for r in self.artifact["resources"] if r["kind"] == "Deployment" and r["metadata"]["name"] == "core")
        container = target["spec"]["template"]["spec"]["containers"][0]
        image = container["image"]
        container["image"] = self.fixture.summary["services"]["kfe"]["image"]
        with self.assertRaises(stack.ApplyBlockedError):
            self.verify()
        container["image"] = image
        container["env"][0]["valueFrom"]["secretKeyRef"]["optional"] = True
        with self.assertRaises(stack.ApplyBlockedError):
            self.verify()

    def test_duplicate_json_and_nonfinite_values_rejected(self):
        for raw in ('{"schema":1,"schema":2}', '{"schema":NaN}', '[' * 1500 + ']' * 1500):
            self.configmap["data"] = {"plan.json": raw}
            with self.subTest(raw=raw[:30]), self.assertRaises(stack.ApplyBlockedError):
                lifecycle.initial_database_plan(stack, self.artifact)

    def test_duplicate_plan_wrong_namespace_and_sensitive_payload_rejected(self):
        self.refresh()
        self.artifact["resources"].append(copy.deepcopy(self.configmap))
        with self.assertRaises(stack.ApplyBlockedError):
            self.verify()
        self.artifact["resources"].pop()
        self.configmap["metadata"]["namespace"] = "kerosene-staging-vault"
        with self.assertRaises(stack.ApplyBlockedError):
            self.verify()
        self.configmap["metadata"]["namespace"] = "kerosene-staging"
        self.plan["services"]["core"]["runtimeSecret"]["password"] = "synthetic-sensitive-value"
        with self.assertRaises(stack.ApplyBlockedError) as error:
            self.verify()
        self.assertNotIn("synthetic-sensitive-value", str(error.exception))


if __name__ == "__main__":
    unittest.main()
