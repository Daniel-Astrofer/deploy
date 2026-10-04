"""Cell-local lifecycle and approved-manifest execution; never executes archive code.

This module deliberately has no YAML parser: the approved artifact is JSON,
so canonical configuration digests are reproducible across packaging and apply.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import urllib.parse
import urllib.error
import urllib.request
import admin_install
import probe_process

SCHEMA = "kerosene.stack.deployment/v1"
NAMESPACES = {"kerosene-staging", "kerosene-staging-vault"}
WORKLOADS = {"Deployment", "StatefulSet"}
KINDS = WORKLOADS | {"Namespace", "ConfigMap", "Service", "ServiceAccount", "NetworkPolicy", "PersistentVolumeClaim", "PodDisruptionBudget", "HorizontalPodAutoscaler"}
DATABASE_PLAN_NAME = "kerosene-cell-database-plan"
DATABASE_SCRIPTS = ("create-service-databases.sql", "service-runtime-grants.sql")

# These are implementation capabilities, never caller-supplied declarations.
# Remove a blocker only with the corresponding implementation and integration
# test; signed evidence cannot implement an absent runtime safety mechanism.
EXECUTION_BLOCKERS = (
    "vault-live-rebuild-provenance-not-qualified",
    "migration-executor-live-jars-recovery-not-qualified",
    "node-vault-live-protocol-quorum-not-qualified",
    "complete-cell-live-acceptance-not-qualified",
)


def require_execution_capabilities(stack):
    if EXECUTION_BLOCKERS:
        raise stack.ApplyBlockedError("Cell apply is not qualified: " + ", ".join(EXECUTION_BLOCKERS))


def kubectl_command(stack, config):
    tool = shutil.which("kubectl")
    cluster = config.get("cluster")
    if not tool or not cluster:
        raise stack.ApplyBlockedError("Cell lacks explicit Kubernetes cluster binding; do not use the active context")
    stack.require_keys(cluster, "cluster binding", ("kubeconfig", "context", "systemNamespaceUid"))
    if not Path(cluster["kubeconfig"]).is_absolute() or not cluster["context"] or not cluster["systemNamespaceUid"]:
        raise stack.ApplyBlockedError("invalid explicit cluster binding")
    command = [tool, "--kubeconfig", cluster["kubeconfig"], "--context", cluster["context"], "--request-timeout=30s"]
    live = json.loads(run(command + ["get", "namespace", "kube-system", "-o", "json"]))
    if live.get("metadata", {}).get("uid") != cluster["systemNamespaceUid"]:
        raise stack.ApplyBlockedError("Kubernetes cluster identity differs from Cell bootstrap")
    return command


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def identity(resource):
    meta = resource["metadata"]
    return (meta.get("namespace", ""), resource["kind"], meta["name"])


def containers(resource):
    spec = resource.get("spec", {}).get("template", {}).get("spec", {})
    return spec.get("containers", []) + spec.get("initContainers", [])


def component_config(artifact, image, name):
    resources = [r for r in artifact["resources"] if r["kind"] not in WORKLOADS or any(c.get("image") == image for c in containers(r))]
    result = {"resources": sorted(resources, key=identity)}
    if name == "admin":
        result["admin"] = artifact["admin"]
    return result


def verify_deployment(stack, release, summary, path):
    artifact = stack.read_json_document(path, "approved deployment manifest", 8 * 1024 * 1024)
    stack.require_keys(artifact, "deployment", ("schema", "environment", "resources", "admin"))
    if artifact["schema"] != SCHEMA or artifact["environment"] != "staging-cell":
        raise stack.ApplyBlockedError("deployment manifest has unsupported schema/environment")
    resources = artifact["resources"]
    if type(resources) is not list or not resources or len(resources) > 1024:
        raise stack.ApplyBlockedError("deployment resources must be a nonempty bounded list")
    seen, observed = set(), set()
    allowed_images = {s["image"] for name, s in summary["services"].items() if name != "admin"}
    for resource in resources:
        stack.require_keys(resource, "resource", ("apiVersion", "kind", "metadata"), ("spec", "data", "binaryData"))
        if resource["kind"] not in KINDS:
            raise stack.ApplyBlockedError("deployment cannot create Secrets, Jobs, RBAC or arbitrary resource kinds")
        if resource["kind"] == "HorizontalPodAutoscaler":
            raise stack.ApplyBlockedError("HPA requires a separately approved freeze/restore policy during Cell update")
        meta = stack.require_object(resource["metadata"], "resource metadata")
        name = stack.require_identifier(meta.get("name"), "resource name")
        if resource["kind"] == "Namespace":
            if name not in NAMESPACES:
                raise stack.ApplyBlockedError("foreign namespace in deployment")
        elif meta.get("namespace") not in NAMESPACES:
            raise stack.ApplyBlockedError("every namespaced resource must name a Cell namespace")
        key = identity(resource)
        if key in seen:
            raise stack.ApplyBlockedError("duplicate resource identity in deployment")
        seen.add(key)
        if resource["kind"] in WORKLOADS:
            spec = resource.get("spec", {})
            if type(spec.get("replicas")) is not int or spec["replicas"] < 1:
                raise stack.ApplyBlockedError("workload requires explicit positive replicas")
            pod = spec.get("template", {}).get("spec", {})
            if any(pod.get(k) for k in ("hostNetwork", "hostPID", "hostIPC")):
                raise stack.ApplyBlockedError("host namespaces forbidden")
            if any("hostPath" in v for v in pod.get("volumes", [])):
                raise stack.ApplyBlockedError("hostPath forbidden")
            for container in containers(resource):
                image = container.get("image")
                if image not in allowed_images:
                    raise stack.ApplyBlockedError("runtime/init image is not an approved service image")
                if container.get("securityContext", {}).get("privileged"):
                    raise stack.ApplyBlockedError("privileged container forbidden")
                observed.add(image)
    admin = stack.require_keys(artifact["admin"], "deployment admin", ("image", "config"))
    if admin["image"] != summary["services"]["admin"]["image"]:
        raise stack.ApplyBlockedError("Admin artifact does not match approved release")
    stack.reject_sensitive_field_names(admin["config"], "Admin configuration")
    try:
        admin_install.validate_config(admin["config"])
    except RuntimeError as error:
        raise stack.ApplyBlockedError(str(error)) from error
    for name, service in summary["services"].items():
        if name != "admin" and service["image"] not in observed:
            raise stack.ApplyBlockedError(f"missing deployed component: {name}")
        if digest(component_config(artifact, service["image"], name)) != service["configDigest"]:
            raise stack.ApplyBlockedError(f"approved configuration digest mismatch: {name}")
    workload_phases(stack, artifact, summary)
    initial_database_plan(stack, artifact, summary)
    verify_vault_probe_configuration(stack, artifact)
    try:
        required_secret_references(stack, artifact)
    except (TypeError, AttributeError, KeyError) as error:
        raise stack.ApplyBlockedError("invalid external Secret reference structure") from error
    return artifact


def verify_vault_probe_configuration(stack, artifact):
    """Bind opt-in authenticated probe URL to approved ConfigMap bytes."""
    for resource in artifact["resources"]:
        if resource["kind"] not in WORKLOADS:
            continue
        for container in containers(resource):
            command = container.get("readinessProbe", {}).get("exec", {}).get("command", [])
            if command != ["/usr/local/bin/kerosene-vault", "--health-probe"]:
                continue
            try:
                probe = container["readinessProbe"]
                if type(probe.get("timeoutSeconds")) is not int or probe["timeoutSeconds"] <= 4 or any(key in probe for key in ("httpGet", "tcpSocket", "grpc")):
                    raise ValueError("invalid authenticated probe deadline or handler")
                entries = [e for e in container.get("env", []) if e.get("name") == "VAULT_HEALTH_PROBE_URL"]
                if len(entries) != 1 or set(entries[0]) != {"name", "valueFrom"}:
                    raise ValueError("unbound probe URL")
                source = entries[0]["valueFrom"]
                if set(source) != {"configMapKeyRef"}:
                    raise ValueError("wrong probe source")
                reference = source["configMapKeyRef"]
                if set(reference) - {"name", "key", "optional"} or reference.get("optional", False) is not False:
                    raise ValueError("optional probe configuration")
                matches = [r for r in artifact["resources"] if identity(r) ==
                    (resource["metadata"]["namespace"], "ConfigMap", reference["name"])]
                if len(matches) != 1:
                    raise ValueError("unapproved probe configuration")
                raw = matches[0]["data"][reference["key"]]
                if not isinstance(raw, str) or len(raw) > 2048:
                    raise ValueError("invalid probe URL")
                url = urllib.parse.urlsplit(raw)
                if url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None or url.path != "/v1/health" or url.query or url.fragment:
                    raise ValueError("invalid probe URL")
                try:
                    ipaddress.ip_address(url.hostname)
                except ValueError:
                    pass
                else:
                    raise ValueError("literal IP probe target")
                if url.hostname.replace(".", "").isdigit() or re.fullmatch(r"0[xX][0-9a-fA-F]+", url.hostname):
                    raise ValueError("numeric probe target")
                if url.port is not None and not 0 < url.port <= 65535:
                    raise ValueError("invalid probe port")
            except (ValueError, TypeError, AttributeError, KeyError):
                raise stack.ApplyBlockedError("Vault authenticated readiness requires an approved nonoptional URL ConfigMap") from None


def run(argv, *, input_bytes=None):
    result = subprocess.run(argv, input=input_bytes, capture_output=True, check=False, timeout=120)
    if result.returncode:
        # Never echo kubectl's output: it can include secret material.
        raise RuntimeError(f"operation failed ({result.returncode}): {argv[0]} {argv[1]}")
    return result.stdout


def apply_resource(kubectl, resource, dry_run=False, live_precondition=None):
    candidate = resource
    if live_precondition is not None:
        if (tuple(live_precondition.get("identity", ())) != identity(resource) or
                not isinstance(live_precondition.get("uid"), str) or
                not isinstance(live_precondition.get("resourceVersion"), str)):
            raise RuntimeError("invalid live resource precondition")
        candidate = copy.deepcopy(resource)
        candidate["metadata"]["uid"] = live_precondition["uid"]
        candidate["metadata"]["resourceVersion"] = live_precondition["resourceVersion"]
    argv = kubectl + ["apply", "--server-side", "--field-manager=kerosene-stack", "-f", "-"]
    if dry_run:
        argv.append("--dry-run=server")
    run(argv, input_bytes=canonical(candidate))


def verify_empty_installation(stack, kubectl):
    """Initial install cannot adopt an existing Cell or persistent state.

    An absent local journal does not prove an empty bound cluster. Existing
    identities may be provisioned out of band, but workloads and PVCs require
    explicit recovery, never a fresh-install overwrite.
    """
    for namespace in sorted(NAMESPACES):
        raw = run(kubectl + ["get", "namespace", namespace, "--ignore-not-found", "-o", "json"])
        if not raw.strip():
            continue
        try:
            observed = json.loads(raw)
            if observed.get("kind") != "Namespace" or observed.get("metadata", {}).get("name") != namespace:
                raise ValueError("namespace identity mismatch")
            resources = json.loads(run(kubectl + ["-n", namespace, "get", "deployments,statefulsets,pods,persistentvolumeclaims", "-o", "json"]))
            if not isinstance(resources, dict) or not isinstance(resources.get("items"), list):
                raise ValueError("invalid inventory")
        except (ValueError, AttributeError) as error:
            raise stack.ApplyBlockedError("initial-install cluster inventory is invalid") from error
        if resources["items"]:
            raise stack.ApplyBlockedError("initial install refuses existing workloads or persistent volumes; use explicit recovery")


def required_secret_references(stack, artifact):
    """Collect Kubernetes-required external credentials, never their values."""
    references = {}
    def add(namespace, value, name_field, keys=()):
        if not isinstance(value, dict) or type(value.get("optional", False)) is not bool:
            raise stack.ApplyBlockedError("invalid Secret reference")
        name = stack.require_identifier(value.get(name_field), "external Secret name")
        if value.get("optional") is True:
            return
        required = references.setdefault((namespace, name), set())
        for key in keys:
            if not isinstance(key, str) or not key or len(key) > 253 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in key):
                raise stack.ApplyBlockedError("invalid external Secret key")
            required.add(key)
    for resource in artifact["resources"]:
        if resource["kind"] not in WORKLOADS:
            continue
        namespace = resource["metadata"]["namespace"]
        pod = resource["spec"]["template"]["spec"]
        for container in containers(resource):
            for variable in container.get("env", []):
                reference = variable.get("valueFrom", {}).get("secretKeyRef")
                if reference is not None:
                    add(namespace, reference, "name", [reference.get("key")])
            for source in container.get("envFrom", []):
                if "secretRef" in source:
                    add(namespace, source["secretRef"], "name")
        for volume in pod.get("volumes", []):
            if "secret" in volume:
                reference = volume["secret"]
                add(namespace, reference, "secretName", [item.get("key") for item in reference.get("items", [])])
            for source in volume.get("projected", {}).get("sources", []):
                if "secret" in source:
                    reference = source["secret"]
                    add(namespace, reference, "name", [item.get("key") for item in reference.get("items", [])])
        for reference in pod.get("imagePullSecrets", []):
            add(namespace, reference, "name")
    plan = initial_database_plan(stack, artifact)
    if plan is not None:
        for reference in [plan["postgres"]["bootstrapSecret"]] + [service[key] for service in plan["services"].values()
                                                               for key in ("migrationSecret", "runtimeSecret")]:
            add("kerosene-staging", reference, "name", [value for key, value in reference.items() if key != "name"])
    if len(references) > 1024:
        raise stack.ApplyBlockedError("external Secret reference limit exceeded")
    return references


def initial_database_plan(stack, artifact, summary=None):
    """Inert approved configuration only; never executes SQL or reads credentials.

    ConfigMaps are already included in every component configDigest. The reserved
    plan therefore cannot be substituted independently of the approved release.
    Legacy manifests remain inspectable without a plan, not install-qualified.
    """
    candidates = [r for r in artifact["resources"] if r.get("metadata", {}).get("name") == DATABASE_PLAN_NAME]
    if not candidates:
        return None
    def keys(value, required):
        return stack.require_keys(value, "initial database plan", required)
    def sql_name(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) or value in ("postgres", "template0", "template1"):
            raise ValueError("invalid SQL identity")
    def secret(value, url=False):
        keys(value, ("name", "usernameKey", "passwordKey", *(('urlKey',) if url else ())))
        stack.require_identifier(value["name"], "database Secret name")
        for name, key in value.items():
            if name != "name" and (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,253}", key)):
                raise ValueError("invalid Secret key")
        if len(set(value[key] for key in value if key != "name")) != len(value) - 1:
            raise ValueError("aliased credential keys")
    def workload(binding, component):
        keys(binding, ("kind", "name", "container"))
        if binding["kind"] not in WORKLOADS:
            raise ValueError("invalid workload kind")
        for key in ("name", "container"):
            stack.require_identifier(binding[key], "database workload identity")
        matches = [r for r in artifact["resources"] if identity(r) == ("kerosene-staging", binding["kind"], binding["name"])]
        if len(matches) != 1:
            raise ValueError("ambiguous database workload")
        pod = matches[0]["spec"]["template"]["spec"]
        found = [c for c in pod["containers"] if c["name"] == binding["container"]]
        if len(found) != 1 or (summary is not None and found[0]["image"] != summary["services"][component]["image"]):
            raise ValueError("database workload is not the approved component")
        env = found[0].get("env", [])
        if len({e["name"] for e in env}) != len(env):
            raise ValueError("ambiguous database environment")
        return {e["name"]: e for e in env}
    def binding_matches(env, variable, reference, key):
        value = keys(env[variable], ("name", "valueFrom"))
        source = keys(value["valueFrom"], ("secretKeyRef",))["secretKeyRef"]
        stack.require_keys(source, "database environment Secret", ("name", "key"), ("optional",))
        if source.get("optional", False) is not False or source["name"] != reference["name"] or source["key"] != reference[key]:
            raise ValueError("database environment contradicts plan")
    try:
        if len(candidates) != 1:
            raise ValueError("duplicate plan")
        resource = candidates[0]
        if resource["kind"] != "ConfigMap" or resource["metadata"].get("namespace") != "kerosene-staging":
            raise ValueError("foreign plan namespace/kind")
        data = keys(resource["data"], ("plan.json",))["plan.json"]
        if not isinstance(data, str) or len(data.encode("utf-8")) > 32768:
            raise ValueError("unbounded plan")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate plan field")
                result[key] = value
            return result
        plan = json.loads(data, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite plan")))
        keys(plan, ("schema", "mode", "postgres", "services", "scriptDigests"))
        if plan["schema"] != "kerosene.cell.initial-databases/v1" or plan["mode"] != "initial":
            raise ValueError("unsupported database plan")
        keys(plan["scriptDigests"], DATABASE_SCRIPTS)
        for name in DATABASE_SCRIPTS:
            path = Path(__file__).resolve().parents[1] / "runtime/postgres" / name
            raw = stack.read_regular_file_bytes(path, "installed database primitive", 256 * 1024)
            if plan["scriptDigests"][name] != "sha256:" + hashlib.sha256(raw).hexdigest():
                raise ValueError("installed database primitive digest differs")
        postgres = keys(plan["postgres"], ("workload", "bootstrapSecret"))
        secret(postgres["bootstrapSecret"])
        env = workload(postgres["workload"], "postgres")
        binding_matches(env, "POSTGRES_USER", postgres["bootstrapSecret"], "usernameKey")
        binding_matches(env, "POSTGRES_PASSWORD", postgres["bootstrapSecret"], "passwordKey")
        keys(plan["services"], ("core", "kfe"))
        databases, roles, secrets = [], [], [postgres["bootstrapSecret"]["name"]]
        for name, service in plan["services"].items():
            keys(service, ("database", "migrationRole", "runtimeRole", "workload", "migrationSecret", "runtimeSecret"))
            for key in ("database", "migrationRole", "runtimeRole"):
                sql_name(service[key])
            databases.append(service["database"])
            roles.extend([service["migrationRole"], service["runtimeRole"]])
            for key in ("migrationSecret", "runtimeSecret"):
                secret(service[key], url=True)
                secrets.append(service[key]["name"])
            env = workload(service["workload"], name)
            for variable, key in (("SPRING_DATASOURCE_URL", "urlKey"), ("SPRING_DATASOURCE_USERNAME", "usernameKey"), ("SPRING_DATASOURCE_PASSWORD", "passwordKey")):
                binding_matches(env, variable, service["runtimeSecret"], key)
        if len(set(databases)) != 2 or len(set(roles)) != 4 or len(set(secrets)) != 5:
            raise ValueError("database, role or credential aliases")
        return plan
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError, stack.ReleaseValidationError) as error:
        raise stack.ApplyBlockedError("invalid approved initial database plan") from error


def initial_database_migration_jobs(stack, artifact, summary, update_id, operation="migrate"):
    """Build inert controller-owned Jobs; never submit/start them here.

    This fixed command bypasses image/application startup flags. Jobs use only
    migration credentials and distinct labels, never runtime Service/PDB labels.
    Scheduling, bound-target verification and recovery are still unqualified.
    """
    stack.require_digest(update_id, "database migration update identity")
    if operation not in ("capabilities", "validate", "migrate"):
        raise stack.ApplyBlockedError("unsupported database migration operation")
    plan = initial_database_plan(stack, artifact, summary)
    if plan is None:
        raise stack.ApplyBlockedError("database migration Jobs require an approved initial plan")
    plan_digest = digest(plan)
    jobs = []
    for component in ("core", "kfe"):
        reference = plan["services"][component]["migrationSecret"]
        image = summary["services"][component]["image"]
        key = digest({"updateId": update_id, "planDigest": plan_digest, "component": component, "operation": operation})[7:47]
        prefix = f"cell-db-{component}-{operation}-"
        job_name = prefix + key[:63 - len(prefix)]
        labels = {"app.kubernetes.io/name": "cell-database-migration", "kerosene.io/migration-component": component}
        jobs.append({"apiVersion": "batch/v1", "kind": "Job", "metadata": {
            "name": job_name, "namespace": "kerosene-staging", "labels": labels,
            "annotations": {"kerosene.io/update-id": update_id, "kerosene.io/database-plan-digest": plan_digest}},
            "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 30 if operation == "capabilities" else 300, "template": {
                "metadata": {"labels": labels}, "spec": {
                    "restartPolicy": "Never", "automountServiceAccountToken": False,
                    "enableServiceLinks": False, "hostNetwork": False, "hostPID": False, "hostIPC": False,
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 65532, "runAsGroup": 65532,
                                        "fsGroup": 65532, "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [{"name": "migration", "image": image, "imagePullPolicy": "IfNotPresent",
                        "command": ["java", "-XX:+ExitOnOutOfMemoryError", "-XX:MaxRAMPercentage=75.0", "-jar", "/app/app.jar"],
                        "args": ["--cell-migration=" + operation],
                        "env": [{"name": variable, "valueFrom": {"secretKeyRef": {"name": reference["name"], "key": reference[field]}}}
                                for variable, field in (() if operation == "capabilities" else (("SPRING_DATASOURCE_URL", "urlKey"), ("SPRING_DATASOURCE_USERNAME", "usernameKey"), ("SPRING_DATASOURCE_PASSWORD", "passwordKey")))],
                        "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}},
                        "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                            "capabilities": {"drop": ["ALL"]}},
                        "volumeMounts": [{"name": "temporary", "mountPath": "/tmp"}]}],
                    "volumes": [{"name": "temporary", "emptyDir": {"sizeLimit": "64Mi"}}]}}}})
    return jobs


def initial_database_capability_resources(stack, artifact, summary, update_id):
    """Prepare namespace isolation before credential-free probes; no API writes.

    The executor must refuse a pre-existing namespace or overlapping allow
    policies and qualify CNI enforcement before scheduling. Policy presence
    alone is not proof of isolation.
    """
    jobs = initial_database_migration_jobs(stack, artifact, summary, update_id, "capabilities")
    namespace = "cell-probe-" + digest({"updateId": update_id,
        "planDigest": jobs[0]["metadata"]["annotations"]["kerosene.io/database-plan-digest"]})[7:47]
    annotations = dict(jobs[0]["metadata"]["annotations"])
    resources = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {
        "name": namespace, "annotations": annotations,
        "labels": {"pod-security.kubernetes.io/enforce": "restricted"}}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
         "metadata": {"name": "deny-all", "namespace": namespace, "annotations": annotations},
         "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"], "ingress": [], "egress": []}}]
    for job in jobs:
        job["metadata"]["namespace"] = namespace
    return resources + jobs


def verify_database_capabilities_output(stack, component, raw):
    """Validate bounded untrusted probe stdout, not release/runtime evidence."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result
    try:
        if component not in ("core", "kfe") or type(raw) is not bytes or not 0 < len(raw) <= 4096:
            raise ValueError("invalid probe input")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite field")))
        expected = {"schema": "kerosene.cell.migration-capabilities/v1",
                    "component": component, "operations": ["validate", "migrate"]}
        if value != expected:
            raise ValueError("unsupported capabilities")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise stack.ApplyBlockedError("invalid database capabilities output") from error


