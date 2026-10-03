"""Install the approved operator distribution without starting its OCI image.

Only the installed controller calls this after release authorization. Receipts
are local integrity records, not independent release/build attestations.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid
import urllib.parse

MAX_ARCHIVE = 32 * 1024 * 1024
MAX_FILES = 256
SCHEMA = "kerosene.stack.admin-installation/v1"


def validate_config(config):
    if not isinstance(config, dict) or "apiBaseUrl" not in config or set(config) - {"apiBaseUrl", "kfeBaseUrl"}:
        raise RuntimeError("Admin config requires apiBaseUrl and permits only optional kfeBaseUrl")
    for value in config.values():
        if not isinstance(value, str) or len(value) > 2048 or any(ord(char) <= 32 for char in value):
            raise RuntimeError("Admin configuration requires bounded HTTPS origins")
        try:
            url = urllib.parse.urlsplit(value)
            port = url.port
        except ValueError as error:
            raise RuntimeError("Admin configuration has an invalid HTTPS origin") from error
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment
                or url.path not in {"", "/"} or (port is not None and not 1 <= port <= 65535)):
            raise RuntimeError("Admin configuration permits only credential-free HTTPS origins")
    return config


def command_prefix(config, target, arguments):
    validate_config(config)
    for argument in arguments:
        if argument.startswith("@"):
            raise RuntimeError("Installed Admin argument files are forbidden")
        if any(argument == name or argument.startswith(name + "=")
               for name in ("--endpoint", "--kfe-endpoint", "--profile")):
            raise RuntimeError("Installed Admin endpoint/profile overrides are forbidden; use the approved configuration")
    if target == "core":
        return ["--endpoint", config["apiBaseUrl"]]
    if target == "kfe" and config.get("kfeBaseUrl"):
        return ["--kfe-endpoint", config["kfeBaseUrl"]]
    raise RuntimeError("Requested Admin target is not configured in the approved release")


def private_directory(path):
    path = Path(path).absolute()
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise RuntimeError("Admin installation path cannot contain symlinks")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Admin installation requires an owner-only directory")
    return path


def directory(parent, name):
    target = parent / name
    target.mkdir(mode=0o700, exist_ok=True)
    return private_directory(target)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def require_java(run):
    java_home = os.environ.get("JAVA_HOME")
    java = str(Path(java_home) / "bin/java") if java_home else shutil.which("java")
    if not java:
        raise RuntimeError("Installed Admin requires a provisioned Java 21 or newer runtime")
    version = run([java, "--version"]).decode("utf-8", errors="replace")
    match = re.match(r"(?:openjdk|java) ([0-9]+)(?:[. +\n]|$)", version)
    if not match or int(match[1]) < 21:
        raise RuntimeError("Installed Admin requires Java 21 or newer")


def capture_archive(argv):
    """Bound both Docker output and elapsed time; never echo engine output."""
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
        try:
            output = bytearray()
            deadline = time.monotonic() + 60
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise RuntimeError("Admin archive transfer timed out")
                    chunk = os.read(child.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > MAX_ARCHIVE:
                        raise RuntimeError("Admin archive exceeds transfer limit")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or child.wait(timeout=remaining) != 0:
                raise RuntimeError("Admin archive transfer failed")
            return bytes(output)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


def extract_distribution(raw, destination):
    """Accept only regular launcher/JAR files under Docker cp's fixed prefix."""
    if len(raw) > MAX_ARCHIVE:
        raise RuntimeError("Admin archive exceeds transfer limit")
    files, seen, total = {}, set(), 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for count, member in enumerate(archive, 1):
                name = member.name.rstrip("/")
                parts = PurePosixPath(name).parts
                if (count > MAX_FILES or name in seen or not parts
                        or parts[0] != "kerosene-jctl" or ".." in parts
                        or "\\" in name or str(PurePosixPath(name)) != name):
                    raise RuntimeError("Admin archive has invalid or duplicate paths")
                seen.add(name)
                if member.isdir() and name in {"kerosene-jctl", "kerosene-jctl/bin", "kerosene-jctl/lib"}:
                    continue
                relative = "/".join(parts[1:])
                allowed = relative in {"bin/kerosene-jctl", "bin/kerosene-jctl.bat"} or (
                    len(parts) == 3 and parts[1] == "lib" and re.fullmatch(r"[A-Za-z0-9._+-]+\.jar", parts[2]))
                if not member.isreg() or not allowed or member.size <= 0:
                    raise RuntimeError("Admin archive permits only nonempty launcher and JAR files")
                total += member.size
                if total > MAX_ARCHIVE:
                    raise RuntimeError("Admin distribution exceeds extraction limit")
                target = destination / relative
                target.parent.mkdir(mode=0o700, exist_ok=True)
                data = archive.extractfile(member).read(member.size + 1)
                if len(data) != member.size:
                    raise RuntimeError("Admin archive is truncated")
                with target.open("xb") as handle:
                    os.fchmod(handle.fileno(), 0o700 if relative == "bin/kerosene-jctl" else 0o600)
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                files[relative] = "sha256:" + hashlib.sha256(data).hexdigest()
    except (tarfile.TarError, EOFError) as error:
        raise RuntimeError("Admin archive is invalid") from error
    if "bin/kerosene-jctl" not in files or not any(name.startswith("lib/") for name in files):
        raise RuntimeError("Admin distribution lacks launcher or libraries")
    for folder in (destination / "bin", destination / "lib", destination):
        sync_directory(folder)
    return files


def verify_installation(stack, target, expected):
    private_directory(target)
    info = (target / "installation.json").lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Admin installation receipt is not private and regular")
    receipt = stack.read_json_document(str(target / "installation.json"), "Admin installation")
    if receipt != expected or receipt.get("schema") != SCHEMA:
        raise RuntimeError("Admin installation receipt differs from committed evidence")
    files = receipt.get("files")
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise RuntimeError("Admin installation has invalid file inventory")
    validate_config(receipt.get("config"))
    inventory = set()
    for path in target.rglob("*"):
        name = str(path.relative_to(target))
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            if name not in {"bin", "lib"}:
                raise RuntimeError("Admin installation contains an unexpected directory")
            private_directory(path)
        elif stat.S_ISREG(info.st_mode):
            inventory.add(name)
        else:
            raise RuntimeError("Admin installation contains a link or special file")
    if inventory != set(files) | {"installation.json"}:
        raise RuntimeError("Admin installation file inventory changed")
    for name, digest in files.items():
        if name not in {"bin/kerosene-jctl", "bin/kerosene-jctl.bat"} and not re.fullmatch(r"lib/[A-Za-z0-9._+-]+\.jar", name):
            raise RuntimeError("Admin installation contains an invalid path")
        path = target / name
        private_directory(path.parent)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("Admin installation file is not private and regular")
        raw = stack.read_regular_file_bytes(path, "Admin distribution file", MAX_ARCHIVE)
        if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
            raise RuntimeError("Admin installation file digest changed")
    launcher = target / "bin/kerosene-jctl"
    if not os.access(launcher, os.X_OK):
        raise RuntimeError("Admin launcher is not executable")
    return launcher


def install(stack, cell_dir, cell_id, summary, config, update_id, run):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", update_id):
        raise RuntimeError("Admin installation requires the exact release digest")
    require_java(run)
    validate_config(config)
    cell = private_directory(cell_dir)
    root = directory(directory(cell, "admin"), "installations")
    target = root / update_id.removeprefix("sha256:")
    service = summary["services"]["admin"]
    expected = {"schema": SCHEMA, "updateId": update_id, "cellId": cell_id,
                "image": service["image"], "configDigest": service["configDigest"], "config": config}
    lock = os.open(cell / "admin-install.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("Admin installation lock is not private and regular")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if target.is_symlink():
            raise RuntimeError("Admin installation destination cannot be a symlink")
        if target.exists():
            receipt = stack.read_json_document(str(target / "installation.json"), "Admin installation")
            if {key: receipt.get(key) for key in expected} != expected:
                raise RuntimeError("Existing Admin installation belongs to another release")
            verify_installation(stack, target, receipt)
            return receipt
        docker = shutil.which("docker")
        if not docker or any(os.environ.get(name) for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")):
            raise RuntimeError("Admin installation requires the local default Docker context without overrides")
        command = [docker, "--context", "default"]
        images = json.loads(run(command + ["image", "inspect", service["image"]]))
        if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict) or service["image"] not in (images[0].get("RepoDigests") or []) or not re.fullmatch(r"sha256:[0-9a-f]{64}", images[0].get("Id", "")):
            raise RuntimeError("Cached Admin OCI image identity differs from approved release")
        name = "kerosene-admin-extract-" + uuid.uuid4().hex
        try:
            # Creation never starts the entrypoint, executes candidate code or
            # mounts host credentials. Pin the locally inspected image ID.
            run(command + ["create", "--name", name, "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", images[0]["Id"]])
            raw = capture_archive(command + ["cp", name + ":/opt/kerosene-jctl", "-"])
        finally:
            # Exact disposable name only; never force-remove a running container.
            run(command + ["rm", name])
        with tempfile.TemporaryDirectory(prefix=".admin-stage-", dir=root) as staging:
            staged = Path(staging) / "distribution"
            staged.mkdir(mode=0o700)
            receipt = {**expected, "files": extract_distribution(raw, staged)}
            stack.atomic_write_json(str(staged / "installation.json"), receipt, mode=0o600)
            verify_installation(stack, staged, receipt)
            os.rename(staged, target)
            sync_directory(root)
        return receipt
    except (ValueError, TypeError, KeyError) as error:
        raise RuntimeError("Admin installation input validation failed") from error
    finally:
        os.close(lock)
