"""Render inert Kubernetes YAML into a validated canonical Cell deployment."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from archive import ArchiveError, bytes_digest, canonical_bytes, open_regular, strict_json, write_new
import lifecycle


IMAGE_SELECTION_SCHEMA = "kerosene.cell-images/v1"
PLACEHOLDER = re.compile(r"kerosene-cell\.invalid/([a-z0-9][a-z0-9-]*):selected$")
MAX_INPUT = 8 * 1024 * 1024


def read_json(path, label):
    with open_regular(path) as stream:
        raw = stream.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise ArchiveError(f"{label} byte limit exceeded")
    return strict_json(raw)


def selected_images(document, stack):
    stack.require_keys(document, "image selection", ("schema", "services"))
    if document["schema"] != IMAGE_SELECTION_SCHEMA:
        raise ArchiveError("unsupported image selection schema")
    services = stack.require_object(document["services"], "image selection services")
    if not services or len(services) > 64 or "admin" not in services:
        raise ArchiveError("image selection requires bounded services including admin")
    selected = {}
    for name, value in services.items():
        stack.require_identifier(name, "service name")
        stack.require_keys(value, f"service {name}", ("image",))
        selected[name] = {"image": stack.require_image(value["image"], f"service {name} image")}
    return selected


def decode_yaml(kubectl, path):
    path = Path(os.path.abspath(path))
    with open_regular(path) as stream:
        if os.fstat(stream.fileno()).st_size > MAX_INPUT:
            raise ArchiveError("resources YAML byte limit exceeded")
    try:
        result = subprocess.run(
            [kubectl, "apply", "--dry-run=client", "--validate=false", "-f", str(path), "-o", "json"],
            stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=60)
    except subprocess.TimeoutExpired as error:
        raise ArchiveError("kubectl YAML decoding timed out") from error
    if result.returncode or len(result.stdout) > MAX_INPUT:
        raise ArchiveError("kubectl could not decode bounded local resources YAML")
    try:
        document = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArchiveError("kubectl returned invalid resource JSON") from error
    items = document.get("items") if document.get("kind") == "List" else [document]
    if not isinstance(items, list) or not items or len(items) > 1024:
        raise ArchiveError("resources YAML must contain a bounded nonempty object list")
    return items


def normalize_resources(resources, selected):
    approved = {service["image"] for name, service in selected.items() if name != "admin"}
    normalized = []
    for source in resources:
        if not isinstance(source, dict):
            raise ArchiveError("resource must be an object")
        resource = json.loads(json.dumps(source))
        annotations = resource.get("metadata", {}).get("annotations")
        if isinstance(annotations, dict):
            annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
            if not annotations:
                resource["metadata"].pop("annotations", None)
        pod = resource.get("spec", {}).get("template", {}).get("spec", {})
        for container in pod.get("containers", []) + pod.get("initContainers", []):
            image = container.get("image")
            match = PLACEHOLDER.fullmatch(image) if isinstance(image, str) else None
            if match:
                service = match.group(1)
                if service == "admin" or service not in selected:
                    raise ArchiveError("workload uses an unknown/non-runtime image placeholder")
                container["image"] = selected[service]["image"]
            elif image not in approved:
                raise ArchiveError("workload image must be a selected placeholder or exact selected digest")
        normalized.append(resource)
    return normalized


def render(stack, package_release, image_path, resources_path, admin_path, output, kubectl=None):
    selected = selected_images(read_json(image_path, "image selection"), stack)
    admin = read_json(admin_path, "Admin configuration")
    if not isinstance(admin, dict):
        raise ArchiveError("Admin configuration must be an object")
    executable = kubectl or shutil.which("kubectl")
    if not executable:
        raise ArchiveError("kubectl is required only as a local YAML decoder")
    resource_paths = resources_path if isinstance(resources_path, (list, tuple)) else [resources_path]
    decoded = []
    for path in resource_paths:
        decoded.extend(decode_yaml(executable, path))
    resources = normalize_resources(decoded, selected)
    deployment = {"schema": lifecycle.SCHEMA, "environment": "staging-cell",
                  "resources": resources,
                  "admin": {"image": selected["admin"]["image"], "config": admin}}
    configured = {name: {**service, "configDigest": lifecycle.digest(
        lifecycle.component_config(deployment, service["image"], name))}
                  for name, service in selected.items()}
    summary = package_release.candidate_runtime_summary(deployment, configured, lifecycle)
    raw = canonical_bytes(deployment) + b"\n"
    with tempfile.TemporaryDirectory(prefix="kerosene-render-") as temporary:
        candidate = Path(temporary) / "deployment.json"
        candidate.write_bytes(raw)
        lifecycle.verify_deployment(stack, {"services": configured}, summary, str(candidate))
    output = Path(os.path.abspath(output))
    write_new(output, raw)
    return {"schema": "kerosene.cell-deployment-render/v1", "output": str(output),
            "digest": bytes_digest(raw),
            "configurationDigests": {name: service["configDigest"]
                                     for name, service in sorted(configured.items())}}