def verify_database_capability_terminal_pod(stack, job, job_uid, pod, pod_uid, operation="capabilities"):
    """Bind a terminal probe observation to controller-recorded UIDs.

    This records runtime imageID, not registry/index provenance or CNI proof.
    The executor must check the live Job and re-read the pod around log collection.
    """
    try:
        if any(not isinstance(uid, str) or not uid or len(uid) > 128 for uid in (job_uid, pod_uid)):
            raise ValueError("missing recorded identity")
        meta, spec, status = pod["metadata"], pod["spec"], pod["status"]
        expected = job["spec"]["template"]["spec"]
        if meta["uid"] != pod_uid or meta["namespace"] != job["metadata"]["namespace"] or meta.get("deletionTimestamp"):
            raise ValueError("pod identity changed")
        owners = [owner for owner in meta.get("ownerReferences", []) if owner.get("controller") is True]
        if len(owners) != 1 or any(owners[0].get(key) != value for key, value in
                (("apiVersion", "batch/v1"), ("kind", "Job"), ("name", job["metadata"]["name"]), ("uid", job_uid))):
            raise ValueError("foreign controller")
        if status.get("phase") != "Succeeded" or spec.get("restartPolicy") != "Never":
            raise ValueError("probe not successfully terminal")
        for field in ("automountServiceAccountToken", "enableServiceLinks", "hostNetwork", "hostPID", "hostIPC"):
            if spec.get(field, False) is not False:
                raise ValueError("unsafe pod namespaces or injection")
        if spec.get("automountServiceAccountToken") is not False or spec.get("enableServiceLinks") is not False:
            raise ValueError("missing explicit isolation")
        if any(spec.get(field) for field in ("initContainers", "ephemeralContainers", "imagePullSecrets")):
            raise ValueError("unexpected container or secret")
        if spec.get("securityContext") != expected["securityContext"] or spec.get("volumes") != expected["volumes"]:
            raise ValueError("changed security or storage")
        actual = spec["containers"]
        if len(actual) != 1 or len(expected["containers"]) != 1:
            raise ValueError("unexpected container count")
        container = actual[0]
        target = expected["containers"][0]
        if operation not in ("capabilities", "migrate", "validate") or target["args"] != ["--cell-migration=" + operation]:
            raise ValueError("unexpected migration operation")
        if operation == "capabilities" and target.get("env"):
            raise ValueError("not a credential-free probe")
        for key, value in target.items():
            if container.get(key, [] if key == "env" else None) != value:
                raise ValueError("changed probe command or configuration")
        allowed_defaults = {"terminationMessagePath", "terminationMessagePolicy"}
        if set(container) - set(target) - allowed_defaults:
            raise ValueError("extra container configuration")
        states = status["containerStatuses"]
        if len(states) != 1 or states[0]["name"] != target["name"] or type(states[0].get("restartCount")) is not int or states[0]["restartCount"] != 0:
            raise ValueError("unexpected runtime container or restart")
        state = states[0]
        if state.get("lastState") or set(state["state"]) != {"terminated"}:
            raise ValueError("ambiguous termination")
        terminated = state["state"]["terminated"]
        if type(terminated.get("exitCode")) is not int or terminated["exitCode"] != 0 or terminated.get("signal", 0) != 0:
            raise ValueError("failed termination")
        image_id = state.get("imageID")
        if not isinstance(image_id, str) or not image_id or len(image_id) > 1024:
            raise ValueError("missing runtime image identity")
        return {"jobUid": job_uid, "podUid": pod_uid, "podName": meta["name"],
                "namespace": meta["namespace"], "image": target["image"], "imageID": image_id,
                "podSpecDigest": digest(spec), "operation": operation,
                "component": job["metadata"]["labels"]["kerosene.io/migration-component"]}
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise stack.ApplyBlockedError("database capability pod is not the expected successful probe") from None


