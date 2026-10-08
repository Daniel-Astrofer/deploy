"""Prepare all inert resources for one complete Cell in a new directory."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from archive import ArchiveError, open_regular, strict_json
import node_resources
import render_deployment
import vault_resources


SCHEMA = "kerosene.cell-preparation/v1"
MAX_CONFIG = 1024 * 1024


def _input(base, value, label):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ArchiveError(label + " path must be a nonempty string")
    path = Path(value)
    path = path if path.is_absolute() else base / path
    path = Path(os.path.abspath(path))
    with open_regular(path):
        pass
    return path


def _configuration(stack, path):
    path = Path(os.path.abspath(path))
    with open_regular(path) as stream:
        raw = stream.read(MAX_CONFIG + 1)
    if len(raw) > MAX_CONFIG:
        raise ArchiveError("Cell preparation config byte limit exceeded")
    document = strict_json(raw)
    stack.require_keys(document, "Cell preparation", (
        "schema", "images", "adminConfig", "genesis", "planes", "vaultMeasurementPin"))
    if document["schema"] != SCHEMA:
        raise ArchiveError("unsupported Cell preparation schema")
    planes = stack.require_object(document["planes"], "Cell preparation planes")
    stack.require_keys(planes, "Cell preparation planes", ("bank", "vault"))
    result = {"images": _input(path.parent, document["images"], "images"),
              "admin": _input(path.parent, document["adminConfig"], "Admin configuration"),
              "genesis": _input(path.parent, document["genesis"], "genesis"),
              "measurement": document["vaultMeasurementPin"], "planes": {}}
    for plane in ("bank", "vault"):
        value = stack.require_object(planes[plane], plane + " plane")
        stack.require_keys(value, plane + " plane", ("membership", "attestation", "snapshot"))
        result["planes"][plane] = {
            name: _input(path.parent, value[name], plane + " " + name)
            for name in ("membership", "attestation", "snapshot")}
    return result


def _foundation(kubectl, overlay, output):
    try:
        result = subprocess.run([kubectl, "kustomize", str(overlay)], stdin=subprocess.DEVNULL,
                                capture_output=True, check=False, timeout=60)
    except subprocess.TimeoutExpired as error:
        raise ArchiveError("complete Cell foundation rendering timed out") from error
    if result.returncode or not result.stdout or len(result.stdout) > render_deployment.MAX_INPUT:
        raise ArchiveError("kubectl could not render the bounded complete Cell foundation")
    output.write_bytes(result.stdout)
    os.chmod(output, 0o444)


def prepare(stack, package_release, config_path, output_dir, root, kubectl=None):
    config = _configuration(stack, config_path)
    output = Path(os.path.abspath(output_dir))
    if not output.parent.is_dir():
        raise ArchiveError("Cell preparation output parent does not exist")
    executable = kubectl or shutil.which("kubectl")
    if not executable:
        raise ArchiveError("kubectl is required for local Kustomize and YAML decoding")
    os.mkdir(output, 0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".building.", dir=output))
    try:
        foundation = temporary / "foundation.yaml"
        nodes = temporary / "nodes.json"
        vaults = temporary / "vaults.json"
        deployment = temporary / "deployment.json"
        _foundation(executable, Path(root) / "kubernetes/overlays/complete-cell", foundation)
        genesis = node_resources.document(config["genesis"], "genesis")
        manifests = {plane: node_resources.document(value["membership"], plane + " membership")
                     for plane, value in config["planes"].items()}
        snapshots = {}
        for plane, value in config["planes"].items():
            attestation = node_resources.document(value["attestation"], plane + " attestation")
            with open_regular(value["snapshot"]) as stream:
                payload = stream.read(node_resources.MAX_DOCUMENT + 1)
            if len(payload) > node_resources.MAX_DOCUMENT:
                raise ArchiveError(plane + " snapshot byte limit exceeded")
            snapshots[plane] = (attestation, payload)
        node_resources.write(node_resources.generate(genesis, manifests, snapshots), nodes)
        vault_resources.write(vault_resources.generate(
            genesis.get("network_id"), config["measurement"]), vaults)
        result = render_deployment.render(
            stack, package_release, config["images"], [foundation, nodes, vaults],
            config["admin"], deployment, kubectl=executable)
        for path in temporary.iterdir():
            os.rename(path, output / path.name)
        os.rmdir(temporary)
        os.chmod(output, 0o750)
        result["output"] = str(output / "deployment.json")
        result["directory"] = str(output)
        return result
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise
