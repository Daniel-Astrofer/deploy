#!/usr/bin/env python3
"""Opt-in live Kubernetes qualification for critical Cell rollout semantics."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("critical_rollout_stack", str(ROOT / "kerosene-stack"))
spec = importlib.util.spec_from_loader(loader.name, loader)
stack = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stack
loader.exec_module(stack)
lifecycle = stack.lifecycle

NAMESPACE = "kerosene-critical-rollout-qualification"
NODE_IMAGE = "docker.io/kerosene-lab/offline-tools:audit-20261002"
VAULT_IMAGE = "docker.io/kerosene-lab/admission:audit-20261002"


def command(kubectl, *args, input_bytes=None):
    result = subprocess.run([*kubectl, *args], input=input_bytes, capture_output=True, timeout=180, check=False)
    if result.returncode:
        raise RuntimeError("live qualification command failed: " + " ".join(args[:4]))
    return result.stdout


def resource(name, image, claim, component):
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": name, "namespace": NAMESPACE, "labels": {"qualification": "kerosene-critical-rollout"}},
            "spec": {"replicas": 1, "strategy": {"type": "Recreate"},
                     "selector": {"matchLabels": {"app": name}},
                     "template": {"metadata": {"labels": {"app": name}, "annotations": {"qualification/revision": "1"}},
                                  "spec": {"automountServiceAccountToken": False,
                                           "containers": [{"name": component, "image": image,
                                                           "imagePullPolicy": "Never",
                                                           "command": ["sh", "-c", "trap : TERM INT; sleep infinity & wait"],
                                                           "volumeMounts": [{"name": "identity", "mountPath": "/identity"}]}],
                                           "volumes": [{"name": "identity", "persistentVolumeClaim": {"claimName": claim}}]}}}}


def main():
    if os.environ.get("KEROSENE_RUN_KIND_CRITICAL_ROLLOUT") != "1":
        print("SKIP: set KEROSENE_RUN_KIND_CRITICAL_ROLLOUT=1 with an explicit KUBECONFIG")
        return 0
    kubeconfig = os.environ.get("KUBECONFIG")
    if not kubeconfig or not Path(kubeconfig).is_absolute():
        raise RuntimeError("live qualification requires an explicit absolute KUBECONFIG")
    kubectl_path = os.environ.get("KUBECTL", "kubectl")
    context = os.environ.get("KEROSENE_KIND_CONTEXT")
    kubectl = [kubectl_path, "--kubeconfig", kubeconfig]
    if context:
        kubectl.extend(["--context", context])
    if command(kubectl, "get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "name").strip():
        raise RuntimeError("qualification namespace already exists; refusing to adopt or delete it")
    resources = [{"apiVersion": "v1", "kind": "PersistentVolumeClaim",
                  "metadata": {"name": name + "-data", "namespace": NAMESPACE},
                  "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "16Mi"}}}}
                 for name in ("node", "vault-1", "vault-2", "vault-3")]
    workloads = [resource("node", NODE_IMAGE, "node-data", "node")] + [
        resource(name, VAULT_IMAGE, name + "-data", "vault") for name in ("vault-1", "vault-2", "vault-3")]
    artifact = {"resources": [*resources, *workloads]}
    summary = {"services": {"node": {"image": NODE_IMAGE}, "vault": {"image": VAULT_IMAGE}},
               "vaultCompatibility": {"members": 3, "threshold": 2}}
    topology = lifecycle.critical_replica_topology(stack, artifact, summary)
    evidence = {"schema": "kerosene.critical-rollout-kind-qualification/v1", "namespace": NAMESPACE,
                "groups": {name: [lifecycle.identity(item["resource"]) for item in members]
                           for name, members in topology.items()}, "events": []}
    command(kubectl, "create", "namespace", NAMESPACE)
    try:
        for item in resources + workloads:
            lifecycle.apply_resource(kubectl, item)
        for item in workloads:
            ns, kind, name = lifecycle.identity(item)
            command(kubectl, "-n", ns, "rollout", "status", kind.lower() + "/" + name, "--timeout=120s")
        pvc_before = json.loads(command(kubectl, "-n", NAMESPACE, "get", "pvc", "-o", "json"))
        pvc_uids = {item["metadata"]["name"]: item["metadata"]["uid"] for item in pvc_before["items"]}
        for component in ("node", "vault"):
            members = topology[component]
            threshold = 2 if component == "vault" else len(members)
            for member in members:
                before = lifecycle.verify_critical_group_available(stack, kubectl, component, members, threshold)
                desired = json.loads(json.dumps(member["resource"]))
                desired["spec"]["template"]["metadata"]["annotations"]["qualification/revision"] = "2"
                precondition = next(item for item in before if tuple(item["identity"]) == lifecycle.identity(desired))
                lifecycle.apply_resource(kubectl, desired, False, precondition)
                ns, kind, name = lifecycle.identity(desired)
                command(kubectl, "-n", ns, "rollout", "status", kind.lower() + "/" + name, "--timeout=120s")
                runtime = lifecycle.verify_running(kubectl, desired)
                after = lifecycle.verify_critical_group_available(stack, kubectl, component, members, threshold)
                evidence["events"].append({"component": component, "workload": name,
                                           "beforeMembers": len(before), "afterMembers": len(after),
                                           "runtimePods": len(runtime)})
                member["resource"] = desired
        command(kubectl, "-n", NAMESPACE, "scale", "deployment/vault-2", "--replicas=0")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            live = json.loads(command(kubectl, "-n", NAMESPACE, "get", "deployment/vault-2", "-o", "json"))
            if live.get("status", {}).get("readyReplicas", 0) == 0:
                break
            time.sleep(1)
        try:
            lifecycle.verify_critical_group_available(stack, kubectl, "vault", topology["vault"], 2)
        except stack.ApplyBlockedError:
            evidence["unavailableMemberBlocked"] = True
        else:
            raise RuntimeError("critical rollout did not fail closed with an unavailable Vault")
        vault_two = next(item["resource"] for item in topology["vault"] if item["resource"]["metadata"]["name"] == "vault-2")
        lifecycle.apply_resource(kubectl, vault_two)
        command(kubectl, "-n", NAMESPACE, "rollout", "status", "deployment/vault-2", "--timeout=120s")
        lifecycle.verify_critical_group_available(stack, kubectl, "vault", topology["vault"], 2)
        pvc_after = json.loads(command(kubectl, "-n", NAMESPACE, "get", "pvc", "-o", "json"))
        if {item["metadata"]["name"]: item["metadata"]["uid"] for item in pvc_after["items"]} != pvc_uids:
            raise RuntimeError("persistent identity PVC changed during critical rollout")
        evidence["persistentIdentityUidsPreserved"] = True
        print(json.dumps(evidence, sort_keys=True))
        return 0
    finally:
        command(kubectl, "delete", "namespace", NAMESPACE, "--wait=true", "--timeout=120s")


if __name__ == "__main__":
    raise SystemExit(main())
