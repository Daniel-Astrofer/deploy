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
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("tested_stack", str(ROOT / "kerosene-stack"))
spec = importlib.util.spec_from_loader(loader.name, loader)
stack = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stack
loader.exec_module(stack)
lifecycle = stack.lifecycle


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.release = json.loads((ROOT / "stack/examples/release-lock-v2.example.json").read_text())
        resources = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "kerosene-staging"}}]
        for name, service in self.release["services"].items():
            if name == "admin":
                continue
            resources.append({"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "namespace": "kerosene-staging"}, "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}}, "template": {"metadata": {"labels": {"app": name}}, "spec": {"containers": [{"name": name, "image": service["image"]}]}}}})
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

    def test_exact_configuration(self):
        self.assertEqual(self.verify(), self.artifact)

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
                         [{"postgres", "redis", "tor", "node"}, {"bitcoin"}, {"lnd"},
                          {"vault"}, {"core", "kfe"}, {"web-page"}])

    def test_multiple_vault_workloads_preserve_inventory(self):
        vault = next(r for r in self.artifact["resources"] if r["metadata"]["name"] == "vault")
        replica = copy.deepcopy(vault)
        replica["metadata"]["name"] = "vault-secondary"
        self.artifact["resources"].append(replica)
        groups = lifecycle.workload_phases(stack, self.artifact, self.summary)
        self.assertEqual([r["metadata"]["name"] for r in groups[3]], ["vault", "vault-secondary"])

    def test_canonical_node_tor_sidecar_topology_is_supported_in_both_planes(self):
        node = next(r for r in self.artifact["resources"] if r["metadata"]["name"] == "node")
        tor = next(r for r in self.artifact["resources"] if r["metadata"]["name"] == "tor")
        tor["spec"]["template"]["spec"]["containers"].extend(node["spec"]["template"]["spec"]["containers"])
        self.artifact["resources"].remove(node)
        second = copy.deepcopy(tor)
        second["metadata"].update(name="vault-tor", namespace="kerosene-staging-vault")
        self.artifact["resources"].append(second)
        first = lifecycle.workload_phases(stack, self.artifact, self.summary)[0]
        self.assertEqual({r["metadata"]["name"] for r in first}, {"postgres", "redis", "tor", "vault-tor"})

    def test_cross_phase_colocation_is_rejected_not_misordered(self):
        bitcoin = next(r for r in self.artifact["resources"] if r["metadata"]["name"] == "bitcoin")
        bitcoin["spec"]["template"]["spec"]["initContainers"] = [{"name": "lnd", "image": self.summary["services"]["lnd"]["image"]}]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "dependency phases"):
            lifecycle.workload_phases(stack, self.artifact, self.summary)

    def test_operator_cli_is_not_a_daemon(self):
        resource = self.artifact["resources"][1]
        resource["spec"]["template"]["spec"]["containers"][0]["image"] = self.summary["services"]["admin"]["image"]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "Admin CLI"):
            lifecycle.workload_phases(stack, self.artifact, self.summary)

    def test_init_container_does_not_satisfy_service_inventory(self):
        node = next(r for r in self.artifact["resources"] if r["metadata"]["name"] == "node")
        pod = node["spec"]["template"]["spec"]
        pod["initContainers"] = pod["containers"]
        pod["containers"] = [{"name": "tor", "image": self.summary["services"]["tor"]["image"]}]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "missing runtime components: node"):
            lifecycle.workload_phases(stack, self.artifact, self.summary)

    def test_missing_or_ambiguous_component_identity_blocks(self):
        changed = copy.deepcopy(self.artifact)
        changed["resources"] = [r for r in changed["resources"] if r["metadata"]["name"] != "node"]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "missing runtime components: node"):
            lifecycle.workload_phases(stack, changed, self.summary)
        summary = copy.deepcopy(self.summary)
        summary["services"]["node"]["image"] = summary["services"]["vault"]["image"]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "unique approved component"):
            lifecycle.workload_phases(stack, self.artifact, summary)

    def test_application_phase_is_submitted_before_readiness_waits(self):
        from types import SimpleNamespace
        events = []
        # Test orchestration only: capabilities and readiness are mocked. This
        # deliberately provides no qualification for live apply or quorum.
        config = {"cellId": "unit", "cluster": {"kubeconfig": "/protected/cell.conf", "context": "cell-a"}}
        with patch.object(lifecycle, "require_execution_capabilities"), patch.object(lifecycle, "load_config", return_value=config), patch.object(lifecycle, "verify_bootstrap_trust"), patch.object(lifecycle, "kubectl_command", return_value=["/bound-kubectl"]) as binding, patch.object(lifecycle, "verify_maintenance"), patch.object(lifecycle.admin_install, "install", return_value={}), patch.object(lifecycle, "apply_resource", side_effect=lambda cmd, r, dry: events.append(("apply", r["metadata"]["name"]))), patch.object(lifecycle, "verify_running", side_effect=lambda cmd, r: events.append(("ready", r["metadata"]["name"])) or []), patch.object(lifecycle, "run") as run, patch.dict(lifecycle.os.environ, {}, clear=True):
            lifecycle.execute(stack, self.artifact, self.summary, SimpleNamespace(dry_run=False, cell_dir="unit-only", _release={}), lambda *_: None)
            self.assertEqual(binding.call_count, 2)
            smokes = [call.args[0] for call in run.call_args_list if call.args[0][0] == "bash"]
            self.assertEqual(len(smokes), 2)
            for command in smokes:
                self.assertEqual(command[-4:], ["--cell-binding", "/bound-kubectl", "/protected/cell.conf", "cell-a"])
        for name in ["core", "kfe"]:
            for consumer in ["core", "kfe"]:
                self.assertLess(events.index(("apply", name)), events.index(("ready", consumer)))
        self.assertLess(events.index(("ready", "bitcoin")), events.index(("apply", "lnd")))
        self.assertLess(events.index(("ready", "vault")), events.index(("apply", "core")))

    def test_invalid_phase_blocks_before_any_kubernetes_write(self):
        from types import SimpleNamespace
        self.artifact["resources"] = [r for r in self.artifact["resources"] if r["metadata"]["name"] != "node"]
        with patch.object(lifecycle, "load_config", return_value={}), patch.object(lifecycle, "verify_bootstrap_trust"), patch.object(lifecycle, "kubectl_command", return_value=["bound-kubectl"]), patch.object(lifecycle, "apply_resource") as apply, patch.dict(lifecycle.os.environ, {}, clear=True):
            with self.assertRaisesRegex(stack.ApplyBlockedError, "missing runtime components"):
                lifecycle.execute(stack, self.artifact, self.summary, SimpleNamespace(dry_run=True, cell_dir="unit-only"), lambda *_: None)
            apply.assert_not_called()

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
        changed["resources"][1]["spec"]["template"]["spec"]["initContainers"] = [{"name": "injected", "image": "evil.invalid/untrusted:latest"}]
        with self.assertRaisesRegex(stack.ApplyBlockedError, "not an approved"):
            self.verify(changed)

    def test_inline_secret_and_foreign_namespace_rejected(self):
        changed = copy.deepcopy(self.artifact)
        changed["resources"].append({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "forbidden", "namespace": "kerosene-staging"}, "data": {"value": "encoded"}})
        with self.assertRaisesRegex(stack.ApplyBlockedError, "cannot create Secrets"):
            self.verify(changed)
        changed = copy.deepcopy(self.artifact)
        changed["resources"][1]["metadata"]["namespace"] = "other-bank"
        with self.assertRaisesRegex(stack.ApplyBlockedError, "Cell namespace"):
            self.verify(changed)

    def test_privilege_and_admin_tamper(self):
        changed = copy.deepcopy(self.artifact)
        changed["resources"][1]["spec"]["template"]["spec"]["hostNetwork"] = True
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
        args = SimpleNamespace(release="unit.json", bft_receipt="receipt.json", validator_roster="roster.json", bank_observer_report="bank.json", state_dir="unit-state", environment="staging-cell", json=True)
        # Notice routing unit test; crypto and real consensus are tested separately.
        with patch.object(stack, "load_and_validate", return_value=(self.release, summary)), patch.object(stack, "verify_tuf_authorization", return_value={"signatureVerified": True}), patch.object(stack, "verify_consensus_authorization", return_value={"signaturesVerified": 3}), patch.object(stack, "verify_bank_observer_report", return_value={"compatibleObservers": 3}), patch.object(stack, "read_existing_update_state") as state:
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

    def verify(self, resource, live, rs, pod):
        responses = [live]
        if resource["kind"] == "Deployment": responses.append({"items": [rs]})
        responses.append({"items": [pod]})
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


if __name__ == "__main__":
    unittest.main()
