#!/usr/bin/env python3
"""Unit tests for the pinned whole-Cell acceptance verifier."""
import importlib.machinery
import importlib.util
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("cell_acceptance", str(ROOT / "kerosene-cell-acceptance"))
spec = importlib.util.spec_from_loader(loader.name, loader)
acceptance = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = acceptance
loader.exec_module(acceptance)


class AcceptanceTest(unittest.TestCase):
    def workload(self, kind="Deployment"):
        status = {"observedGeneration": 3, "readyReplicas": 1, "updatedReplicas": 1}
        if kind == "Deployment":
            status["availableReplicas"] = 1
        else:
            status.update(currentRevision="revision-a", updateRevision="revision-a")
        return {"kind": kind, "metadata": {"generation": 3}, "spec": {"replicas": 1}, "status": status}

    def test_real_container_names_map_to_release_components(self):
        self.assertEqual(acceptance.CONTAINER_COMPONENT["server"], "core")
        self.assertEqual(acceptance.CONTAINER_COMPONENT["kfe-service"], "kfe")
        self.assertEqual(acceptance.CONTAINER_COMPONENT["bitcoind"], "bitcoin")
        self.assertEqual(acceptance.CONTAINER_COMPONENT["kerosene-node"], "node")

    def test_workload_readiness_is_kind_specific(self):
        self.assertTrue(acceptance.workload_ready(self.workload("Deployment")))
        self.assertTrue(acceptance.workload_ready(self.workload("StatefulSet")))
        changed = self.workload("StatefulSet")
        changed["status"]["updateRevision"] = "revision-b"
        self.assertFalse(acceptance.workload_ready(changed))

    def test_selector_uses_declared_match_labels(self):
        item = {"spec": {"selector": {"matchLabels": {
            "app.kubernetes.io/name": "vault", "app.kubernetes.io/instance": "vault-3"}}}}
        self.assertEqual(acceptance.selector_for(item),
                         "app.kubernetes.io/instance=vault-3,app.kubernetes.io/name=vault")
        item["spec"]["selector"]["matchExpressions"] = [{"key": "unsafe"}]
        with self.assertRaisesRegex(RuntimeError, "exact matchLabels"):
            acceptance.selector_for(item)

    def test_interruption_prefers_three_member_quorum_over_singleton_vault(self):
        vaults = [{"namespace": "kerosene-staging", "kind": "Deployment", "name": f"vault-{index}",
                   "uid": f"uid-{index}", "selector": f"instance=vault-{index}"} for index in range(1, 4)]
        vaults.append({"namespace": "kerosene-staging-vault", "kind": "Deployment", "name": "vault",
                       "uid": "singleton", "selector": "instance=vault"})
        with patch.object(acceptance, "pvc_uids", return_value={"data": "pvc-uid"}), \
             patch.object(acceptance, "kube_json", return_value={"items": [{"metadata": {"name": "vault-3-pod", "uid": "old-pod"}}]}), \
             patch.object(acceptance, "run") as run:
            result = acceptance.interrupt_vault(["kubectl"], vaults)
        self.assertEqual(result["member"][2], "vault-3")
        self.assertIn("kerosene-staging", run.call_args_list[0].args[0])

    def test_private_json_rejects_duplicate_fields_and_shared_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"a":1,"a":2}')
            path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                acceptance.read_json(path, "state")
            path.write_text('{}')
            path.chmod(0o640)
            with self.assertRaisesRegex(RuntimeError, "owner-only"):
                acceptance.read_json(path, "state")

    def test_verify_requires_real_bound_lifecycle_evidence(self):
        digest = "sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state").mkdir(mode=0o700)
            config = {"cellId": "cell-a", "autoActivateVaultSigners": False}
            events = [
                {"phase": "admin-installed", "evidence": {"cellId": "cell-a"}},
                {"phase": "release-plan-verified", "evidence": {
                    "releaseLockCanonicalDigest": digest, "sequence": 7, "changeId": "change-a",
                    "operatorId": "operator-a", "operation": "install"}},
                {"phase": "initial-install-no-prior-traffic", "evidence": {"changeId": "change-a"}},
                {"phase": "legacy-smokes-passed", "evidence": {}},
            ]
            runtime = [{"workloadUid": "uid", "revision": "1", "podUid": "pod",
                        "images": [{"name": "service", "imageID": "image"}]}]
            for namespace, kind, name in (("kerosene-staging", "Deployment", "all"),
                                          ("kerosene-staging-vault", "Deployment", "vault")):
                events.append({"phase": f"ready:{namespace}/{kind}/{name}", "evidence": {"runtime": runtime}})
            state = {"updateId": digest, "sequence": 7, "changeId": "change-a", "operatorId": "operator-a",
                     "status": "verified", "events": events, "evidence": {
                         "tuf": {}, "bft": {}, "vaultCompatibility": {}, "bankObservers": {},
                         "snapshot": {"restoreTested": True}, "recovery": {"signaturesVerified": 2}}}
            for path, value in ((root / "cell.json", config), (root / "state/update-state.json", state)):
                path.write_text(json.dumps(value))
                path.chmod(0o600)
            components = {name: [{"identity": ["kerosene-staging", "Deployment", "all", "uid"], "runtime": runtime}]
                          for name in acceptance.COMPONENTS - {"admin"}}
            components["vault"] = [{"identity": ["kerosene-staging-vault", "Deployment", "vault", "uid"],
                                    "runtime": runtime}]
            recovered = copy.deepcopy(components)
            recovered["vault"][0]["runtime"][0]["podUid"] = "recovered-pod"
            vaults = [{"namespace": "kerosene-staging-vault", "kind": "Deployment", "name": f"vault-{index}",
                       "uid": f"uid-{index}", "selector": f"instance=vault-{index}"} for index in range(3)]
            args = SimpleNamespace(cell_dir=directory, cell_id="cell-a", cluster_uid="cluster-a",
                                   kubeconfig="/private/kubeconfig", context="context-a", release_digest=digest,
                                   sequence=7, change_id="change-a", operator_id="operator-a")
            with patch.object(acceptance, "kube_json", return_value={"metadata": {"uid": "cluster-a"}}), \
                 patch.object(acceptance, "ready_workloads", side_effect=[(components, vaults), (recovered, vaults)]), \
                 patch.object(acceptance, "interrupt_vault", return_value={
                     "member": ["kerosene-staging-vault", "Deployment", "vault", "uid"],
                     "deletedPodUid": "pod"}):
                report = acceptance.verify(args)
            self.assertEqual(set(report["components"]), acceptance.COMPONENTS)
            self.assertEqual(set(report["scenarios"]), acceptance.SCENARIOS)
            self.assertFalse(report["operatorResumePerformed"])
            state["events"] = [event for event in events if event["phase"] != "release-plan-verified"]
            (root / "state/update-state.json").write_text(json.dumps(state))
            os.chmod(root / "state/update-state.json", 0o600)
            with patch.object(acceptance, "kube_json", return_value={"metadata": {"uid": "cluster-a"}}), \
                 patch.object(acceptance, "ready_workloads", return_value=(components, vaults)), \
                 self.assertRaisesRegex(RuntimeError, "release-plan"):
                acceptance.verify(args)


if __name__ == "__main__":
    unittest.main()
