#!/usr/bin/env python3
"""Assemble a deterministic, unsigned release candidate from explicit inputs."""

import argparse
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
import tarfile
import tempfile

from archive import (ArchiveError, DEFAULT_LIMITS, bytes_digest, canonical_bytes,
                     copy_verified, digest_hex, file_record, fsync_directory,
                     open_regular, private_directory, publish_directory,
                     safe_member_name, strict_json, write_new)


INPUT_SCHEMA = "kerosene.release-candidate-input.v1"
INDEX_SCHEMA = "kerosene.deployment-bundle.v1"
DEPLOYMENT_SCHEMA = "kerosene.stack.deployment/v1"
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]{64}")
JSON_LIMIT = 16 * 1024 * 1024


def keys(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ArchiveError("unexpected/missing input fields: " + ", ".join(required))
    return value


def name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value) or value in (".", ".."):
        raise ArchiveError("invalid material/service name")
    return value


def input_path(value, base, root=None):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ArchiveError("invalid input path")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    # These are explicit operator-local inputs, never paths from a download.
    if root is not None and not path.resolve().is_relative_to(root.resolve()):
        raise ArchiveError("input escapes --input-root")
    return path


def json_file(path):
    with open_regular(path) as stream:
        raw = stream.read(JSON_LIMIT + 1)
    if len(raw) > JSON_LIMIT:
        raise ArchiveError("JSON input byte limit exceeded")
    return raw, strict_json(raw)


def material(value, base, root):
    keys(value, ("path", "digest"))
    digest_hex(value["digest"])
    raw, parsed = json_file(input_path(value["path"], base, root))
    if bytes_digest(raw) != value["digest"]:
        raise ArchiveError("selected JSON input digest mismatch")
    return raw, parsed


def git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
               GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
               LC_ALL="C", TZ="UTC")
    return env


def git(args, *, env=None):
    def bound_output_file():
        resource.setrlimit(resource.RLIMIT_FSIZE,
                           (DEFAULT_LIMITS.max_file_bytes, DEFAULT_LIMITS.max_file_bytes))

    command = ["git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
               "-c", "core.fsmonitor=false", "-c", "gc.auto=0",
               "-c", "maintenance.auto=false", "-c", "pack.threads=1",
               "-c", "pack.window=0", "-c", "pack.depth=0",
               "-c", "pack.useBitmaps=false", "-c", "pack.useSparse=false",
               "-c", "pack.reuseDeltas=false", "-c", "pack.reuseObjects=false"] + list(args)
    try:
        # Temporary files bound captured output, including malicious bundle headers.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            result = subprocess.run(command, env=env or git_env(), stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, timeout=DEFAULT_LIMITS.total_seconds,
                                    preexec_fn=bound_output_file, check=False)
            stdout.seek(0)
            output = stdout.read(JSON_LIMIT + 1)
            if result.returncode or len(output) > JSON_LIMIT:
                raise ArchiveError("local Git operation failed: " + args[-1])
            return output.decode("utf-8").strip()
    except (subprocess.TimeoutExpired, UnicodeError) as exc:
        raise ArchiveError("local Git operation timed out/returned invalid output") from exc


def verify_bundle(path, commit, scratch):
    # Accept exactly one full-history candidate head; never clone/checkout it.
    with open_regular(path) as stream:
        header = stream.read(4097)
    marker = header.find(b"\n\n")
    if marker < 0 or marker > 4096:
        raise ArchiveError("Git bundle header limit exceeded")
    lines = header[:marker].decode("ascii").splitlines()
    expected_version = "# v3 git bundle" if len(commit) == 64 else "# v2 git bundle"
    expected = [expected_version]
    if len(commit) == 64:
        expected += ["@object-format=sha256"]
    expected += [commit + " refs/heads/candidate"]
    if lines != expected:
        raise ArchiveError("bundle must contain only the selected candidate head, no prerequisites")
    git(["init", "--bare", "--object-format=" + ("sha256" if len(commit) == 64 else "sha1"), str(scratch)])
    git(["--git-dir=" + str(scratch), "bundle", "verify", str(path)])
    # Index the pack in scratch and verify commit existence/type and full connectivity.
    git(["--git-dir=" + str(scratch), "bundle", "unbundle", str(path)])
    if git(["--git-dir=" + str(scratch), "cat-file", "-t", commit]) != "commit":
        raise ArchiveError("selected bundle object is not a commit")
    git(["--git-dir=" + str(scratch), "update-ref", "refs/heads/candidate", commit])
    git(["--git-dir=" + str(scratch), "fsck", "--strict", "--no-reflogs"])


