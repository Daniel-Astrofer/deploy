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
        self.assertIn("migration-executor-live-jars-recovery-not-qualified", lifecycle.EXECUTION_BLOCKERS)

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
            for field in ("enableServiceLinks", "hostNetwork", "hostPID", "hostIPC"):
                self.assertIs(pod[field], False)
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

    def test_capability_resources_use_separate_namespace_and_deny_all_before_jobs(self):
        self.refresh()
        update = "sha256:" + "a" * 64
        with patch.object(lifecycle, "run") as run:
            resources = lifecycle.initial_database_capability_resources(stack, self.artifact, self.fixture.summary, update)
            run.assert_not_called()
        namespace, policy, *jobs = resources
        name = namespace["metadata"]["name"]
        self.assertNotEqual(name, "kerosene-staging")
        self.assertLessEqual(len(name), 63)
        self.assertEqual(namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"], "restricted")
        self.assertEqual(policy["spec"], {"podSelector": {}, "policyTypes": ["Ingress", "Egress"], "ingress": [], "egress": []})
        self.assertTrue(all(r["metadata"]["namespace"] == name for r in [policy, *jobs]))
        self.assertEqual(resources, lifecycle.initial_database_capability_resources(stack, self.artifact, self.fixture.summary, update))
        other = lifecycle.initial_database_capability_resources(stack, self.artifact, self.fixture.summary, "sha256:" + "b" * 64)
        self.assertNotEqual(name, other[0]["metadata"]["name"])
        for job in jobs:
            self.assertEqual(job["spec"]["template"]["spec"]["containers"][0]["env"], [])

    def test_capabilities_output_requires_exact_bounded_component_contract(self):
        for component in ("core", "kfe"):
            expected = {"schema": "kerosene.cell.migration-capabilities/v1", "component": component, "operations": ["validate", "migrate"]}
            raw = json.dumps(expected).encode() + b"\n"
            self.assertEqual(lifecycle.verify_database_capabilities_output(stack, component, raw), expected)
            for changed in (dict(expected, extra=True), dict(expected, component="vault"),
                            dict(expected, schema="unknown"), dict(expected, operations=["migrate", "validate"]),
                            dict(expected, operations=["validate", "migrate", "clean"])):
                with self.subTest(changed=changed), self.assertRaises(stack.ApplyBlockedError):
                    lifecycle.verify_database_capabilities_output(stack, component, json.dumps(changed).encode())

    def test_capabilities_output_rejects_ambiguous_logs_without_echoing_them(self):
        raw = b'{"schema":"kerosene.cell.migration-capabilities/v1","component":"core","operations":["validate","migrate"]}'
        invalid = [b"", "not bytes", b"x" * 4097, b"\xff", b"[]", b"null", b"NaN",
                   b"startup secret-marker\n" + raw, raw + raw,
                   raw[:-1] + b',"component":"core"}', b"[" * 2000 + b"]" * 2000]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(stack.ApplyBlockedError) as error:
                lifecycle.verify_database_capabilities_output(stack, "core", value)
            self.assertEqual(str(error.exception), "invalid database capabilities output")
        with self.assertRaises(stack.ApplyBlockedError):
            lifecycle.verify_database_capabilities_output(stack, "vault", raw)

    def terminal_probe_fixture(self):
        self.refresh()
        job = lifecycle.initial_database_capability_resources(stack, self.artifact, self.fixture.summary, "sha256:" + "a" * 64)[2]
        pod = {"metadata": {"name": "probe-pod", "uid": "pod-uid", "namespace": job["metadata"]["namespace"],
            "ownerReferences": [{"apiVersion": "batch/v1", "kind": "Job", "name": job["metadata"]["name"], "uid": "job-uid", "controller": True}]},
            "spec": copy.deepcopy(job["spec"]["template"]["spec"]),
            "status": {"phase": "Succeeded", "containerStatuses": [{"name": "migration", "restartCount": 0,
                "imageID": "containerd://sha256:" + "c" * 64, "state": {"terminated": {"exitCode": 0}}}]}}
        return job, pod

    def test_terminal_probe_binds_recorded_uids_and_records_runtime_image(self):
        job, pod = self.terminal_probe_fixture()
        pod["spec"]["containers"][0].pop("env")  # Kubernetes omits empty env.
        record = lifecycle.verify_database_capability_terminal_pod(stack, job, "job-uid", pod, "pod-uid")
        self.assertEqual(record["imageID"], pod["status"]["containerStatuses"][0]["imageID"])
        self.assertEqual(record["podSpecDigest"], lifecycle.digest(pod["spec"]))
        self.assertEqual(record["podUid"], "pod-uid")

    def test_terminal_probe_rejects_identity_failure_restart_and_injected_configuration(self):
        job, original = self.terminal_probe_fixture()
        cases = [("metadata", "uid", "replacement"), ("metadata", "namespace", "foreign"),
            ("metadata", "deletionTimestamp", "now"), ("status", "phase", "Running"),
            ("spec", "hostNetwork", True), ("spec", "automountServiceAccountToken", True),
            ("spec", "enableServiceLinks", True), ("spec", "volumes", []),
            ("spec", "ephemeralContainers", [{"name": "debug"}])]
        for parent, key, value in cases:
            pod = copy.deepcopy(original)
            pod[parent][key] = value
            with self.subTest(key=key), self.assertRaises(stack.ApplyBlockedError):
                lifecycle.verify_database_capability_terminal_pod(stack, job, "job-uid", pod, "pod-uid")
        for mutation in ("owner", "exit", "restart", "imageID", "image", "envFrom", "args", "sidecar"):
            pod = copy.deepcopy(original)
            state = pod["status"]["containerStatuses"][0]
            container = pod["spec"]["containers"][0]
            if mutation == "owner": pod["metadata"]["ownerReferences"][0]["uid"] = "replacement"
            elif mutation == "exit": state["state"]["terminated"]["exitCode"] = 1
            elif mutation == "restart": state["restartCount"] = 1
            elif mutation == "imageID": state["imageID"] = ""
            elif mutation == "image": container["image"] = "foreign"
            elif mutation == "envFrom": container["envFrom"] = [{"secretRef": {"name": "forbidden"}}]
            elif mutation == "args": container["args"] = ["--cell-migration=migrate"]
            else: pod["spec"]["containers"].append(copy.deepcopy(container))
            with self.subTest(mutation=mutation), self.assertRaises(stack.ApplyBlockedError):
                lifecycle.verify_database_capability_terminal_pod(stack, job, "job-uid", pod, "pod-uid")

    def test_probe_collection_uses_one_named_container_and_bounded_transport(self):
        namespace = "cell-probe-" + "a" * 40
        expected = {"schema": "kerosene.cell.migration-capabilities/v1", "component": "core", "operations": ["validate", "migrate"]}
        command = ["/usr/bin/kubectl", "--kubeconfig", "/bound/config", "--context", "bound"]
        with patch.object(lifecycle.probe_process, "run_probe", return_value=json.dumps(expected).encode()) as run:
            self.assertEqual(lifecycle.collect_database_capabilities_output(stack, command, "core", namespace, "probe-pod"), expected)
        run.assert_called_once_with(command + ["-n", namespace, "logs", "probe-pod", "--container=migration", "--timestamps=false", "--limit-bytes=4097"], timeout=30)

    def test_probe_collection_rejects_foreign_targets_before_subprocess(self):
        with patch.object(lifecycle.probe_process, "run_probe") as run:
            for component, namespace, pod in (("vault", "cell-probe-" + "a" * 40, "probe"),
                    ("core", "kerosene-staging", "probe"), ("core", "cell-probe-" + "a" * 40, "--all-containers")):
                with self.subTest(component=component, namespace=namespace), self.assertRaises(stack.ReleaseValidationError):
                    lifecycle.collect_database_capabilities_output(stack, ["/bound/kubectl"], component, namespace, pod)
            run.assert_not_called()

    def test_probe_transport_failure_is_generic_and_never_accepted(self):
        with patch.object(lifecycle.probe_process, "run_probe", side_effect=lifecycle.probe_process.ProbeProcessError("private-marker")):
            with self.assertRaises(stack.ApplyBlockedError) as error:
                lifecycle.collect_database_capabilities_output(stack, ["/bound/kubectl"], "core", "cell-probe-" + "a" * 40, "probe")
        self.assertEqual(str(error.exception), "database probe log collection failed")

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

    def test_migration_executor_runs_migrate_then_validate_and_rechecks_identity(self):
        self.refresh()
        update = "sha256:" + "a" * 64
        jobs = [job for operation in ("migrate", "validate")
                for job in lifecycle.initial_database_migration_jobs(stack, self.artifact, self.fixture.summary, update, operation)]
        fixtures = {}
        for index, job in enumerate(jobs, 1):
            uid = f"00000000-0000-0000-0000-{index:012d}"
            pod_uid = f"10000000-0000-0000-0000-{index:012d}"
            pod_name = job["metadata"]["name"] + "-pod"
            live_job = {"metadata": {"name": job["metadata"]["name"], "namespace": "kerosene-staging",
                "uid": uid, "annotations": copy.deepcopy(job["metadata"]["annotations"])},
                "status": {"succeeded": 1, "conditions": [{"type": "Complete", "status": "True"}]}}
            pod = {"metadata": {"name": pod_name, "namespace": "kerosene-staging", "uid": pod_uid,
                "ownerReferences": [{"apiVersion": "batch/v1", "kind": "Job", "name": job["metadata"]["name"],
                                      "uid": uid, "controller": True}]},
                "spec": copy.deepcopy(job["spec"]["template"]["spec"]),
                "status": {"phase": "Succeeded", "containerStatuses": [{"name": "migration", "restartCount": 0,
                    "imageID": "containerd://sha256:" + f"{index:x}" * 64,
                    "state": {"terminated": {"exitCode": 0}}}]}}
            fixtures[job["metadata"]["name"]] = (live_job, pod)
        job_reads = {name: 0 for name in fixtures}
        def command(argv, **_):
            if "job" in argv and "--ignore-not-found" in argv:
                return b""
            if "job" in argv:
                name = argv[argv.index("job") + 1]
                job_reads[name] += 1
                return json.dumps(fixtures[name][0]).encode()
            if "pods" in argv:
                uid = argv[argv.index("-l") + 1].split("=", 1)[1]
                pod = next(value[1] for value in fixtures.values()
                           if value[0]["metadata"]["uid"] == uid)
                return json.dumps({"items": [pod]}).encode()
            if "pod" in argv:
                name = argv[argv.index("pod") + 1]
                pod = next(value[1] for value in fixtures.values() if value[1]["metadata"]["name"] == name)
                return json.dumps(pod).encode()
            raise AssertionError(argv)
        applied = []
        with patch.object(lifecycle, "run", side_effect=command), \
                patch.object(lifecycle, "apply_resource", side_effect=lambda _, job, dry: applied.append(job)), \
                patch.object(lifecycle.probe_process, "run_probe", return_value=b"") as wait:
            result = lifecycle.execute_initial_database_migrations(stack, ["/bound/kubectl"], self.artifact,
                                                                    self.fixture.summary, update)
        self.assertEqual([job["spec"]["template"]["spec"]["containers"][0]["args"][0] for job in applied],
                         ["--cell-migration=migrate", "--cell-migration=migrate",
                          "--cell-migration=validate", "--cell-migration=validate"])
        self.assertEqual(len(result["jobs"]), 4)
        self.assertEqual(wait.call_count, 4)
        self.assertTrue(all(count == 2 for count in job_reads.values()))

        retained_names = {jobs[0]["metadata"]["name"], jobs[1]["metadata"]["name"]}
        def recovery_command(argv, **_):
            if "job" in argv and "--ignore-not-found" in argv:
                name = argv[argv.index("job") + 1]
                return json.dumps(fixtures[name][0]).encode() if name in retained_names else b""
            return command(argv)
        recovered_applied = []
        with patch.object(lifecycle, "run", side_effect=recovery_command), \
                patch.object(lifecycle, "apply_resource", side_effect=lambda _, job, dry: recovered_applied.append(job)), \
                patch.object(lifecycle.probe_process, "run_probe", return_value=b""):
            recovered = lifecycle.execute_initial_database_migrations(stack, ["/bound/kubectl"], self.artifact,
                self.fixture.summary, update, recover_existing=True)
        self.assertEqual([record["recoveredExisting"] for record in recovered["jobs"]], [True, True, False, False])
        self.assertEqual([job["metadata"]["name"] for job in recovered_applied],
                         [jobs[2]["metadata"]["name"], jobs[3]["metadata"]["name"]])

        def gap_command(argv, **_):
            if "job" in argv and "--ignore-not-found" in argv:
                name = argv[argv.index("job") + 1]
                return b'{"kind":"Job"}' if name == jobs[2]["metadata"]["name"] else b""
            raise AssertionError(argv)
        with patch.object(lifecycle, "run", side_effect=gap_command), \
                patch.object(lifecycle, "apply_resource") as gap_apply, \
                self.assertRaisesRegex(stack.ApplyBlockedError, "ordered recovery prefix"):
            lifecycle.execute_initial_database_migrations(stack, ["/bound/kubectl"], self.artifact,
                self.fixture.summary, update, recover_existing=True)
        gap_apply.assert_not_called()

    def test_migration_executor_refuses_preexisting_job_without_writes(self):
        self.refresh()
        with patch.object(lifecycle, "run", return_value=b'{"kind":"Job"}'), \
                patch.object(lifecycle, "apply_resource") as apply, \
                patch.object(lifecycle.probe_process, "run_probe") as wait, \
                self.assertRaisesRegex(stack.ApplyBlockedError, "already exists"):
            lifecycle.execute_initial_database_migrations(stack, ["/bound/kubectl"], self.artifact,
                self.fixture.summary, "sha256:" + "a" * 64)
        apply.assert_not_called()
        wait.assert_not_called()

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