def collect_database_capabilities_output(stack, kubectl, component, namespace, pod_name):
    """Read one controller-selected pod's bounded stdout; not completion proof.

    Caller must verify cluster, pod UID/spec, terminal status and isolation
    before and after collection. No label selector, follow or implicit context.
    """
    if component not in ("core", "kfe") or not isinstance(namespace, str) or not re.fullmatch(r"cell-probe-[a-f0-9]{40}", namespace):
        raise stack.ApplyBlockedError("invalid database probe log target")
    stack.require_identifier(pod_name, "database probe pod name")
    try:
        raw = probe_process.run_probe(kubectl + ["-n", namespace, "logs", pod_name,
            "--container=migration", "--timestamps=false", "--limit-bytes=4097"], timeout=30)
    except probe_process.ProbeProcessError:
        raise stack.ApplyBlockedError("database probe log collection failed") from None
    return verify_database_capabilities_output(stack, component, raw)


def execute_initial_database_migrations(stack, kubectl, artifact, summary, update_id, recover_existing=False):
    """Run migrate then validate Jobs, or inspect an exact retained prefix."""
    records = []
    ordered = [(operation, job) for operation in ("migrate", "validate")
               for job in initial_database_migration_jobs(stack, artifact, summary, update_id, operation)]
    existing_documents = []
    for _, job in ordered:
        namespace, name = job["metadata"]["namespace"], job["metadata"]["name"]
        raw = run(kubectl + ["-n", namespace, "get", "job", name, "--ignore-not-found", "-o", "json"])
        existing_documents.append(raw)
    present = [bool(raw.strip()) for raw in existing_documents]
    if any(present) and not recover_existing:
        raise stack.ApplyBlockedError("database migration Job already exists; explicit recovery is required")
    if recover_existing and any(present[index] and not all(present[:index]) for index in range(len(present))):
        raise stack.ApplyBlockedError("retained database migration Jobs are not an ordered recovery prefix")
    for index, (operation, job) in enumerate(ordered):
        namespace, name = job["metadata"]["namespace"], job["metadata"]["name"]
        retained = present[index]
        if not retained:
            apply_resource(kubectl, job, False)
        try:
            probe_process.run_probe(kubectl + ["-n", namespace, "wait", "--for=condition=complete",
                "job/" + name, "--timeout=305s"], timeout=310)
            live_job = json.loads(run(kubectl + ["-n", namespace, "get", "job", name, "-o", "json"]))
            metadata, status = live_job["metadata"], live_job["status"]
            job_uid = metadata["uid"]
            if (metadata.get("name") != name or metadata.get("namespace") != namespace or
                    metadata.get("deletionTimestamp") or
                    metadata.get("annotations", {}).get("kerosene.io/update-id") != update_id or
                    not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", job_uid) or
                    status.get("succeeded") != 1 or status.get("failed", 0) != 0 or status.get("active", 0) != 0 or
                    not any(condition.get("type") == "Complete" and condition.get("status") == "True"
                            for condition in status.get("conditions", []))):
                raise ValueError("unexpected completed Job")
            raw_pods = run(kubectl + ["-n", namespace, "get", "pods", "-l",
                "batch.kubernetes.io/controller-uid=" + job_uid, "-o", "json"])
            pod_list = json.loads(raw_pods)
            if not isinstance(pod_list, dict) or not isinstance(pod_list.get("items"), list) or len(pod_list["items"]) != 1:
                raise ValueError("ambiguous migration Pod")
            pod = pod_list["items"][0]
            pod_uid = pod.get("metadata", {}).get("uid")
            record = verify_database_capability_terminal_pod(stack, job, job_uid, pod, pod_uid, operation)
            final_job = json.loads(run(kubectl + ["-n", namespace, "get", "job", name, "-o", "json"]))
            final_pod = json.loads(run(kubectl + ["-n", namespace, "get", "pod", record["podName"], "-o", "json"]))
            if final_job.get("metadata", {}).get("uid") != job_uid:
                raise ValueError("migration Job identity changed")
            final_record = verify_database_capability_terminal_pod(stack, job, job_uid, final_pod, pod_uid, operation)
            if final_record != record:
                raise ValueError("migration Pod changed after observation")
            record["recoveredExisting"] = retained
            records.append(record)
        except (probe_process.ProbeProcessError, RuntimeError, ValueError, KeyError, TypeError,
                json.JSONDecodeError, stack.ApplyBlockedError) as error:
            raise stack.ApplyBlockedError("database migration did not complete with the expected retained Job") from error
    return {"schema": "kerosene.cell.database-migration-execution/v1",
            "updateId": update_id, "jobs": records}


def verify_external_secrets(stack, kubectl, references):
    # A template projects name and key names only; no credential value is
    # emitted, decoded, logged, journaled or included in an error.
    template = '{{.metadata.name}}{{"\\n"}}{{range $key, $_ := .data}}{{$key}}{{"\\n"}}{{end}}'
    for (namespace, name), required_keys in sorted(references.items()):
        try:
            raw = run(kubectl + ["-n", namespace, "get", "secret", name, "-o", "go-template=" + template])
            if len(raw) > 64 * 1024:
                raise ValueError("oversized key inventory")
            lines = raw.decode("ascii").splitlines()
            if not lines or lines[0] != name or len(lines[1:]) != len(set(lines[1:])):
                raise ValueError("invalid Secret identity/key inventory")
            if not required_keys <= set(lines[1:]):
                raise ValueError("missing required Secret key")
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
            raise stack.ApplyBlockedError("required external Secret unavailable or incomplete: " + namespace + "/" + name) from error


def workload_phases(stack, artifact, summary):
    """Plan infrastructure before consumers, without serializing cyclic apps.

    This is startup ordering, not a quorum-preserving update algorithm. The
    separate execution capability gate remains mandatory for real apply.
    Admin/jctl is an operator artifact, never a long-running workload.
    """
    # Both canonical planes embed Node alongside Tor and share its onion
    # identity volume. They are one startup unit, not separable workloads.
    phases = ({"postgres", "redis", "tor", "node"}, {"bitcoin"}, {"lnd"},
              {"vault"}, {"core", "kfe"}, {"web-page"})
    images = {}
    for name, service in summary["services"].items():
        images.setdefault(service["image"], set()).add(name)
    groups = [[] for _ in phases]
    observed = set()
    for resource in artifact["resources"]:
        if resource["kind"] not in WORKLOADS:
            continue
        names = set()
        for container in containers(resource):
            owners = images.get(container.get("image"), set())
            if len(owners) != 1:
                raise stack.ApplyBlockedError("workload image lacks a unique approved component identity")
            names.update(owners)
        if not names or "admin" in names:
            raise stack.ApplyBlockedError("Admin CLI cannot be installed as a runtime workload")
        indexes = {i for i, phase in enumerate(phases) if names & phase}
        if len(indexes) != 1 or not names <= phases[next(iter(indexes))]:
            raise stack.ApplyBlockedError("workload mixes unsupported Cell dependency phases")
        groups[next(iter(indexes))].append(resource)
        # A completed init container does not keep a Cell service running.
        for container in resource.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []):
            observed.update(images[container["image"]])
    missing = set(summary["services"]) - {"admin"} - observed
    if missing:
        raise stack.ApplyBlockedError("missing runtime components: " + ", ".join(sorted(missing)))
    return [sorted(group, key=identity) for group in groups if group]


