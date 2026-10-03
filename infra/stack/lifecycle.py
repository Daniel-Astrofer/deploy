"""Cell-local lifecycle and approved-manifest execution; never executes archive code.

This module deliberately has no YAML parser: the approved artifact is JSON,
so canonical configuration digests are reproducible across packaging and apply.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import urllib.parse
import urllib.request

SCHEMA = "kerosene.stack.deployment/v1"
NAMESPACES = {"kerosene-staging", "kerosene-staging-vault"}
WORKLOADS = {"Deployment", "StatefulSet"}
KINDS = WORKLOADS | {"Namespace", "ConfigMap", "Service", "ServiceAccount", "NetworkPolicy", "PersistentVolumeClaim", "PodDisruptionBudget", "HorizontalPodAutoscaler"}

# These are implementation capabilities, never caller-supplied declarations.
# Remove a blocker only with the corresponding implementation and integration
# test; signed evidence cannot implement an absent runtime safety mechanism.
EXECUTION_BLOCKERS = (
    "vault-independent-compatibility-gate-not-integrated",
    "migration-executor-and-tested-recovery-not-integrated",
    "node-vault-replica-quorum-rollout-not-qualified",
    "admin-artifact-installation-not-integrated",
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
    for name, service in summary["services"].items():
        if name != "admin" and service["image"] not in observed:
            raise stack.ApplyBlockedError(f"missing deployed component: {name}")
        if digest(component_config(artifact, service["image"], name)) != service["configDigest"]:
            raise stack.ApplyBlockedError(f"approved configuration digest mismatch: {name}")
    workload_phases(stack, artifact, summary)
    return artifact


def run(argv, *, input_bytes=None):
    result = subprocess.run(argv, input=input_bytes, capture_output=True, check=False, timeout=120)
    if result.returncode:
        # Never echo kubectl's output: it can include secret material.
        raise RuntimeError(f"operation failed ({result.returncode}): {argv[0]} {argv[1]}")
    return result.stdout


def apply_resource(kubectl, resource, dry_run=False):
    argv = kubectl + ["apply", "--server-side", "--field-manager=kerosene-stack", "-f", "-"]
    if dry_run:
        argv.append("--dry-run=server")
    run(argv, input_bytes=canonical(resource))


def workload_phases(stack, artifact, summary):
    """Plan infrastructure before consumers, without serializing cyclic apps.

    This is startup ordering, not a quorum-preserving update algorithm. The
    separate execution capability gate remains mandatory for real apply.
    Admin/jctl is an operator artifact, never a long-running workload.
    """
    phases = ({"postgres", "redis", "tor"}, {"bitcoin"}, {"lnd"},
              {"node", "vault"}, {"core", "kfe"}, {"web-page"})
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


def verify_running(kubectl, resource):
    """Require observed generation, desired ready replicas and named live images.

    imageID may be an OCI platform manifest digest rather than the image's
    index digest; Kubernetes spec equality + a nonempty runtime imageID is
    recorded, not falsely presented as a registry provenance verification.
    """
    ns, kind, name = identity(resource)
    live = json.loads(run(kubectl + ["-n", ns, "get", kind.lower(), name, "-o", "json"]))
    expected = resource["spec"]["replicas"]
    status = live.get("status", {})
    if status.get("observedGeneration", 0) < live["metadata"].get("generation", 1):
        raise RuntimeError(f"controller has not observed {kind}/{name}")
    if status.get("readyReplicas", 0) != expected or status.get("updatedReplicas", 0) != expected:
        raise RuntimeError(f"not all desired replicas are updated and ready for {kind}/{name}")
    selector = live["spec"]["selector"].get("matchLabels", {})
    if not selector or live["spec"]["selector"].get("matchExpressions"):
        raise RuntimeError("unsupported or empty workload selector")
    labels = ",".join(f"{key}={value}" for key, value in sorted(selector.items()))
    pods = json.loads(run(kubectl + ["-n", ns, "get", "pods", "-l", labels, "-o", "json"]))["items"]
    active = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
    if len(active) != expected:
        raise RuntimeError("live pod count does not equal approved replicas")
    images = {c["name"]: c["image"] for c in containers(resource)}
    records = []
    for pod in active:
        readiness = pod.get("status", {}).get("conditions", [])
        if not any(c.get("type") == "Ready" and c.get("status") == "True" for c in readiness):
            raise RuntimeError("live pod is not ready")
        pod_spec = pod["spec"]
        actual = {c["name"]: c["image"] for c in pod_spec.get("containers", []) + pod_spec.get("initContainers", [])}
        if actual != images:
            raise RuntimeError("live pod images differ from approved named containers")
        pod_status = pod.get("status", {})
        statuses = pod_status.get("containerStatuses", []) + pod_status.get("initContainerStatuses", [])
        if {s["name"] for s in statuses} != set(images) or any(not s.get("imageID") for s in statuses):
            raise RuntimeError("missing live runtime image identity")
        records.append({"podUid": pod["metadata"]["uid"], "images": [{"name": s["name"], "imageID": s["imageID"]} for s in statuses]})
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
    if any(os.environ.get(k) for k in ("KUBECTL", "KEROSENE_SKIP_STAGING_SMOKES", "KEROSENE_FORCE_CONFLICTS")):
        raise stack.ApplyBlockedError("approved execution forbids tool/gate override environment variables")
    prerequisites = [r for r in artifact["resources"] if r["kind"] not in WORKLOADS]
    phases = workload_phases(stack, artifact, summary)
    # Apply non-runtime inputs first. Explicit ordering prevents HPA creation
    # from silently changing approved replicas before validation.
    prerequisites.sort(key=lambda r: (0 if r["kind"] == "Namespace" else 2 if r["kind"] == "HorizontalPodAutoscaler" else 1, identity(r)))
    # Validate all unsupported policies before the first Kubernetes write.
    if any(r["kind"] == "HorizontalPodAutoscaler" for r in prerequisites):
        raise stack.ApplyBlockedError("HPA requires a separately approved freeze/restore policy during Cell update")
    for resource in prerequisites:
        apply_resource(kubectl, resource, args.dry_run)
    for phase in phases:
        # Core and KFE have reciprocal integration references. Submit the
        # entire phase before waiting, otherwise the first readiness gate can
        # deadlock bootstrap by waiting for a service not yet created.
        for resource in phase:
            label = "/".join(identity(resource))
            if not args.dry_run:
                verify_maintenance(stack, args)
            checkpoint("before:" + label, {})
            apply_resource(kubectl, resource, args.dry_run)
        if args.dry_run:
            continue
        for resource in phase:
            label = "/".join(identity(resource))
            ns, kind, name = identity(resource)
            run(kubectl + ["-n", ns, "rollout", "status", f"{kind.lower()}/{name}", "--timeout=110s"])
            checkpoint("ready:" + label, {"runtime": verify_running(kubectl, resource)})
    if not args.dry_run:
        # Existing operational gates execute from installed controller, not
        # executable content supplied in source/archive artifacts.
        root = Path(stack.__file__).resolve().parents[1]
        for script in ("smoke-staging-vault.sh", "smoke-staging.sh"):
            run(["bash", str(root / "infra/kubernetes/scripts" / script)])


def load_config(stack, directory):
    root = Path(directory)
    if root.is_symlink():
        raise stack.ReleaseValidationError("Cell directory cannot be a symlink")
    return stack.read_json_document(str(root / "cell.json"), "Cell configuration")


def verify_bootstrap_trust(stack, directory, config):
    stack.require_keys(config, "Cell configuration", ("schema", "cellId", "environment", "trustDigests", "autoActivateVaultSigners"), ("consensusVerifier", "cluster"))
    if config["schema"] != "kerosene.stack.cell/v1" or config["environment"] != "staging-cell" or config["autoActivateVaultSigners"] is not False:
        raise stack.ReleaseValidationError("unsupported Cell configuration or forbidden signer activation")
    expected_names = {"tuf-root.json", "validator-roster.json", "snapshot-provider.pub"}
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


def command_init(stack, args):
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
    sources = {"tuf-root.json": args.tuf_trusted_root, "validator-roster.json": args.validator_roster, "snapshot-provider.pub": args.snapshot_provider_key}
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
            run(kubectl_command(stack, config) + ["get", "--raw=/readyz"])
        except (stack.ReleaseValidationError, RuntimeError, subprocess.TimeoutExpired):
            blockers.append("cluster-unreachable-or-not-ready")
    print(json.dumps({"schema": "kerosene.stack.preflight/v1", "cellId": config["cellId"], "runtime": runtime, "blockers": blockers, "passed": not blockers, "financialReadinessVerified": False, "applyQualified": not EXECUTION_BLOCKERS, "executionBlockers": list(EXECUTION_BLOCKERS)}, indent=2))
    return 0 if not blockers else stack.EXIT_CANNOT_APPLY


def add_commands(stack, subcommands):
    init = subcommands.add_parser("init", help="initialize protected Cell identity and explicit out-of-band public trust anchors")
    init.add_argument("--cell-dir", required=True)
    init.add_argument("--cell-id", required=True)
    init.add_argument("--tuf-trusted-root", required=True)
    init.add_argument("--validator-roster", required=True)
    init.add_argument("--snapshot-provider-key", required=True)
    init.add_argument("--consensus-anchor", help="independently verified immutable Comet light block and governance policy")
    init.add_argument("--consensus-verifier", help="independently installed verifier executable, pinned by digest at bootstrap")
    init.add_argument("--kubeconfig", help="explicit Kubernetes configuration reference; secrets remain outside the Cell journal")
    init.add_argument("--kube-context", help="explicit context pinned to kube-system UID at initialization")
    init.set_defaults(handler=lambda args: command_init(stack, args))
    for name in ("status", "diagnose", "preflight"):
        parser = subcommands.add_parser(name, help="inspect Cell without changing services or trust")
        parser.add_argument("--cell-dir", required=True)
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
