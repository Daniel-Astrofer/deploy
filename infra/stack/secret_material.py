"""Provision exact external Cell Secrets from protected operator-local files."""

import base64
import os
from pathlib import Path
import stat

from archive import ArchiveError, canonical_bytes, strict_json, write_new


SCHEMA = "kerosene.cell-secret-material/v1"
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
SECRET_TYPES = {"Opaque", "kubernetes.io/tls", "kubernetes.io/dockerconfigjson"}


def template(references, output):
    secrets = []
    for (namespace, name), keys in sorted(references.items()):
        secret_type = "Opaque"
        if keys == {"tls.crt", "tls.key"}:
            secret_type = "kubernetes.io/tls"
        elif keys == {".dockerconfigjson"}:
            secret_type = "kubernetes.io/dockerconfigjson"
        files = {key: "secrets/" + namespace + "/" + name + "/" + key
                 for key in sorted(keys)}
        secrets.append({"namespace": namespace, "name": name,
                        "type": secret_type, "files": files})
    write_new(output, canonical_bytes({"schema": SCHEMA, "secrets": secrets}) + b"\n")


def _private_bytes(path, label, limit):
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_mode & 0o077 or info.st_size <= 0 or info.st_size > limit):
            raise ArchiveError(label + " must be a bounded owner-only regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(limit + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > limit:
        raise ArchiveError(label + " byte limit exceeded")
    return raw


def load(stack, lifecycle, deployment_path, material_path):
    deployment = stack.read_json_document(
        deployment_path, "prepared Cell deployment", 8 * 1024 * 1024)
    stack.require_keys(deployment, "prepared Cell deployment",
                       ("schema", "environment", "resources", "admin"))
    if deployment["schema"] != lifecycle.SCHEMA or deployment["environment"] != "staging-cell":
        raise ArchiveError("unsupported prepared Cell deployment")
    if any(resource.get("metadata", {}).get("namespace") not in lifecycle.NAMESPACES
           for resource in deployment["resources"] if resource.get("kind") != "Namespace"):
        raise ArchiveError("prepared Cell deployment contains a foreign namespace")
    references = lifecycle.required_secret_references(stack, deployment)
    material_path = Path(os.path.abspath(material_path))
    document = strict_json(_private_bytes(material_path, "Secret material manifest", MAX_FILE))
    stack.require_keys(document, "Secret material", ("schema", "secrets"))
    if document["schema"] != SCHEMA or not isinstance(document["secrets"], list):
        raise ArchiveError("unsupported Secret material schema")
    observed, total = {}, 0
    for value in document["secrets"]:
        stack.require_keys(value, "Secret material entry", ("namespace", "name", "type", "files"))
        namespace = value["namespace"]
        name = stack.require_identifier(value["name"], "Secret material name")
        if namespace not in lifecycle.NAMESPACES or value["type"] not in SECRET_TYPES:
            raise ArchiveError("Secret material namespace or type is unsupported")
        identity = (namespace, name)
        if identity in observed or identity not in references:
            raise ArchiveError("Secret material is duplicate or not referenced by the deployment")
        files = stack.require_object(value["files"], "Secret material files")
        if not files or len(files) > 128:
            raise ArchiveError("Secret material requires a bounded nonempty file map")
        data = {}
        for key, source in files.items():
            if (not isinstance(key, str) or not key or len(key) > 253 or
                    any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in key)):
                raise ArchiveError("Secret material key is invalid")
            if not isinstance(source, str) or not source or "\x00" in source:
                raise ArchiveError("Secret material source path is invalid")
            path = Path(source)
            path = path if path.is_absolute() else material_path.parent / path
            raw = _private_bytes(path, "Secret material source", MAX_FILE)
            total += len(raw)
            if total > MAX_TOTAL:
                raise ArchiveError("Secret material aggregate byte limit exceeded")
            data[key] = base64.b64encode(raw).decode("ascii")
        if not references[identity] <= set(data):
            raise ArchiveError("Secret material lacks a deployment-required key")
        observed[identity] = {"apiVersion": "v1", "kind": "Secret", "immutable": True,
                              "metadata": {"namespace": namespace, "name": name},
                              "type": value["type"], "data": data}
    if set(observed) != set(references):
        raise ArchiveError("Secret material does not cover the complete deployment inventory")
    return deployment, observed
def provision(stack, lifecycle, cell_dir, deployment_path, material_path):
    deployment, secrets = load(stack, lifecycle, deployment_path, material_path)
    config = lifecycle.load_config(stack, cell_dir)
    lifecycle.verify_bootstrap_trust(stack, cell_dir, config)
    kubectl = lifecycle.kubectl_command(stack, config)
    namespaces = {resource["metadata"]["name"]: resource for resource in deployment["resources"]
                  if resource["kind"] == "Namespace" and resource["metadata"]["name"] in lifecycle.NAMESPACES}
    if set(namespaces) != lifecycle.NAMESPACES:
        raise ArchiveError("prepared deployment does not declare both Cell namespaces")
    existing_namespaces = set()
    for namespace in sorted(lifecycle.NAMESPACES):
        raw = lifecycle.run(kubectl + ["get", "namespace", namespace, "--ignore-not-found", "-o", "name"])
        if raw.strip():
            if raw.decode("ascii").strip() != "namespace/" + namespace:
                raise ArchiveError("unexpected namespace lookup result")
            existing_namespaces.add(namespace)
            for identity in sorted(key for key in secrets if key[0] == namespace):
                found = lifecycle.run(kubectl + ["-n", namespace, "get", "secret", identity[1],
                                                  "--ignore-not-found", "-o", "name"])
                if found.strip():
                    raise ArchiveError("refusing to overwrite existing Cell Secret: " + "/".join(identity))
    created_namespaces = []
    for namespace in sorted(lifecycle.NAMESPACES - existing_namespaces):
        lifecycle.run(kubectl + ["create", "-f", "-"],
                      input_bytes=canonical_bytes(namespaces[namespace]) + b"\n")
        created_namespaces.append(namespace)
    created = []
    for identity, secret in sorted(secrets.items()):
        lifecycle.run(kubectl + ["create", "-f", "-"],
                      input_bytes=canonical_bytes(secret) + b"\n")
        created.append({"namespace": identity[0], "name": identity[1],
                        "keys": sorted(secret["data"])})
    return {"schema": "kerosene.cell-secret-provisioning/v1",
            "clusterUid": config["cluster"]["systemNamespaceUid"],
            "createdNamespaces": created_namespaces, "createdSecrets": created}