def critical_replica_topology(stack, artifact, summary):
    """Bind Node/Vault replicas to distinct persistent identities.

    Critical workloads are deliberately one replica per controller. This makes
    a controller-level update the unit of failure and allows the executor to
    prove that it changes no more than one identity at a time.
    """
    service_images = {name: service["image"] for name, service in summary["services"].items()}
    declared_pvcs = {identity(resource) for resource in artifact["resources"]
                     if resource["kind"] == "PersistentVolumeClaim"}
    groups = {"node": [], "vault": []}
    storage_owners = {}
    for resource in artifact["resources"]:
        if resource["kind"] not in WORKLOADS:
            continue
        present = {name for name in groups
                   if any(container.get("image") == service_images[name] for container in containers(resource))}
        if not present:
            continue
        if len(present) != 1:
            raise stack.ApplyBlockedError("Node and Vault cannot share one critical workload controller")
        component = present.pop()
        if resource.get("spec", {}).get("replicas") != 1:
            raise stack.ApplyBlockedError(f"{component} requires one replica per persistent workload controller")
        pod_spec = resource["spec"]["template"]["spec"]
        critical_containers = [container for container in pod_spec.get("containers", [])
                               if container.get("image") == service_images[component]]
        mounted = {mount.get("name") for container in critical_containers
                   for mount in container.get("volumeMounts", []) if isinstance(mount.get("name"), str)}
        ns, _, name = identity(resource)
        persistent = set()
        for volume in pod_spec.get("volumes", []):
            claim = volume.get("persistentVolumeClaim", {}).get("claimName")
            if volume.get("name") in mounted and isinstance(claim, str):
                claim_identity = (ns, "PersistentVolumeClaim", claim)
                if claim_identity not in declared_pvcs:
                    raise stack.ApplyBlockedError(f"{component} persistent identity claim is not declared in the approved artifact")
                persistent.add(f"pvc:{ns}/{claim}")
        if resource["kind"] == "StatefulSet":
            for template in resource["spec"].get("volumeClaimTemplates", []):
                template_name = template.get("metadata", {}).get("name")
                if template_name in mounted:
                    persistent.add(f"statefulset:{ns}/{name}/{template_name}")
        if len(persistent) != 1:
            raise stack.ApplyBlockedError(f"{component} must mount exactly one approved persistent identity volume")
        storage = persistent.pop()
        if storage in storage_owners:
            raise stack.ApplyBlockedError(f"critical persistent identity is shared by {storage_owners[storage]} and {ns}/{name}")
        storage_owners[storage] = f"{ns}/{name}"
        groups[component].append({"resource": resource, "storage": storage})
    expected_vaults = summary["vaultCompatibility"]["members"]
    if len(groups["vault"]) != expected_vaults:
        raise stack.ApplyBlockedError(f"approved topology requires exactly {expected_vaults} independent Vault workloads")
    if not groups["node"]:
        raise stack.ApplyBlockedError("approved topology requires at least one persistent Node workload")
    for component in groups:
        groups[component].sort(key=lambda item: identity(item["resource"]))
    return groups


def verify_critical_group_available(stack, kubectl, component, members, threshold):
    """Require the complete critical group ready and identities unchanged."""
    ready = 0
    observations = []
    for member in members:
        resource = member["resource"]
        ns, kind, name = identity(resource)
        try:
            live = json.loads(run(kubectl + ["-n", ns, "get", kind.lower(), name, "-o", "json"]))
            status = live.get("status", {})
            if (identity(live) != (ns, kind, name) or not live.get("metadata", {}).get("uid") or
                    not live["metadata"].get("resourceVersion") or
                    live["metadata"].get("deletionTimestamp") or live.get("spec", {}).get("replicas") != 1 or
                    status.get("observedGeneration", 0) < live["metadata"].get("generation", 1) or
                    status.get("readyReplicas") != 1):
                raise ValueError("critical workload is not stably ready")
            # Compare storage directly; full topology cardinality belongs to
            # critical_replica_topology and would reject this one-member view.
            pod_spec = live["spec"]["template"]["spec"]
            mounted = {mount.get("name") for container in containers(resource)
                       for mount in container.get("volumeMounts", [])}
            observed_storage = set()
            for volume in pod_spec.get("volumes", []):
                claim = volume.get("persistentVolumeClaim", {}).get("claimName")
                if volume.get("name") in mounted and claim:
                    observed_storage.add(f"pvc:{ns}/{claim}")
            if kind == "StatefulSet":
                for template in live["spec"].get("volumeClaimTemplates", []):
                    template_name = template.get("metadata", {}).get("name")
                    if template_name in mounted:
                        observed_storage.add(f"statefulset:{ns}/{name}/{template_name}")
            if observed_storage != {member["storage"]}:
                raise ValueError("persistent identity binding changed")
            ready += 1
            observations.append({"identity": [ns, kind, name], "uid": live["metadata"]["uid"],
                                 "resourceVersion": live["metadata"]["resourceVersion"],
                                 "storage": member["storage"]})
        except (RuntimeError, ValueError, TypeError, KeyError, json.JSONDecodeError,
                OSError, subprocess.TimeoutExpired) as error:
            raise stack.ApplyBlockedError(f"{component} replica group is not safe to mutate") from error
    if ready < threshold:
        raise stack.ApplyBlockedError(f"{component} ready replicas are below the required threshold")
    return observations


def verify_running(kubectl, resource):
    """Require observed generation, desired ready replicas and named live images.

    imageID may be an OCI platform manifest digest rather than the image's
    index digest; named image/ownership/revision checks + runtime imageID are
    recorded, not falsely presented as a registry provenance verification.
    """
    ns, kind, name = identity(resource)
    live = json.loads(run(kubectl + ["-n", ns, "get", kind.lower(), name, "-o", "json"]))
    if identity(live) != (ns, kind, name) or not live["metadata"].get("uid") or live["metadata"].get("deletionTimestamp"):
        raise RuntimeError("live workload identity is missing or changed")
    workload_uid = live["metadata"]["uid"]
    expected = resource["spec"]["replicas"]
    if type(expected) is not int or expected < 1 or type(live["spec"].get("replicas")) is not int or live["spec"]["replicas"] != expected:
        raise RuntimeError("live replica target differs from approved workload")
    def startup_configuration(items):
        result = {}
        for container in items:
            if container["name"] in result:
                raise RuntimeError("duplicate runtime container identity")
            result[container["name"]] = {key: container.get(key, []) for key in ("command", "args", "env", "envFrom")}
        return result
    approved_startup = startup_configuration(containers(resource))
    if startup_configuration(containers(live)) != approved_startup:
        raise RuntimeError("live workload startup configuration differs from approved manifest")
    status = live.get("status", {})
    if status.get("observedGeneration", 0) < live["metadata"].get("generation", 1):
        raise RuntimeError(f"controller has not observed {kind}/{name}")
    if any(type(status.get(key)) is not int or status[key] != expected for key in ("readyReplicas", "updatedReplicas")):
        raise RuntimeError(f"not all desired replicas are updated and ready for {kind}/{name}")
    selector = live["spec"]["selector"].get("matchLabels", {})
    if not selector or live["spec"]["selector"].get("matchExpressions"):
        raise RuntimeError("unsupported or empty workload selector")
    labels = ",".join(f"{key}={value}" for key, value in sorted(selector.items()))
    def controller(resource, owner_kind, owner_uids):
        owners = [o for o in resource.get("metadata", {}).get("ownerReferences", []) if o.get("controller") is True]
        return len(owners) == 1 and owners[0].get("kind") == owner_kind and owners[0].get("uid") in owner_uids
    if kind == "Deployment":
        revision = live["metadata"].get("annotations", {}).get("deployment.kubernetes.io/revision")
        if not isinstance(revision, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", revision):
            raise RuntimeError("Deployment lacks a current rollout revision")
        replicasets = json.loads(run(kubectl + ["-n", ns, "get", "replicasets", "-l", labels, "-o", "json"]))["items"]
        owner_uids = {r["metadata"]["uid"] for r in replicasets if r["metadata"].get("uid")
            and r["metadata"].get("namespace") == ns and not r["metadata"].get("deletionTimestamp")
            and controller(r, "Deployment", {workload_uid})
            and r["metadata"].get("annotations", {}).get("deployment.kubernetes.io/revision") == revision}
        owner_kind = "ReplicaSet"
    else:
        revision = status.get("updateRevision")
        if not isinstance(revision, str) or not revision or status.get("currentRevision") != revision:
            raise RuntimeError("StatefulSet rollout revision is incomplete")
        owner_uids, owner_kind = {workload_uid}, "StatefulSet"
    pods = json.loads(run(kubectl + ["-n", ns, "get", "pods", "-l", labels, "-o", "json"]))["items"]
    active = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
    if len(active) != expected:
        raise RuntimeError("live pod count does not equal approved replicas")
    images = {c["name"]: c["image"] for c in containers(resource)}
    records = []
    for pod in active:
        if pod["metadata"].get("namespace") != ns or not pod["metadata"].get("uid") or not controller(pod, owner_kind, owner_uids):
            raise RuntimeError("live pod does not belong to the updated workload")
        if kind == "StatefulSet" and pod["metadata"].get("labels", {}).get("controller-revision-hash") != revision:
            raise RuntimeError("live pod belongs to an old StatefulSet revision")
        readiness = pod.get("status", {}).get("conditions", [])
        if not any(c.get("type") == "Ready" and c.get("status") == "True" for c in readiness):
            raise RuntimeError("live pod is not ready")
        pod_spec = pod["spec"]
        actual = {c["name"]: c["image"] for c in pod_spec.get("containers", []) + pod_spec.get("initContainers", [])}
        if actual != images:
            raise RuntimeError("live pod images differ from approved named containers")
        if startup_configuration(pod_spec.get("containers", []) + pod_spec.get("initContainers", [])) != approved_startup:
            raise RuntimeError("live pod startup configuration differs from approved manifest")
        pod_status = pod.get("status", {})
        statuses = pod_status.get("containerStatuses", []) + pod_status.get("initContainerStatuses", [])
        if {s["name"] for s in statuses} != set(images) or any(not s.get("imageID") for s in statuses):
            raise RuntimeError("missing live runtime image identity")
        records.append({"workloadUid": workload_uid, "revision": revision, "podUid": pod["metadata"]["uid"], "images": [{"name": s["name"], "imageID": s["imageID"]} for s in statuses]})
    final = json.loads(run(kubectl + ["-n", ns, "get", kind.lower(), name, "-o", "json"]))
    def observation(workload):
        meta = workload.get("metadata", {})
        status = workload.get("status", {})
        return {"identity": identity(workload), "uid": meta.get("uid"),
            "generation": meta.get("generation"), "deletionTimestamp": meta.get("deletionTimestamp"),
            "spec": workload.get("spec"), "revision": meta.get("annotations", {}).get("deployment.kubernetes.io/revision"),
            "status": {key: status.get(key) for key in ("observedGeneration", "readyReplicas", "updatedReplicas", "currentRevision", "updateRevision")}}
    if observation(final) != observation(live):
        raise RuntimeError("workload changed while collecting rollout readiness")
    return records


def verify_managed_configmaps(stack, kubectl, artifact):
    """Verify managed nonsecret configuration; never fetch Secret values."""
    records = []
    for resource in sorted((r for r in artifact["resources"] if r["kind"] == "ConfigMap"), key=identity):
        ns, kind, name = identity(resource)
        try:
            live = json.loads(run(kubectl + ["-n", ns, "get", "configmap", name, "-o", "json"]))
            if identity(live) != (ns, kind, name) or live.get("apiVersion") != "v1" or not live["metadata"].get("uid") or live["metadata"].get("deletionTimestamp"):
                raise ValueError("changed identity")
            approved = {key: resource.get(key, {}) for key in ("data", "binaryData")}
            observed = {key: live.get(key, {}) for key in ("data", "binaryData")}
            if observed != approved:
                raise ValueError("changed configuration")
            records.append({"namespace": ns, "name": name, "uid": live["metadata"]["uid"], "contentDigest": digest(observed)})
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, subprocess.TimeoutExpired):
            raise stack.ApplyBlockedError("managed ConfigMap unavailable or differs from approved configuration") from None
    return records