def repository_bundle(value, base, root, destination, work):
    keys(value, ("commit",), ("path", "bundle", "bundleDigest"))
    commit = value["commit"]
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ArchiveError("repositories require an explicit full commit, never a ref/tag")
    if ("path" in value) == ("bundle" in value):
        raise ArchiveError("choose a local repository path OR a pinned existing bundle")
    if "bundleDigest" in value:
        digest_hex(value["bundleDigest"])
    if "bundle" in value:
        bundle = keys(value["bundle"], ("path", "digest", "size"))
        source = input_path(bundle["path"], base, root)
        with open_regular(source) as stream, open(destination, "xb") as output:
            copy_verified(stream, output, bundle["digest"], bundle["size"])
            output.flush()
            os.fchmod(output.fileno(), 0o444)
            os.fsync(output.fileno())
    else:
        source = input_path(value["path"], base, root)
        common = Path(git(["-C", str(source), "rev-parse", "--path-format=absolute", "--git-common-dir"]))
        if root is not None and (not (common / "objects").resolve().is_relative_to(root.resolve()) or
                                 (common / "objects/info/alternates").exists()):
            raise ArchiveError("repository object store escapes --input-root")
        object_format = git(["-C", str(source), "rev-parse", "--show-object-format"])
        if object_format not in ("sha1", "sha256") or (len(commit) == 64) != (object_format == "sha256"):
            raise ArchiveError("commit/object-format mismatch")
        isolated = work / "build.git"
        git(["init", "--bare", "--object-format=" + object_format, str(isolated)])
        env = git_env()
        # Only object storage is shared. The isolated repo has no source config,
        # hooks, refs, replace objects, worktree, or shallow-boundary metadata.
        env["GIT_OBJECT_DIRECTORY"] = str(common / "objects")
        if git(["--git-dir=" + str(isolated), "cat-file", "-t", commit], env=env) != "commit":
            raise ArchiveError("selected object is not a commit")
        git(["--git-dir=" + str(isolated), "update-ref", "refs/heads/candidate", commit], env=env)
        version = "3" if object_format == "sha256" else "2"
        git(["--git-dir=" + str(isolated), "bundle", "create", "--version=" + version,
             str(destination), "refs/heads/candidate"], env=env)
        with open_regular(destination) as stream:
            os.fchmod(stream.fileno(), 0o444)
            os.fsync(stream.fileno())
    verify_bundle(destination, commit, work / "verify.git")
    record = file_record(destination)
    if record["size"] > DEFAULT_LIMITS.max_file_bytes:
        raise ArchiveError("Git bundle file limit exceeded")
    if "bundleDigest" in value and value["bundleDigest"] != record["digest"]:
        raise ArchiveError("generated bundleDigest does not match selected digest")
    return {"commit": commit, "bundleDigest": record["digest"]}


def deployment_shape(value, services):
    keys(value, ("schema", "environment", "resources", "admin"))
    if value["schema"] != DEPLOYMENT_SCHEMA or value["environment"] != "staging-cell":
        raise ArchiveError("deployment must use kerosene.stack.deployment/v1 staging-cell")
    keys(value["admin"], ("image", "config"))
    if not isinstance(value["admin"]["config"], dict) or value["admin"]["image"] != services["admin"]["image"]:
        raise ArchiveError("deployment admin must match selected pinned Admin image and JSON object config")
    if not isinstance(value["resources"], list) or not all(isinstance(r, dict) for r in value["resources"]):
        raise ArchiveError("deployment resources must be an array of Kubernetes resource objects")
    # Only an envelope check: the executor owns full Kubernetes/RBAC/configDigest
    # policy validation. This packager never interprets a manifest as instructions.


