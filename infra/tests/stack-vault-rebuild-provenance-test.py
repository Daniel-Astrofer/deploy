#!/usr/bin/env python3
"""Opt-in independent Vault source-to-OCI rebuild qualification.

The test creates two isolated build contexts from the same committed Git tree,
builds both without Docker layer reuse, compares the executable and normalized
SPDX package inventory, and verifies immutable OCI source/recipe bindings.
It uses no release credentials and does not authorize deployment.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time


SCHEMA = "kerosene.vault-rebuild-provenance-qualification/v1"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def run(command, *, cwd=None, data=None, timeout=3600, check=True):
    result = subprocess.run(command, cwd=cwd, input=data, capture_output=True, timeout=timeout)
    if check and result.returncode != 0:
        detail = result.stderr.decode(errors="replace")[-4000:]
        raise RuntimeError("qualification command failed: " + " ".join(command) + "\n" + detail)
    return result


def sha256(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def git(source, *arguments):
    return run(["git", "-C", str(source), *arguments], timeout=60).stdout.decode().strip()


def extract_committed_source(source, commit, destination):
    archive = run(["git", "-C", str(source), "archive", "--format=tar", commit], timeout=120).stdout
    destination.mkdir()
    with tarfile.open(fileobj=__import__("io").BytesIO(archive), mode="r:") as bundle:
        for member in bundle.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise RuntimeError("Git archive contains a forbidden entry")
        bundle.extractall(destination, filter="data")
    return sha256(archive)


def normalized_spdx(document):
    if document.get("spdxVersion") != "SPDX-2.3" or not isinstance(document.get("packages"), list):
        raise RuntimeError("docker sbom did not emit SPDX 2.3 JSON")
    packages = []
    for package in document["packages"]:
        if not isinstance(package, dict) or not package.get("name"):
            raise RuntimeError("SPDX package record is malformed")
        refs = tuple(sorted(
            (entry.get("referenceType", ""), entry.get("referenceLocator", ""))
            for entry in package.get("externalRefs", []) if isinstance(entry, dict)
        ))
        packages.append((package["name"], package.get("versionInfo", ""), refs))
    return sorted(packages)


def inspect_image(tag):
    raw = run(["docker", "image", "inspect", tag], timeout=60).stdout
    records = json.loads(raw)
    if len(records) != 1 or not DIGEST.fullmatch(records[0].get("Id", "")):
        raise RuntimeError("Docker did not return one content-addressed image ID")
    return records[0]


def build_once(source, dockerfile, commit, tree, archive_digest, recipe_digest, epoch, tag):
    command = [
        "docker", "build", "--quiet", "--no-cache", "--pull=false",
        "--build-arg", "SOURCE_DATE_EPOCH=" + epoch,
        "--build-arg", "SOURCE_COMMIT=" + commit,
        "--build-arg", "SOURCE_TREE_DIGEST=" + archive_digest,
        "--build-arg", "BUILD_RECIPE_DIGEST=" + recipe_digest,
        "--label", "io.kerosene.git-tree=" + tree,
        "-t", tag, "-f", str(dockerfile), str(source),
    ]
    run(command, timeout=3600)
    image = inspect_image(tag)
    labels = image.get("Config", {}).get("Labels") or {}
    expected = {
        "org.opencontainers.image.revision": commit,
        "io.kerosene.source-tree-digest": archive_digest,
        "io.kerosene.build-recipe-digest": recipe_digest,
        "io.kerosene.git-tree": tree,
    }
    if any(labels.get(name) != value for name, value in expected.items()):
        raise RuntimeError("OCI image does not bind its exact source and build recipe")
    binary = run([
        "docker", "run", "--rm", "--entrypoint", "sha256sum", tag,
        "/usr/local/bin/kerosene-vault",
    ], timeout=60).stdout.decode().split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", binary):
        raise RuntimeError("Vault executable digest is malformed")
    sbom_raw = run(["docker", "sbom", "--format", "spdx-json", tag], timeout=600).stdout
    sbom = json.loads(sbom_raw)
    packages = normalized_spdx(sbom)
    if not packages:
        raise RuntimeError("Vault image SBOM is empty")
    return {
        "imageId": image["Id"],
        "binaryDigest": "sha256:" + binary,
        "sbomDigest": sha256(json.dumps(packages, sort_keys=True, separators=(",", ":")).encode()),
        "packageCount": len(packages),
    }


def main():
    if os.environ.get("KEROSENE_RUN_VAULT_REBUILD_QUALIFICATION") != "1":
        print("SKIP: set KEROSENE_RUN_VAULT_REBUILD_QUALIFICATION=1")
        return 0
    source = Path(os.environ.get("KEROSENE_VAULT_SOURCE", "")).resolve()
    deploy = Path(__file__).resolve().parents[2]
    dockerfile = deploy / "infra/docker/images/kerosene-vault/Dockerfile"
    if not (source / ".git").exists() or not dockerfile.is_file():
        raise RuntimeError("explicit Vault checkout and canonical Dockerfile are required")
    if git(source, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Vault checkout has tracked changes; qualify a committed source tree")
    commit = git(source, "rev-parse", "HEAD")
    if not COMMIT.fullmatch(commit):
        raise RuntimeError("Vault commit is not canonical SHA-1")
    expected_commit = os.environ.get("KEROSENE_VAULT_EXPECTED_COMMIT", commit)
    if expected_commit != commit:
        raise RuntimeError("Vault checkout does not match KEROSENE_VAULT_EXPECTED_COMMIT")
    tree = git(source, "rev-parse", "HEAD^{tree}")
    epoch = git(source, "show", "-s", "--format=%ct", commit)
    recipe_digest = sha256(dockerfile.read_bytes())
    qualifier_digest = sha256(Path(__file__).read_bytes())
    nonce = f"{os.getpid()}-{time.time_ns()}"
    tags = [f"kerosene/vault-rebuild-qualification:{nonce}-{index}" for index in (1, 2)]
    rebuilds = []
    try:
        with tempfile.TemporaryDirectory(prefix="kerosene-vault-rebuild-") as temporary:
            root = Path(temporary)
            archive_digests = []
            for index, tag in enumerate(tags, 1):
                context = root / f"source-{index}"
                archive_digest = extract_committed_source(source, commit, context)
                archive_digests.append(archive_digest)
                rebuilds.append(build_once(
                    context, dockerfile, commit, tree, archive_digest,
                    recipe_digest, epoch, tag,
                ))
            if len(set(archive_digests)) != 1:
                raise RuntimeError("independent Git archives differ")
        binaries = {item["binaryDigest"] for item in rebuilds}
        sboms = {item["sbomDigest"] for item in rebuilds}
        if len(binaries) != 1 or len(sboms) != 1:
            raise RuntimeError("independent Vault rebuilds are not equivalent")
        evidence = {
            "schema": SCHEMA,
            "source": {"commit": commit, "tree": tree, "archiveDigest": archive_digests[0]},
            "recipeDigest": recipe_digest,
            "qualifierDigest": qualifier_digest,
            "sourceDateEpoch": int(epoch),
            "baseImages": {
                "builder": "rust@sha256:3914072ca0c3b8aad871db9169a651ccfce30cf58303e5d6f2db16d1d8a7e58f",
                "runtime": "debian@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587",
            },
            "rebuilds": rebuilds,
            "reproducibleBinary": True,
            "equivalentSbom": True,
            "releaseAuthorization": False,
        }
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    finally:
        for tag in tags:
            run(["docker", "image", "rm", "--force", tag], timeout=120, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