def execute(stack, artifact, summary, args, checkpoint):
    if not args.dry_run:
        require_execution_capabilities(stack)
    if not args.cell_dir:
        raise stack.ApplyBlockedError("approved manifest execution requires an initialized Cell directory")
    config = load_config(stack, args.cell_dir)
    verify_bootstrap_trust(stack, args.cell_dir, config)
    kubectl = kubectl_command(stack, config)
    # No inherited tool wrappers or gate bypasses in the approved path.
    if any(os.environ.get(k) for k in ("KUBECTL", "KEROSENE_SKIP_STAGING_SMOKES", "KEROSENE_FORCE_CONFLICTS",
                                     "KEROSENE_STAGING_NAMESPACE", "KEROSENE_STAGING_VAULT_NAMESPACE",
                                     "KEROSENE_STAGING_LOGIN_PORT", "KEROSENE_STAGING_VAULT_SMOKE_PORT")):
        raise stack.ApplyBlockedError("approved execution forbids tool/gate override environment variables")
    prerequisites = [r for r in artifact["resources"] if r["kind"] not in WORKLOADS]
    phases = workload_phases(stack, artifact, summary)
    critical = critical_replica_topology(stack, artifact, summary)
    critical_by_identity = {identity(member["resource"]): (component, member)
                            for component, members in critical.items() for member in members}
    # Apply non-runtime inputs first. Explicit ordering prevents HPA creation
    # from silently changing approved replicas before validation.
    prerequisites.sort(key=lambda r: (0 if r["kind"] == "Namespace" else 2 if r["kind"] == "HorizontalPodAutoscaler" else 1, identity(r)))
    # Validate all unsupported policies before the first Kubernetes write.
    if any(r["kind"] == "HorizontalPodAutoscaler" for r in prerequisites):
        raise stack.ApplyBlockedError("HPA requires a separately approved freeze/restore policy during Cell update")
    fresh_install = getattr(args, "command", None) == "install"
    prewrite_initial_recovery = (getattr(args, "command", None) == "recover" and
                                 getattr(args, "recover_initial_install", False))
    postwrite_initial_recovery = bool(getattr(args, "_postwrite_initial_recovery", False))
    initial_install = fresh_install or prewrite_initial_recovery or postwrite_initial_recovery
    if initial_install:
        if fresh_install or prewrite_initial_recovery:
            verify_empty_installation(stack, kubectl)
        if not args.dry_run and initial_database_plan(stack, artifact, summary) is None:
            raise stack.ApplyBlockedError("initial installation requires an approved database plan")
        verify_external_secrets(stack, kubectl, required_secret_references(stack, artifact))
        if not args.dry_run:
            if postwrite_initial_recovery:
                checkpoint("initial-admission-retained", args._postwrite_initial_recovery_evidence)
            else:
                operation = "inspect-recovery" if prewrite_initial_recovery else "consume"
                admission = consume_initial_admission(stack, config, summary, args, operation)
                checkpoint("initial-admission-recovered" if operation == "inspect-recovery" else "initial-admission-consumed", admission)
    else:
        verify_external_secrets(stack, kubectl, required_secret_references(stack, artifact))
    if not args.dry_run:
        receipt = admin_install.install(stack, args.cell_dir, config["cellId"], summary, artifact["admin"]["config"],
                                       stack.canonical_digest(args._release), run,
                                       getattr(args, "admin_oci_archive", None))
        checkpoint("admin-installed", receipt)
    for resource in prerequisites:
        apply_resource(kubectl, resource, args.dry_run)
    if not args.dry_run:
        configuration_records = verify_managed_configmaps(stack, kubectl, artifact)
        checkpoint("configuration-verified", {"configMaps": configuration_records})
    migrations_executed = False
    application_images = {summary["services"][name]["image"] for name in ("core", "kfe")}
    for phase in phases:
        if not args.dry_run and verify_managed_configmaps(stack, kubectl, artifact) != configuration_records:
            raise stack.ApplyBlockedError("managed ConfigMaps changed before the next Cell phase")
        if (initial_install and not args.dry_run and not migrations_executed and
                any(container.get("image") in application_images for resource in phase for container in containers(resource))):
            migration = execute_initial_database_migrations(
                stack, kubectl, artifact, summary, summary["_canonicalDigest"],
                recover_existing=postwrite_initial_recovery)
            checkpoint("initial-database-migrations-validated", migration)
            migrations_executed = True
        # Core and KFE have reciprocal integration references. Submit the
        # entire noncritical phase before waiting, otherwise the first
        # readiness gate can deadlock bootstrap. Existing Node/Vault identities
        # are instead mutated one controller at a time.
        batch = phase if initial_install or args.dry_run else [r for r in phase if identity(r) not in critical_by_identity]
        for resource in batch:
            label = "/".join(identity(resource))
            if not args.dry_run and not initial_install:
                verify_maintenance(stack, args)
            checkpoint("before:" + label, {})
            apply_resource(kubectl, resource, args.dry_run)
        if args.dry_run:
            continue
        for resource in batch:
            label = "/".join(identity(resource))
            ns, kind, name = identity(resource)
            run(kubectl + ["-n", ns, "rollout", "status", f"{kind.lower()}/{name}", "--timeout=110s"])
            checkpoint("ready:" + label, {"runtime": verify_running(kubectl, resource)})
        if not initial_install:
            for resource in (r for r in phase if identity(r) in critical_by_identity):
                component, _ = critical_by_identity[identity(resource)]
                members = critical[component]
                threshold = summary["vaultCompatibility"]["threshold"] if component == "vault" else len(members)
                before = verify_critical_group_available(stack, kubectl, component, members, threshold)
                label = "/".join(identity(resource))
                verify_maintenance(stack, args)
                checkpoint("critical-group-ready-before:" + label, {"component": component, "members": before})
                checkpoint("before:" + label, {})
                precondition = next((item for item in before if tuple(item["identity"]) == identity(resource)), None)
                if precondition is None:
                    raise stack.ApplyBlockedError("critical workload precondition is missing")
                apply_resource(kubectl, resource, False, precondition)
                ns, kind, name = identity(resource)
                run(kubectl + ["-n", ns, "rollout", "status", f"{kind.lower()}/{name}", "--timeout=110s"])
                runtime = verify_running(kubectl, resource)
                after = verify_critical_group_available(stack, kubectl, component, members, threshold)
                checkpoint("ready:" + label, {"runtime": runtime})
                checkpoint("critical-group-ready-after:" + label, {"component": component, "members": after})
    if initial_install and not args.dry_run and not migrations_executed:
        raise stack.ApplyBlockedError("initial database migrations were not reached before application workloads")
    if not args.dry_run:
        # Existing operational gates execute from installed controller, not
        # executable content supplied in source/archive artifacts.
        root = Path(stack.__file__).resolve().parents[1]
        # Recheck the bound cluster identity after rollout, then pin all smoke
        # subprocesses too. Legacy scripts alone used the ambient context.
        kubectl = kubectl_command(stack, config)
        if verify_managed_configmaps(stack, kubectl, artifact) != configuration_records:
            raise stack.ApplyBlockedError("managed ConfigMaps changed during Cell rollout")
        binding = ["--cell-binding", kubectl[0], config["cluster"]["kubeconfig"], config["cluster"]["context"]]
        for script in ("smoke-staging-vault.sh", "smoke-staging.sh"):
            run(["bash", str(root / "infra/kubernetes/scripts" / script), *binding])
        checkpoint("legacy-smokes-passed", {"completeCellAcceptance": False})
        acceptance = verify_complete_cell_acceptance(stack, config, args.cell_dir, summary, args)
        checkpoint("complete-cell-acceptance-passed", acceptance)


def load_config(stack, directory):
    root = Path(directory)
    if root.is_symlink():
        raise stack.ReleaseValidationError("Cell directory cannot be a symlink")
    return stack.read_json_document(str(root / "cell.json"), "Cell configuration")


def verify_bootstrap_trust(stack, directory, config):
    stack.require_keys(config, "Cell configuration", ("schema", "cellId", "environment", "trustDigests", "autoActivateVaultSigners"), ("consensusVerifier", "acceptanceVerifier", "cluster"))
    if config["schema"] != "kerosene.stack.cell/v1" or config["environment"] != "staging-cell" or config["autoActivateVaultSigners"] is not False:
        raise stack.ReleaseValidationError("unsupported Cell configuration or forbidden signer activation")
    expected_names = {"tuf-root.json", "validator-roster.json", "vault-roster.json", "snapshot-provider.pub"}
    if "consensusVerifier" in config:
        expected_names.add("consensus-anchor.json")
    if set(config["trustDigests"]) != expected_names:
        raise stack.ReleaseValidationError("unexpected bootstrap trust files")
    for name, expected in config["trustDigests"].items():
        raw = stack.read_regular_file_bytes(Path(directory) / name, "bootstrap trust", stack.MAX_TUF_METADATA_BYTES)
        if "sha256:" + hashlib.sha256(raw).hexdigest() != expected:
            raise stack.ReleaseValidationError("bootstrap trust anchor changed: " + name)