def lifecycle_configuration(raw, deployment, services):
    """Use only installed controller code, never a module from selected sources."""
    lifecycle_path = Path(__file__).with_name("lifecycle.py")
    if not lifecycle_path.exists():
        if any("configDigest" not in service for service in services.values()):
            raise ArchiveError("computing configDigest requires the installed lifecycle.py")
        return services, "unverified-lifecycle-unavailable", {}
    stack_path = Path(__file__).parents[1] / "kerosene-stack"
    admin_path = lifecycle_path.with_name("admin_install.py")
    controller_paths = {"kerosene-stack": stack_path, "lifecycle.py": lifecycle_path,
                        "admin_install.py": admin_path,
                        "postgres/create-service-databases.sql": stack_path.parent / "runtime/postgres/create-service-databases.sql",
                        "postgres/service-runtime-grants.sql": stack_path.parent / "runtime/postgres/service-runtime-grants.sql"}
    for path in controller_paths.values():
        if path.is_symlink() or not path.is_file():
            raise ArchiveError("installed controller and dependencies must be regular local files")
    # Python reuses imported module names. Refuse a module from a different
    # checkout before loading the controller, not after calling its validator.
    for module_name, path in (("lifecycle", lifecycle_path), ("admin_install", admin_path)):
        cached = sys.modules.get(module_name)
        if cached is not None and Path(getattr(cached, "__file__", "")).resolve() != path.resolve():
            raise ArchiveError("cached controller dependency is not the installed module: " + module_name)
    controller_tools = {name: file_record(path)["digest"] for name, path in controller_paths.items()}
    loader = importlib.machinery.SourceFileLoader("candidate_installed_stack", str(stack_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    stack = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = stack
    loader.exec_module(stack)
    lifecycle = stack.lifecycle
    if Path(lifecycle.__file__).resolve() != lifecycle_path.resolve():
        raise ArchiveError("lifecycle module did not load from the installed controller directory")
    if Path(lifecycle.admin_install.__file__).resolve() != admin_path.resolve():
        raise ArchiveError("Admin installer did not load from the installed controller directory")
    selected = {name: dict(service) for name, service in services.items()}
    try:
        for name, service in selected.items():
            computed = lifecycle.digest(lifecycle.component_config(deployment, service["image"], name))
            if "configDigest" in service and service["configDigest"] != computed:
                raise ArchiveError("selected configuration digest mismatch: " + name)
            service["configDigest"] = computed
        with tempfile.TemporaryDirectory(prefix="kerosene-candidate-manifest-") as temporary:
            path = Path(temporary) / "deployment.json"
            write_new(path, raw)
            lifecycle.verify_deployment(stack, {"services": selected}, {"services": selected}, str(path))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ArchiveError("installed lifecycle rejected candidate configuration: " + str(exc)) from exc
    if controller_tools != {name: file_record(path)["digest"] for name, path in controller_paths.items()}:
        raise ArchiveError("installed controller dependency changed during candidate validation")
    return selected, "verified-installed-lifecycle", controller_tools


def deterministic_tar(tree, destination):
    paths = sorted(p for p in tree.rglob("*") if p.is_file())
    if len(paths) > DEFAULT_LIMITS.max_members:
        raise ArchiveError("candidate member limit exceeded")
    total = 0
    with open(destination, "xb") as output:
        with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for path in paths:
                relative = path.relative_to(tree).as_posix()
                safe_member_name(relative)
                with open_regular(path) as source:
                    size = os.fstat(source.fileno()).st_size
                    if size > DEFAULT_LIMITS.max_file_bytes:
                        raise ArchiveError("candidate member byte limit exceeded")
                    total += 512 + ((size + 511) // 512) * 512
                    if total + 10240 > DEFAULT_LIMITS.max_archive_bytes:
                        raise ArchiveError("candidate archive byte limit exceeded")
                    header = tarfile.TarInfo(relative)
                    header.size = size
                    header.mode = 0o444
                    header.uid = header.gid = header.mtime = 0
                    header.uname = header.gname = ""
                    archive.addfile(header, source)
        output.flush()
        os.fchmod(output.fileno(), 0o444)
        os.fsync(output.fileno())


def assemble(selection_path, output, *, input_root=None):
    selection_path = Path(os.path.abspath(selection_path))
    base = selection_path.parent
    root = Path(input_root) if input_root is not None else None
    if root is not None and not selection_path.resolve().is_relative_to(root.resolve()):
        raise ArchiveError("selection escapes --input-root")
    _, selection = json_file(selection_path)
    keys(selection, ("schema", "repositories", "services", "deployment", "sbom"))
    if selection["schema"] != INPUT_SCHEMA:
        raise ArchiveError("unsupported release candidate input schema")
    repositories = selection["repositories"]
    services = selection["services"]
    if not isinstance(repositories, dict) or not 1 <= len(repositories) <= 32:
        raise ArchiveError("select 1..32 repositories explicitly")
    if not isinstance(services, dict) or "admin" not in services or not 1 <= len(services) <= 64:
        raise ArchiveError("select 1..64 services, including admin")
    for service, value in services.items():
        name(service)
        keys(value, ("image",), ("configDigest",))
        if not isinstance(value["image"], str) or not IMAGE.fullmatch(value["image"]):
            raise ArchiveError("service images must be pinned OCI references")
        if "configDigest" in value:
            digest_hex(value["configDigest"])
    raw_deployment, deployment = material(selection["deployment"], base, root)
    deployment_shape(deployment, services)
    services, config_verification, controller_tools = lifecycle_configuration(raw_deployment, deployment, services)
    raw_sbom, sbom = material(selection["sbom"], base, root)
    if not isinstance(sbom, dict) or not (sbom.get("bomFormat") == "CycloneDX" or
                                         str(sbom.get("spdxVersion", "")).startswith("SPDX-")):
        raise ArchiveError("provide a digest-selected SPDX or CycloneDX JSON SBOM")
    canonical_digest = bytes_digest(canonical_bytes(deployment))
    output = Path(os.path.abspath(output))
    private_directory(output.parent)
    if os.path.lexists(output):
        raise ArchiveError("candidate output already exists")
    staged = Path(tempfile.mkdtemp(prefix=".candidate-", dir=output.parent))
    try:
        with tempfile.TemporaryDirectory(prefix=".pack-work-", dir=output.parent) as scratch:
            work = Path(scratch)
            tree = work / "payload"
            (tree / "sources").mkdir(parents=True, mode=0o700)
            write_new(tree / "deployment.json", raw_deployment)
            write_new(tree / "sbom.json", raw_sbom)
            sources = {}
            (work / "repos").mkdir(mode=0o700)
            for repository in sorted(repositories):
                name(repository)
                repo_work = work / "repos" / repository
                repo_work.mkdir(mode=0o700)
                sources[repository] = repository_bundle(repositories[repository], base, root,
                                                       tree / "sources" / (repository + ".bundle"), repo_work)
            # Exclude machine paths, wall clock, branch tips and the outer archive
            # digest (which would create a self-referential provenance cycle).
            portable_selection = {"schema": INPUT_SCHEMA, "repositories": sources,
                                  "services": services, "deployment": selection["deployment"]["digest"],
                                  "sbom": selection["sbom"]["digest"]}
            write_new(tree / "selection.json", canonical_bytes(portable_selection))
            entries = [file_record(p, p.relative_to(tree).as_posix())
                       for p in sorted(tree.rglob("*")) if p.is_file()]
            tools = {"package-release.py": file_record(Path(__file__))['digest'],
                     "archive.py": file_record(Path(__file__).with_name("archive.py"))['digest'],
                     "git": git(["--version"]), **controller_tools}
            provenance = {"_type": "https://in-toto.io/Statement/v1",
                          "subject": [{"name": e["path"], "digest": {"sha256": e["digest"][7:]}}
                                      for e in entries],
                          "predicateType": "urn:kerosene:release-candidate-assembly:v1",
                          "predicate": {"candidate": True, "trusted": False, "signed": False,
                                        "tools": tools, "selection": portable_selection,
                                        "configurationVerification": config_verification,
                                        "canonicalRenderedManifestsDigest": canonical_digest}}
            write_new(tree / "provenance.json", canonical_bytes(provenance))
            entries.append(file_record(tree / "provenance.json", "provenance.json"))
            entries.sort(key=lambda e: e["path"])
            deterministic_tar(tree, staged / "candidate.tar")
            index = {"schema": INDEX_SCHEMA, "status": "unsigned-candidate", "candidate": True, "trusted": False,
                     "archive": {**file_record(staged / "candidate.tar"), "type": "file",
                                 "format": "tar", "mediaType": "application/x-tar"},
                     "configurationVerification": config_verification,
                     "canonicalRenderedManifestsDigest": canonical_digest,
                     "entries": entries, "repositories": sources, "services": services}
            write_new(staged / "index.json", canonical_bytes(index))
        fsync_directory(staged)
        publish_directory(staged, output)
        return index
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--output", required=True, help="NEW directory containing index.json and candidate.tar")
    parser.add_argument("--input-root", help="confine every input and Git object store to this root (CI)")
    args = parser.parse_args()
    try:
        result = assemble(args.selection, args.output, input_root=args.input_root)
        print(canonical_bytes(result).decode("utf-8"))
    except (OSError, ValueError) as exc:
        parser.exit(1, "release candidate: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