def verify_consensus(stack, directory, config, proof, release_digest, summary):
    verifier = config.get("consensusVerifier")
    if not verifier:
        raise stack.ApplyBlockedError("Cell bootstrap lacks independently provisioned consensus anchor and pinned verifier")
    stack.require_keys(verifier, "consensus verifier", ("path", "digest"))
    path = Path(verifier["path"])
    if not path.is_absolute():
        raise stack.ApplyBlockedError("consensus verifier must have an absolute installed path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        import stat
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024 * 1024 or info.st_mode & 0o022:
            raise stack.ApplyBlockedError("consensus verifier must be bounded, regular and protected against shared writes")
        h = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            h.update(chunk)
        if "sha256:" + h.hexdigest() != verifier["digest"]:
            raise stack.ApplyBlockedError("installed consensus verifier digest changed")
        command = [f"/proc/self/fd/{fd}", "verify", "--trusted-anchor", str(Path(directory) / "consensus-anchor.json"), "--proof", proof, "--release-digest", release_digest, "--sequence", str(summary["sequence"])]
        result = subprocess.run(command, pass_fds=(fd,), capture_output=True, timeout=60, check=False)
        if result.returncode or len(result.stdout) > 16384:
            raise stack.ApplyBlockedError("real ordered consensus verification failed")
    finally:
        os.close(fd)
    evidence = json.loads(result.stdout)
    expected = {"schema": "kerosene.release-consensus-verification/v1", "releaseLockCanonicalDigest": release_digest, "networkId": summary["bft"]["networkId"], "epoch": summary["bft"]["epoch"], "sequence": summary["sequence"]}
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise stack.ApplyBlockedError("consensus proof domain differs from approved release")
    evidence["signaturesVerified"] = summary["bft"]["threshold"]
    evidence["consensusVerified"] = True
    return evidence


def verify_initial_admission_recovery_state(stack, previous, summary, args):
    if not isinstance(previous, dict) or previous.get("schema") != "kerosene.stack.update-state/v1":
        raise stack.ApplyBlockedError("initial admission recovery requires the retained failed installation journal")
    expected = {"updateId": summary["_canonicalDigest"], "sequence": summary["sequence"],
                "environment": args.environment, "changeId": args.change_id,
                "operatorId": args.operator_id, "status": "failed", "phase": "failed",
                "manualRecoveryRequired": True}
    if any(previous.get(key) != value for key, value in expected.items()):
        raise stack.ApplyBlockedError("initial admission recovery journal does not bind this exact failed installation")
    if args.resume_update_id != summary["_canonicalDigest"]:
        raise stack.ApplyBlockedError("initial admission recovery requires the exact --resume-update-id")
    events = previous.get("events")
    if (not isinstance(events, list) or [event.get("phase") if isinstance(event, dict) else None for event in events]
            != ["snapshot-accepted", "rollout-started", "failed"] or
            previous.get("failure") != "Bank initial admission failed or is uncertain; inspect recovery before retry"):
        raise stack.ApplyBlockedError("journal does not prove a pre-write uncertain initial admission")
    return {"updateId": summary["_canonicalDigest"], "changeId": args.change_id,
            "operatorId": args.operator_id, "priorPhase": "failed"}


def verify_postwrite_initial_install_recovery_state(stack, previous, summary, args):
    """Classify an exact failed initial install after Bank admission.

    Returns None for an ordinary update recovery. Once an initial admission is
    observed, every mismatch fails closed instead of falling through to update
    semantics or attempting to consume the admission nonce again.
    """
    if not isinstance(previous, dict) or previous.get("schema") != "kerosene.stack.update-state/v1":
        return None
    events = previous.get("events")
    if not isinstance(events, list) or len(events) > 4096 or any(not isinstance(event, dict) for event in events):
        return None
    phases = [event.get("phase") for event in events]
    admission_phases = {"initial-admission-consumed", "initial-admission-recovered",
                        "initial-admission-retained"}
    observed = [phase for phase in phases if phase in admission_phases]
    if not observed:
        return None
    expected = {"updateId": summary["_canonicalDigest"], "sequence": summary["sequence"],
                "environment": args.environment, "changeId": args.change_id,
                "operatorId": args.operator_id, "status": "failed", "phase": "failed",
                "manualRecoveryRequired": True}
    if any(previous.get(key) != value for key, value in expected.items()):
        raise stack.ApplyBlockedError("post-write initial recovery journal does not bind this exact failed installation")
    if getattr(args, "resume_update_id", None) != summary["_canonicalDigest"]:
        raise stack.ApplyBlockedError("post-write initial recovery requires the exact --resume-update-id")
    if getattr(args, "recover_initial_install", False):
        raise stack.ApplyBlockedError("post-write initial recovery must not use --recover-initial-install")
    if (len(observed) != 1 or phases[:2] != ["snapshot-accepted", "rollout-started"] or
            phases[-1:] != ["failed"] or "validate-and-commit" in phases):
        raise stack.ApplyBlockedError("journal does not prove one incomplete post-admission initial installation")
    admission_index = phases.index(observed[0])
    if admission_index < 2 or admission_index >= len(phases) - 1:
        raise stack.ApplyBlockedError("initial admission checkpoint is out of order")
    return {"updateId": summary["_canonicalDigest"], "changeId": args.change_id,
            "operatorId": args.operator_id, "priorAdmissionPhase": observed[0],
            "bankAdmissionReused": False}


def consume_initial_admission(stack, config, summary, args, operation="consume"):
    if operation not in {"consume", "inspect-recovery"}:
        raise stack.ApplyBlockedError("unsupported Bank initial admission operation")
    required = tuple(getattr(args, name, None) for name in
                     ("initial_admission", "admission_endpoint", "admission_ca",
                      "admission_cert", "admission_key", "consensus_proof",
                      "operator_id", "change_id"))
    if any(not value for value in required):
        raise stack.ApplyBlockedError("initial installation requires Bank initial admission envelope, mTLS endpoint and identity")
    cluster = config.get("cluster")
    if not cluster or not cluster.get("systemNamespaceUid"):
        raise stack.ApplyBlockedError("initial admission requires the independently observed cluster identity")
    try:
        endpoint = urllib.parse.urlsplit(args.admission_endpoint)
        port = endpoint.port
    except ValueError as error:
        raise stack.ApplyBlockedError("invalid Bank admission endpoint") from error
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username is not None or
            endpoint.password is not None or endpoint.query or endpoint.fragment or
            endpoint.path not in {"", "/"} or (port is not None and not 1 <= port <= 65535)):
        raise stack.ApplyBlockedError("Bank admission endpoint must be a credential-free HTTPS origin")
    envelope = stack.read_json_document(args.initial_admission, "initial Cell admission envelope")
    proof = stack.read_json_document(args.consensus_proof, "ordered consensus proof")
    admission = envelope.get("admission") if isinstance(envelope, dict) else None
    fields = {"cellId", "changeId", "clusterUid", "epoch", "expiresAtUnixSeconds",
              "issuedAtUnixSeconds", "networkId", "nonce", "operatorId",
              "releaseApprovalDigest", "schema"}
    if not isinstance(admission, dict) or set(admission) != fields:
        raise stack.ApplyBlockedError("initial admission payload shape is invalid")
    expected = {"cellId": config["cellId"], "clusterUid": cluster["systemNamespaceUid"],
                "operatorId": args.operator_id, "changeId": args.change_id,
                "networkId": summary["bft"]["networkId"], "epoch": summary["bft"]["epoch"]}
    if any(admission.get(key) != value for key, value in expected.items()):
        raise stack.ApplyBlockedError("initial admission identity or cluster binding differs")
    request = {"schema": "kerosene.bank-cell-admission-request/v1",
               "releaseLockCanonicalDigest": summary["_canonicalDigest"],
               "sequence": summary["sequence"], "proof": proof, "envelope": envelope}
    ca = stack.read_regular_file_bytes(Path(args.admission_ca), "Bank admission CA", 1024 * 1024)
    certificate = stack.read_regular_file_bytes(Path(args.admission_cert), "Bank admission certificate", 1024 * 1024)
    private_key = stack.read_regular_file_bytes(Path(args.admission_key), "Bank admission private key", 1024 * 1024)
    try:
        context = ssl.create_default_context(cadata=ca.decode("ascii"))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with tempfile.TemporaryDirectory(prefix="kerosene-admission-tls-") as directory:
            cert_path, key_path = Path(directory) / "client.crt", Path(directory) / "client.key"
            cert_path.write_bytes(certificate)
            key_path.write_bytes(private_key)
            os.chmod(cert_path, 0o600)
            os.chmod(key_path, 0o600)
            context.load_cert_chain(cert_path, key_path)
    except (UnicodeDecodeError, ValueError, OSError, ssl.SSLError) as error:
        raise stack.ApplyBlockedError("protected Bank admission TLS identity is invalid") from error
    url = urllib.parse.urlunsplit((endpoint.scheme, endpoint.netloc,
                                  "/v1/cell/admissions/" + operation, "", ""))
    http_request = urllib.request.Request(url, data=canonical(request), method="POST",
                                          headers={"Content-Type": "application/json", "Accept": "application/json"})
    uncertain = "Bank initial admission failed or is uncertain; inspect recovery before retry"
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise stack.ApplyBlockedError(uncertain)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPSHandler(context=context), NoRedirect())
    try:
        with opener.open(http_request, timeout=45) as response:
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                raise stack.ApplyBlockedError(uncertain)
            raw = response.read(16385)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, ssl.SSLError) as error:
        raise stack.ApplyBlockedError(uncertain) from error
    if len(raw) > 16384:
        raise stack.ApplyBlockedError(uncertain)
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate Bank admission response field")
            result[key] = value
        return result
    try:
        result = json.loads(raw, object_pairs_hook=unique_fields,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite Bank response")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise stack.ApplyBlockedError(uncertain) from error
    if not isinstance(result, dict) or set(result) != {"schema", "admissionDigest", "consensus", "nonceConsumed", "installAuthorized"}:
        raise stack.ApplyBlockedError(uncertain)
    consensus = result.get("consensus")
    expected_consensus = {"schema": "kerosene.release-consensus-verification/v1",
                          "releaseLockCanonicalDigest": summary["_canonicalDigest"],
                          "networkId": summary["bft"]["networkId"], "epoch": summary["bft"]["epoch"],
                          "sequence": summary["sequence"]}
    admission_digest = "sha256:" + hashlib.sha256(canonical(admission)).hexdigest()
    if (result["schema"] != "kerosene.cell-admission-verification/v1" or
            result["admissionDigest"] != admission_digest or result["nonceConsumed"] is not True or
            result["installAuthorized"] is not False or not isinstance(consensus, dict) or
            any(consensus.get(key) != value for key, value in expected_consensus.items())):
        raise stack.ApplyBlockedError(uncertain)
    return {"schema": result["schema"], "admissionDigest": admission_digest,
            "nonceConsumed": True, "bankInstallAuthorized": False,
            "cellId": config["cellId"], "clusterUid": cluster["systemNamespaceUid"],
            "operation": operation}


def verify_maintenance(stack, args):
    required = (args.change_id, args.operator_id, args.maintenance_endpoint, args.maintenance_ca, args.maintenance_cert, args.maintenance_key, args.maintenance_token_file)
    if not all(required):
        raise stack.ApplyBlockedError("real apply requires change/operator attribution and live authenticated KFE maintenance endpoint plus mTLS/ADMIN session references")
    url = urllib.parse.urlsplit(args.maintenance_endpoint)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment or url.query or url.path != "/api/admin/kfe/maintenance/status":
        raise stack.ApplyBlockedError("maintenance endpoint must be the exact HTTPS status route without credentials/redirects")
    token = stack.read_regular_file_bytes(Path(args.maintenance_token_file), "operator session reference", 16384).decode().strip()
    if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise stack.ApplyBlockedError("invalid authenticated operator session token")
    context = ssl.create_default_context(cafile=args.maintenance_ca)
    context.load_cert_chain(args.maintenance_cert, args.maintenance_key)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise stack.ApplyBlockedError("maintenance redirect forbidden")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), NoRedirect())
    try:
        with opener.open(urllib.request.Request(args.maintenance_endpoint, headers={"Authorization": "Bearer " + token, "Accept": "application/json"}), timeout=15) as response:
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536:
                raise stack.ApplyBlockedError("maintenance response is invalid or oversized")
        def unique_fields(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate maintenance JSON field")
                result[key] = value
            return result
        def invalid_constant(value):
            raise ValueError("non-finite maintenance JSON value")
        status = json.loads(raw, object_pairs_hook=unique_fields, parse_constant=invalid_constant)
    except (OSError, ValueError) as error:
        raise stack.ApplyBlockedError("authenticated maintenance status unavailable") from error
    return validate_maintenance(stack, status, args.change_id)


def validate_maintenance(stack, status, change_id, now=None):
    status = stack.require_keys(status, "live KFE maintenance", ("schema", "mode", "changeId", "revision", "observedAt", "safeToUpdate", "blockers"))
    if status.get("schema") != "kerosene.kfe-maintenance/v1" or status.get("mode") != "DRAINING" or status.get("changeId") != change_id or status.get("safeToUpdate") is not True:
        raise stack.ApplyBlockedError("KFE is not safely drained for this exact operator change")
    stack.require_integer(status.get("revision"), "KFE maintenance revision", minimum=1)
    blockers = stack.require_object(status.get("blockers"), "KFE blockers")
    if not {"mutationCoverageUnknown", "callbackCoverageUnknown", "readSideEffectsUnknown"}.issubset(blockers):
        raise stack.ApplyBlockedError("KFE maintenance coverage evidence is missing")
    if any(type(value) is not int or value != 0 for value in blockers.values()):
        raise stack.ApplyBlockedError("KFE reports unresolved/unknown mutation blockers")
    observed = stack.parse_rfc3339(status.get("observedAt"), "KFE observedAt")
    now = now or dt.datetime.now(dt.timezone.utc)
    if observed > now + dt.timedelta(seconds=30) or now - observed > dt.timedelta(seconds=30):
        raise stack.ApplyBlockedError("live KFE drain observation is stale or future-dated")
    return {key: status[key] for key in ("schema", "mode", "changeId", "revision", "observedAt", "safeToUpdate", "blockers")}


def verify_recovery_plan(stack, release, summary, args):
    if not args.recovery_evidence:
        raise stack.ApplyBlockedError("real apply requires independently quorum-approved tested recovery evidence")
    plan = stack.read_json_document(args.recovery_evidence, "tested recovery plan")
    stack.require_keys(plan, "recovery plan", ("schema", "networkId", "migrationId", "classification", "services", "testedAt", "expiresAt", "testEvidenceDigest", "signatures"))
    if stack.canonical_digest(plan) != summary["migration"]["recoveryEvidenceDigest"]:
        raise stack.ApplyBlockedError("recovery plan digest is not authorized by this release")
    if plan["schema"] != "kerosene.tested-recovery-plan/v1" or plan["networkId"] != summary["networkId"] or plan["migrationId"] != summary["migration"]["id"] or plan["classification"] != summary["migration"]["classification"]:
        raise stack.ApplyBlockedError("recovery plan domain/classification mismatch")
    if plan["services"] != summary["services"]:
        raise stack.ApplyBlockedError("tested recovery must bind every exact target service image and configuration")
    tested = stack.parse_rfc3339(plan["testedAt"], "recovery testedAt")
    expires = stack.parse_rfc3339(plan["expiresAt"], "recovery expiresAt")
    now = dt.datetime.now(dt.timezone.utc)
    if tested > now + dt.timedelta(seconds=30) or expires <= now or expires <= tested or expires - tested > dt.timedelta(days=30):
        raise stack.ApplyBlockedError("tested recovery evidence expired, future-dated or overlong")
    stack.require_digest(plan["testEvidenceDigest"], "independent recovery test evidence digest")
    payload = dict(plan)
    signatures = payload.pop("signatures")
    count = stack.verify_signature_set(stack.canonical_bytes(payload), signatures, stack.validate_roster(args.validator_roster, summary), summary["bft"]["threshold"], "recovery signatures")
    return {"schema": plan["schema"], "testEvidenceDigest": plan["testEvidenceDigest"], "signaturesVerified": count, "testedAt": plan["testedAt"]}


def verify_complete_cell_acceptance(stack, config, cell_dir, summary, args):
    """Run the bootstrap-pinned whole-Cell verifier and validate exact output.

    The verifier owns protocol-specific authenticated probes. The lifecycle
    controller owns executable identity, cluster/release binding, freshness and
    the fail-closed acceptance contract. No signer is activated and maintenance
    is not resumed by this operation.
    """
    verifier = config.get("acceptanceVerifier")
    cluster = config.get("cluster")
    if not isinstance(verifier, dict) or not isinstance(cluster, dict):
        raise stack.ApplyBlockedError("Cell bootstrap lacks a pinned complete-Cell acceptance verifier or cluster binding")
    stack.require_keys(verifier, "acceptance verifier", ("path", "digest"))
    path = Path(verifier["path"])
    if not path.is_absolute():
        raise stack.ApplyBlockedError("acceptance verifier must have an absolute installed path")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        import stat
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024 * 1024 or
                info.st_mode & 0o022 or not info.st_mode & 0o111):
            raise stack.ApplyBlockedError("acceptance verifier must be bounded, executable and protected against shared writes")
        h = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            h.update(chunk)
        if "sha256:" + h.hexdigest() != verifier["digest"]:
            raise stack.ApplyBlockedError("installed acceptance verifier digest changed")
        command = [f"/proc/self/fd/{descriptor}", "verify", "--cell-dir", str(Path(cell_dir).absolute()),
                   "--cell-id", config["cellId"], "--cluster-uid", cluster["systemNamespaceUid"],
                   "--kubeconfig", cluster["kubeconfig"], "--context", cluster["context"],
                   "--release-digest", summary["_canonicalDigest"], "--sequence", str(summary["sequence"]),
                   "--change-id", args.change_id, "--operator-id", args.operator_id]
        result = subprocess.run(command, pass_fds=(descriptor,), capture_output=True, timeout=300, check=False)
    finally:
        os.close(descriptor)
    if result.returncode or len(result.stdout) > 64 * 1024:
        raise stack.ApplyBlockedError("complete-Cell acceptance verifier failed")
    try:
        def unique_fields(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate acceptance JSON field")
                value[key] = item
            return value
        report = json.loads(result.stdout, object_pairs_hook=unique_fields,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, TypeError) as error:
        raise stack.ApplyBlockedError("complete-Cell acceptance output is invalid") from error
    required = ("schema", "cellId", "clusterUid", "releaseLockCanonicalDigest", "sequence", "changeId",
                "operatorId", "observedAt", "components", "scenarios", "financialReadinessVerified",
                "autoActivateVaultSigners", "operatorResumeRequired", "operatorResumePerformed")
    stack.require_keys(report, "complete-Cell acceptance", required)
    expected = {"schema": "kerosene.cell-acceptance/v1", "cellId": config["cellId"],
                "clusterUid": cluster["systemNamespaceUid"], "releaseLockCanonicalDigest": summary["_canonicalDigest"],
                "sequence": summary["sequence"], "changeId": args.change_id, "operatorId": args.operator_id,
                "financialReadinessVerified": True, "autoActivateVaultSigners": False,
                "operatorResumeRequired": True, "operatorResumePerformed": False}
    if any(report.get(key) != value for key, value in expected.items()):
        raise stack.ApplyBlockedError("complete-Cell acceptance binding or safety result differs")
    observed = stack.parse_rfc3339(report["observedAt"], "acceptance observedAt")
    now = dt.datetime.now(dt.timezone.utc)
    if observed > now + dt.timedelta(seconds=30) or now - observed > dt.timedelta(seconds=30):
        raise stack.ApplyBlockedError("complete-Cell acceptance is stale or future-dated")
    expected_components = set(summary["services"])
    components = stack.require_object(report["components"], "acceptance components")
    scenarios = stack.require_object(report["scenarios"], "acceptance scenarios")
    expected_scenarios = {"releaseObserved", "planReviewed", "maintenanceDrained", "rolloutCompleted",
                          "interruptionRecovered", "restoreQualified", "nodeQuorumReady", "vaultQuorumReady",
                          "operatorResumeGuarded"}
    if set(components) != expected_components or set(scenarios) != expected_scenarios:
        raise stack.ApplyBlockedError("complete-Cell acceptance coverage is incomplete or unexpected")
    for label, checks in (("component", components), ("scenario", scenarios)):
        for name, check in checks.items():
            stack.require_keys(check, f"acceptance {label} {name}", ("passed", "evidenceDigest"))
            if check["passed"] is not True:
                raise stack.ApplyBlockedError(f"complete-Cell acceptance {label} failed: {name}")
            stack.require_digest(check["evidenceDigest"], f"acceptance {label} {name} evidence")
    return {"schema": report["schema"], "observedAt": report["observedAt"],
            "componentsVerified": sorted(components), "scenariosVerified": sorted(scenarios),
            "financialReadinessVerified": True, "operatorResumeRequired": True,
            "operatorResumePerformed": False}


def resolve_bootstrap_directory(stack, args):
    """Expand one protected conventional bootstrap directory into init inputs."""
    if not args.bootstrap_dir:
        required = ("cell_id", "tuf_trusted_root", "validator_roster", "vault_roster", "snapshot_provider_key")
        missing = ["--" + name.replace("_", "-") for name in required if not getattr(args, name)]
        if missing:
            raise stack.ReleaseValidationError("init is missing explicit inputs: " + ", ".join(missing))
        return args
    explicit = ("cell_id", "tuf_trusted_root", "validator_roster", "vault_roster", "snapshot_provider_key",
                "consensus_anchor", "consensus_verifier", "acceptance_verifier", "kubeconfig", "kube_context")
    if any(getattr(args, name) for name in explicit):
        raise stack.ReleaseValidationError("--bootstrap-dir cannot be mixed with individual bootstrap inputs")
    root = Path(args.bootstrap_dir).absolute()
    try:
        info = root.lstat()
    except OSError as error:
        raise stack.ReleaseValidationError("bootstrap directory is unavailable") from error
    import stat
    if root.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise stack.ReleaseValidationError("bootstrap directory must be owner-only, local and not a symlink")
    conventional = {"tuf_trusted_root": "tuf-root.json", "validator_roster": "validator-roster.json",
                    "vault_roster": "vault-roster.json", "snapshot_provider_key": "snapshot-provider.pub",
                    "consensus_anchor": "consensus-anchor.json", "consensus_verifier": "kerosene-release-consensus",
                    "acceptance_verifier": "kerosene-cell-acceptance", "kubeconfig": "kubeconfig"}
    for name in (*conventional.values(), "cell-id", "kube-context"):
        path = root / name
        try:
            file_info = path.lstat()
        except OSError as error:
            raise stack.ReleaseValidationError("bootstrap directory is incomplete: " + name) from error
        if (path.is_symlink() or not stat.S_ISREG(file_info.st_mode) or file_info.st_uid != os.getuid() or
                file_info.st_mode & 0o077):
            raise stack.ReleaseValidationError("bootstrap file must be owner-only, regular and not a symlink: " + name)
    for field, name in conventional.items():
        setattr(args, field, str(root / name))
    def text(name, label):
        try:
            value = stack.read_regular_file_bytes(root / name, label, 1024).decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise stack.ReleaseValidationError(label + " must be ASCII") from error
        if not value or any(char.isspace() for char in value):
            raise stack.ReleaseValidationError(label + " must contain one nonempty token")
        return value
    args.cell_id = text("cell-id", "bootstrap Cell ID")
    args.kube_context = text("kube-context", "bootstrap Kubernetes context")
    return args


def resolve_operation_directory(stack, args):
    """Expand private operator references without copying them into Cell state."""
    if not getattr(args, "operation_dir", None):
        return args
    fields = {"confirm_release": "confirm-release", "change_id": "change-id", "operator_id": "operator-id",
              "admission_endpoint": "admission-endpoint", "admission_ca": "admission-ca.pem",
              "admission_cert": "admission-cert.pem", "admission_key": "admission-key.pem",
              "maintenance_endpoint": "maintenance-endpoint", "maintenance_ca": "maintenance-ca.pem",
              "maintenance_cert": "maintenance-cert.pem", "maintenance_key": "maintenance-key.pem",
              "maintenance_token_file": "maintenance-token", "recovery_evidence": "recovery-evidence.json"}
    if any(getattr(args, field, None) for field in fields):
        raise stack.ReleaseValidationError("--operation-dir cannot be mixed with individual operational inputs")
    root = Path(args.operation_dir).absolute()
    try:
        info = root.lstat()
    except OSError as error:
        raise stack.ReleaseValidationError("operation directory is unavailable") from error
    import stat
    if root.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise stack.ReleaseValidationError("operation directory must be owner-only, local and not a symlink")
    for field, name in fields.items():
        path = root / name
        try:
            file_info = path.lstat()
        except OSError as error:
            raise stack.ReleaseValidationError("operation directory is incomplete: " + name) from error
        if (path.is_symlink() or not stat.S_ISREG(file_info.st_mode) or file_info.st_uid != os.getuid() or
                file_info.st_mode & 0o077):
            raise stack.ReleaseValidationError("operation file must be owner-only, regular and not a symlink: " + name)
        if field in {"confirm_release", "change_id", "operator_id", "admission_endpoint", "maintenance_endpoint"}:
            try:
                value = stack.read_regular_file_bytes(path, "operational " + field, 4096).decode("ascii").strip()
            except UnicodeDecodeError as error:
                raise stack.ReleaseValidationError("operational text references must be ASCII") from error
            if not value or any(char in value for char in "\r\n\x00"):
                raise stack.ReleaseValidationError("operational text reference is empty or multiline")
            setattr(args, field, value)
        else:
            # The consumer opens and validates the exact protected file later;
            # no key, token or credential value enters config or journal state.
            setattr(args, field, str(path))
    return args


def command_init(stack, args):
    args = resolve_bootstrap_directory(stack, args)
    target = Path(args.cell_dir).absolute()
    if target.exists():
        raise stack.ReleaseValidationError("init requires a new Cell directory; refuses overwriting identity or trust")
    stack.require_identifier(args.cell_id, "Cell ID")
    if bool(args.kubeconfig) != bool(args.kube_context):
        raise stack.ReleaseValidationError("explicit kubeconfig and context must be provisioned together")
    cluster = None
    if args.kubeconfig:
        command = [shutil.which("kubectl") or "kubectl", "--kubeconfig", str(Path(args.kubeconfig).absolute()), "--context", args.kube_context, "--request-timeout=30s"]
        live = json.loads(run(command + ["get", "namespace", "kube-system", "-o", "json"]))
        cluster = {"kubeconfig": str(Path(args.kubeconfig).absolute()), "context": args.kube_context, "systemNamespaceUid": live["metadata"]["uid"]}
    sources = {"tuf-root.json": args.tuf_trusted_root, "validator-roster.json": args.validator_roster,
               "vault-roster.json": args.vault_roster, "snapshot-provider.pub": args.snapshot_provider_key}
    if bool(args.consensus_anchor) != bool(args.consensus_verifier):
        raise stack.ReleaseValidationError("consensus anchor and installed verifier must be provisioned together")
    if args.consensus_anchor:
        sources["consensus-anchor.json"] = args.consensus_anchor
    inputs = {name: stack.read_regular_file_bytes(Path(path), name, stack.MAX_TUF_METADATA_BYTES) for name, path in sources.items()}
    stack.parse_tuf_root(json.loads(inputs["tuf-root.json"]), "bootstrap TUF root")
    target.mkdir(parents=True, mode=0o700)
    os.chmod(target, 0o700)
    for name, raw in inputs.items():
        descriptor = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    config = {"schema": "kerosene.stack.cell/v1", "cellId": args.cell_id, "environment": "staging-cell", "trustDigests": {name: "sha256:" + hashlib.sha256(raw).hexdigest() for name, raw in inputs.items()}, "autoActivateVaultSigners": False}
    if cluster:
        config["cluster"] = cluster
    if args.consensus_verifier:
        raw = stack.read_regular_file_bytes(Path(args.consensus_verifier), "installed consensus verifier", 64 * 1024 * 1024)
        config["consensusVerifier"] = {"path": str(Path(args.consensus_verifier).absolute()), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
    if args.acceptance_verifier:
        raw = stack.read_regular_file_bytes(Path(args.acceptance_verifier), "installed complete-Cell acceptance verifier", 64 * 1024 * 1024)
        config["acceptanceVerifier"] = {"path": str(Path(args.acceptance_verifier).absolute()), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
    stack.atomic_write_json(str(target / "cell.json"), config, mode=0o600)
    stack.prepare_state_dir(str(target / "state"))
    print(json.dumps({"status": "initialized", "cellId": args.cell_id, "cellDir": str(target), "servicesStarted": False, "nextAction": "preflight"}))
    return 0


def command_status(stack, args):
    config = load_config(stack, args.cell_dir)
    state = stack.read_existing_update_state(str(Path(args.cell_dir) / "state"))
    lock = Path(args.cell_dir) / "state" / stack.UPDATE_LOCK_FILENAME
    result = {"schema": "kerosene.stack.status/v1", "cellId": config["cellId"], "environment": config["environment"], "source": "local-journal-not-live-attestation", "update": state, "updateLockPresent": lock.exists(), "blockers": []}
    if state is None:
        result["blockers"].append("cell-not-installed")
    elif state.get("manualRecoveryRequired"):
        result["blockers"].append("manual-recovery-required")
    if lock.exists():
        result["blockers"].append("update-active-or-abandoned-lock-investigation-required")
    print(json.dumps(result, indent=2))
    return 0


def command_preflight(stack, args):
    config = load_config(stack, args.cell_dir)
    verify_bootstrap_trust(stack, args.cell_dir, config)
    blockers = []
    release_path = getattr(args, "release", None)
    manifest_path = getattr(args, "deployment_manifest", None)
    if bool(release_path) != bool(manifest_path):
        raise stack.ReleaseValidationError("preflight requires --release and --deployment-manifest together")
    references = None
    if release_path:
        release, summary = stack.load_and_validate(release_path)
        artifact = verify_deployment(stack, release, summary, manifest_path)
        references = required_secret_references(stack, artifact)
    secrets_verified = False
    runtime = {name: shutil.which(name) for name in ("openssl", "kubectl", "docker")}
    for name in ("openssl", "kubectl"):
        if runtime[name] is None:
            blockers.append("runtime-missing:" + name)
    for name, expected in config["trustDigests"].items():
        raw = stack.read_regular_file_bytes(Path(args.cell_dir) / name, "bootstrap trust", stack.MAX_TUF_METADATA_BYTES)
        if "sha256:" + hashlib.sha256(raw).hexdigest() != expected:
            blockers.append("trust-anchor-changed:" + name)
    if runtime["kubectl"]:
        try:
            kubectl = kubectl_command(stack, config)
            run(kubectl + ["get", "--raw=/readyz"])
            if references is not None:
                verify_external_secrets(stack, kubectl, references)
                secrets_verified = True
        except (stack.ReleaseValidationError, RuntimeError, subprocess.TimeoutExpired):
            blockers.append("cluster-unreachable-not-ready-or-required-secrets-unavailable")
    print(json.dumps({"schema": "kerosene.stack.preflight/v1", "cellId": config["cellId"], "runtime": runtime, "blockers": blockers, "passed": not blockers, "externalSecretReferencesVerified": secrets_verified, "externalSecretReferenceCount": len(references) if references is not None else None, "financialReadinessVerified": False, "applyQualified": not EXECUTION_BLOCKERS, "executionBlockers": list(EXECUTION_BLOCKERS)}, indent=2))
    return 0 if not blockers else stack.EXIT_CANNOT_APPLY


def add_commands(stack, subcommands):
    admin = subcommands.add_parser("admin", help="run the installed Admin CLI only from a committed Cell update")
    admin.add_argument("--cell-dir", required=True)
    admin.add_argument("--state-dir", help="protected update journal directory; defaults to Cell state")
    admin.add_argument("--target", choices=("core", "kfe"), default="core", help="approved API origin; defaults to Core")
    admin.add_argument("admin_args", nargs="...")
    admin.set_defaults(handler=lambda args: command_admin(stack, args))
    init = subcommands.add_parser("init", help="initialize protected Cell identity and explicit out-of-band public trust anchors")
    init.add_argument("--cell-dir", required=True)
    init.add_argument("--bootstrap-dir", help="owner-only conventional bootstrap directory; cannot be mixed with individual inputs")
    init.add_argument("--cell-id")
    init.add_argument("--tuf-trusted-root")
    init.add_argument("--validator-roster")
    init.add_argument("--vault-roster")
    init.add_argument("--snapshot-provider-key")
    init.add_argument("--consensus-anchor", help="independently verified immutable Comet light block and governance policy")
    init.add_argument("--consensus-verifier", help="independently installed verifier executable, pinned by digest at bootstrap")
    init.add_argument("--acceptance-verifier", help="independently installed whole-Cell protocol verifier, pinned by digest at bootstrap")
    init.add_argument("--kubeconfig", help="explicit Kubernetes configuration reference; secrets remain outside the Cell journal")
    init.add_argument("--kube-context", help="explicit context pinned to kube-system UID at initialization")
    init.set_defaults(handler=lambda args: command_init(stack, args))
    for name in ("status", "diagnose", "preflight"):
        parser = subcommands.add_parser(name, help="inspect Cell without changing services or trust")
        parser.add_argument("--cell-dir", required=True)
        if name != "status":
            parser.add_argument("--release", help="optional release lock paired with --deployment-manifest for credential-reference preflight")
            parser.add_argument("--deployment-manifest", help="approved manifest paired with --release; only external Secret name/key inventory is queried")
        handler = command_status if name == "status" else command_preflight
        parser.set_defaults(handler=lambda args, fn=handler: fn(stack, args))
    artifact = subcommands.add_parser("import-artifact", help="integrity-check inert release data into an offline cache; does not authorize a release")
    artifact.add_argument("--cache", required=True)
    artifact.add_argument("--digest", required=True)
    artifact.add_argument("--size", type=int, required=True)
    artifact.add_argument("--mirror", action="append", default=[])
    artifact.add_argument("--offline", action="store_true")
    artifact.add_argument("--offline-file", action="store_true")
    artifact.add_argument("--destination", help="new destination for safe bounded extraction, never overwrites a Cell")
    def import_artifact(args):
        import archive
        try:
            if args.destination:
                output = archive.install(args.cache, args.destination, args.digest, args.size, args.mirror, offline=args.offline, allow_file=args.offline_file)
            else:
                output = archive.fetch(args.cache, args.digest, args.size, args.mirror, offline=args.offline, allow_file=args.offline_file)
        except (archive.ArchiveError, OSError) as error:
            raise stack.ReleaseValidationError(str(error)) from error
        print(json.dumps({"status": "integrity-verified-not-authorized", "path": str(output), "digest": args.digest}))
        return 0
    artifact.set_defaults(handler=import_artifact)


def command_admin(stack, args):
    try:
        config = load_config(stack, args.cell_dir)
        verify_bootstrap_trust(stack, args.cell_dir, config)
        cell = admin_install.private_directory(args.cell_dir)
        state_dir = require_cell_state_directory(stack, cell, args.state_dir)
        if (state_dir / stack.UPDATE_LOCK_FILENAME).exists() or (state_dir / stack.UPDATE_LOCK_FILENAME).is_symlink():
            raise RuntimeError("Admin launch blocked during an update or abandoned update lock")
        # Hold the same exclusive update lock for the duration of this command:
        # no upgrade can replace the journal between validation and execution.
        summary = {"releaseId": "admin-command", "sequence": 1}
        descriptor, lock_file = stack.acquire_update_lock(str(state_dir), summary)
        try:
            state = stack.read_existing_update_state(str(state_dir))
            if not isinstance(state, dict) or state.get("schema") != "kerosene.stack.update-state/v1" or state.get("status") != "committed" or state.get("manualRecoveryRequired") or state.get("environment") != config["environment"]:
                raise RuntimeError("Admin requires a successfully committed Cell update")
            events = state.get("events")
            if not isinstance(events, list) or len(events) > 4096 or any(not isinstance(event, dict) for event in events):
                raise RuntimeError("Admin requires a valid committed event journal")
            receipts = [event.get("evidence") for event in events if event.get("phase") == "admin-installed"]
            if len(receipts) != 1:
                raise RuntimeError("Committed update lacks one unambiguous Admin installation receipt")
            receipt = receipts[0]
            if not isinstance(receipt, dict) or receipt.get("cellId") != config["cellId"] or receipt.get("updateId") != state.get("updateId"):
                raise RuntimeError("Admin installation belongs to another Cell or release")
            update_id = state["updateId"]
            import re
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", update_id):
                raise RuntimeError("Admin installation has invalid release identity")
            root = admin_install.private_directory(cell / "admin")
            root = admin_install.private_directory(root / "installations")
            launcher = admin_install.verify_installation(stack, root / update_id[7:], receipt)
            admin_install.require_java(run)
            arguments = args.admin_args
            if arguments[:1] == ["--"]:
                arguments = arguments[1:]
            prefix = admin_install.command_prefix(receipt["config"], args.target, arguments)
            # Deliberate operator command execution; no shell interpolation,
            # credentials copied to disk, auto-login or signer activation.
            environment = os.environ.copy()
            # Deployed operator execution always uses the strongest existing
            # jctl authentication policy; an inherited local mode is no bypass.
            environment["KEROSENE_ENVIRONMENT"] = "production"
            return subprocess.run([str(launcher), *prefix, *arguments], env=environment, check=False).returncode
        finally:
            stack.release_update_lock(descriptor, lock_file)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError) as error:
        raise stack.ApplyBlockedError(str(error)) from error


def require_cell_state_directory(stack, cell_dir, state_dir):
    try:
        cell = admin_install.private_directory(cell_dir)
        canonical = admin_install.private_directory(cell / "state")
        selected = admin_install.private_directory(state_dir or canonical)
        if not os.path.samefile(canonical, selected):
            raise RuntimeError("A Cell must use its canonical state directory; alternate journals are forbidden")
        return canonical
    except (RuntimeError, OSError) as error:
        raise stack.ApplyBlockedError(str(error)) from error
