"""Bounded offline publication. Sign only with explicit operator key references.

Installed deploy code supplies release/TUF validation; material is inert data.
Private PEM files are read into private temporary storage, never into output.
"""

import base64
import ctypes
import datetime as dt
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from dataclasses import dataclass

sys.dont_write_bytecode = True
from archive import (canonical_bytes, strict_json, bytes_digest,
                     safe_member_name, write_new, fsync_directory)


class PublicationError(ValueError):
    pass


ROLES = ("targets", "snapshot", "timestamp")
MAX_INTEGER = 9007199254740991
LEDGER_FILE = "publication-ledger.json"
RESERVATION_FILE = "publication-reservation.json"
LEDGER_SCHEMA = "kerosene.publication-ledger/v1"


@dataclass(frozen=True)
class Limits:
    metadata_bytes: int = 1024 * 1024
    key_bytes: int = 16 * 1024
    artifact_bytes: int = 512 * 1024 * 1024
    total_artifact_bytes: int = 1024 * 1024 * 1024
    artifacts: int = 1024
    role_keys: int = 32
    total_seconds: int = 300
    command_seconds: int = 10
    lock_seconds: int = 10
    ledger_bytes: int = 64 * 1024

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise PublicationError("limits must be positive integers")
        if self.metadata_bytes > 1024 * 1024 or self.artifacts > 1024:
            raise PublicationError("limits cannot exceed jctl metadata/count bounds")
        if self.artifact_bytes > 2 * 1024 * 1024 * 1024:
            raise PublicationError("artifact limit cannot exceed jctl's 2 GiB bound")
        if self.ledger_bytes > 64 * 1024:
            raise PublicationError("ledger limit cannot exceed 64 KiB")


def controller(limits=None, deadline=None):
    loader = importlib.machinery.SourceFileLoader(
        "kerosene_publication_controller", str(Path(__file__).parent.parent / "kerosene-stack"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    if limits is not None:
        def bounded_run(*args, **kwargs):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PublicationError("publication deadline exceeded")
            kwargs["timeout"] = min(limits.command_seconds, remaining)
            kwargs.setdefault("stdin", subprocess.DEVNULL)
            try:
                return subprocess.run(*args, **kwargs)
            except subprocess.TimeoutExpired as error:
                raise PublicationError("OpenSSL verification exceeded its time bound") from error
        # Bound the installed verifier's OpenSSL subprocesses without changing
        # its source or the process-global subprocess module.
        module.subprocess = SimpleNamespace(run=bounded_run, PIPE=subprocess.PIPE)
    return module


def fields(value, required):
    if type(value) is not dict or set(value) != set(required):
        raise PublicationError("unexpected/missing fields; expected " + ", ".join(required))
    return value


def integer(value, minimum=1, maximum=MAX_INTEGER):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PublicationError("integer outside publication bounds")
    return value


def absolute(path):
    path = Path(path)
    if ".." in path.parts:
        raise PublicationError("parent traversal in local path")
    return path if path.is_absolute() else Path.cwd() / path


def open_nolinks(path, *, directory=False):
    """Pin every parent directory with dirfd traversal, not a check/open pair."""
    path = absolute(path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for index, part in enumerate(path.parts[1:]):
            is_dir = directory or index < len(path.parts) - 2
            child = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                            os.O_CLOEXEC | (os.O_DIRECTORY if is_dir else 0), dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            raise PublicationError("regular file/directory required")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_bytes(path, maximum, *, private=False):
    fd = open_nolinks(path)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if private and (info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1):
            raise PublicationError("private signer file must be operator-owned, mode 0600/0400, single-link")
        if info.st_size > maximum:
            raise PublicationError("file byte limit exceeded")
        data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise PublicationError("file byte limit exceeded")
        return data


def json_file(path, limits):
    raw = read_bytes(path, limits.metadata_bytes)
    value = strict_json(raw)
    # Preflight canonicalization also rejects lone surrogates/overflow/deep data.
    if len(canonical_bytes(value)) > limits.metadata_bytes:
        raise PublicationError("canonical metadata byte limit exceeded")
    return raw, value


def digest(value):
    if type(value) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise PublicationError("invalid ledger SHA-256 digest")
    return value


def rename_noreplace(fd, source, destination):
    """Atomic Linux rename, relative to the same pinned store descriptor."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise PublicationError("atomic no-replace publication requires Linux renameat2")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(fd, os.fsencode(source), fd, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def publish_directory(staged, destination, *, store_fd):
    rename_noreplace(store_fd, staged.name, destination.name)
    os.fsync(store_fd)


def bundle_bytes_digest(path, limits, deadline):
    """Bind the complete bounded file/directory inventory, including raw bytes.

    Streaming dirfd traversal rejects links, special nodes and excess entries.
    Metadata/signature and artifact bytes are all bound, not just signed payloads.
    """
    files, directories = [], []
    total, nodes = 0, 0
    max_files = limits.artifacts + 9
    max_nodes = max_files * 36

    def walk(fd, prefix="", depth=0):
        nonlocal total, nodes
        if depth > 36:
            raise PublicationError("publication inventory depth exceeds bounds")
        with os.scandir(fd) as entries:
            for entry in entries:
                if time.monotonic() > deadline:
                    raise PublicationError("publication deadline exceeded")
                nodes += 1
                name = prefix + entry.name
                if nodes > max_nodes or len(name.encode("utf-8")) > 512:
                    raise PublicationError("publication inventory exceeds bounds")
                # Input artifact limits apply before adding the bundle prefixes.
                if (entry.name in (".", "..") or any(ord(c) < 32 or ord(c) == 127 for c in entry.name)
                        or ":" in entry.name or "\\" in entry.name):
                    raise PublicationError("unsafe publication inventory path")
                child = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                                os.O_CLOEXEC, dir_fd=fd)
                try:
                    info = os.fstat(child)
                    if stat.S_ISDIR(info.st_mode):
                        directories.append(name)
                        walk(child, name + "/", depth + 1)
                    elif stat.S_ISREG(info.st_mode):
                        if len(files) >= max_files:
                            raise PublicationError("publication file count exceeds bounds")
                        maximum = limits.artifact_bytes if name.startswith("targets/artifacts/") else limits.metadata_bytes
                        if info.st_size > maximum:
                            raise PublicationError("publication file byte limit exceeded")
                        count, hasher = 0, hashlib.sha256()
                        while True:
                            if time.monotonic() > deadline:
                                raise PublicationError("publication deadline exceeded")
                            chunk = os.read(child, min(65536, maximum - count + 1))
                            if not chunk:
                                break
                            count += len(chunk)
                            total += len(chunk)
                            if count > maximum or total > limits.total_artifact_bytes + 9 * limits.metadata_bytes:
                                raise PublicationError("publication inventory byte limit exceeded")
                            hasher.update(chunk)
                        files.append({"path": name, "length": count, "sha256": hasher.hexdigest()})
                    else:
                        raise PublicationError("publication inventory must contain only directories and regular files")
                finally:
                    os.close(child)

    fd = open_nolinks(path, directory=True)
    try:
        walk(fd)
    finally:
        os.close(fd)
    return bytes_digest(canonical_bytes({"directories": sorted(directories),
                                         "files": sorted(files, key=lambda item: item["path"])}))


class PublicationStore:
    """Local high-water state for cooperating publishers of ONE selected store.

    No automatic crash recovery/reset. A durable reservation blocks all reuse.
    flock targets the directory inode itself, so deleting a lock file cannot fork it.
    """

    def __init__(self, path, limits, deadline):
        self.path, self.limits, self.deadline = absolute(path), limits, deadline
        self.fd, self.state, self.state_raw, self.candidate = None, None, None, None

    def __enter__(self):
        self.fd = open_nolinks(self.path, directory=True)
        try:
            info = os.fstat(self.fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise PublicationError("publication store must be operator-owned with mode 0700")
            self.identity = (info.st_dev, info.st_ino)
            lock_deadline = min(self.deadline, time.monotonic() + self.limits.lock_seconds)
            while True:
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= lock_deadline:
                        raise PublicationError("publication store lock timeout")
                    time.sleep(min(0.05, max(0, lock_deadline - time.monotonic())))
            self.assert_pinned()
            try:
                os.stat(RESERVATION_FILE, dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise PublicationError("unresolved publication reservation; manual re-verification required")
            self.state_raw = self.read_ledger()
            if self.state_raw is not None:
                self.state = self.validate_ledger(self.state_raw)
            return self
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def __exit__(self, *error):
        if self.fd is not None:
            os.close(self.fd)  # Releases flock, including exceptional paths.
            self.fd = None

    def assert_pinned(self):
        fd = open_nolinks(self.path, directory=True)
        try:
            info = os.fstat(fd)
            if ((info.st_dev, info.st_ino) != self.identity or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise PublicationError("selected publication store identity/permissions changed")
        finally:
            os.close(fd)

    def read_ledger(self):
        try:
            fd = os.open(LEDGER_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=self.fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) not in (0o600, 0o400)):
                raise PublicationError("publication ledger must be a private operator-owned single-link regular file")
            if info.st_size > self.limits.ledger_bytes:
                raise PublicationError("publication ledger byte limit exceeded")
            raw = stream.read(self.limits.ledger_bytes + 1)
            if len(raw) > self.limits.ledger_bytes:
                raise PublicationError("publication ledger byte limit exceeded")
            return raw

    def child_name(self, path):
        path = absolute(path)
        if (path.parent != self.path or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", path.name)
                or path.name in (LEDGER_FILE, RESERVATION_FILE)):
            raise PublicationError("publication and baseline must be direct children of the selected store")
        return path.name

    def validate_ledger(self, raw):
        state = fields(strict_json(raw), ("schema", "generation", "trustedRootDigest", "networkId", "lastPublication"))
        if state["schema"] != LEDGER_SCHEMA or canonical_bytes(state) != raw:
            raise PublicationError("unsupported/noncanonical publication ledger")
        integer(state["generation"])
        digest(state["trustedRootDigest"])
        if type(state["networkId"]) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", state["networkId"]):
            raise PublicationError("invalid ledger network domain")
        last = fields(state["lastPublication"], ("name", "bytesDigest", "releaseId", "sequence",
                                                 "releaseLockCanonicalDigest", "metadataVersions", "expires"))
        if type(last["name"]) is not str:
            raise PublicationError("invalid ledger publication name")
        self.child_name(self.path / last["name"])
        if type(last["releaseId"]) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", last["releaseId"]):
            raise PublicationError("invalid ledger release ID")
        integer(last["sequence"])
        digest(last["bytesDigest"])
        digest(last["releaseLockCanonicalDigest"])
        fields(last["metadataVersions"], ("root", *ROLES))
        fields(last["expires"], ROLES)
        for value in last["metadataVersions"].values():
            integer(value)
        for value in last["expires"].values():
            expiry(value)
        return state

    def check(self, root_raw, summary, output, previous, initial):
        self.child_name(output)
        if self.state is None:
            with os.scandir(self.fd) as entries:
                if next(entries, None) is not None:
                    raise PublicationError("missing ledger in nonempty store; manual re-verification required")
            if not initial:
                raise PublicationError("empty store requires explicit initial publication")
        else:
            if initial:
                raise PublicationError("initial publication is allowed only in an empty store; reset/replay refused")
            if bytes_digest(root_raw) != self.state["trustedRootDigest"]:
                raise PublicationError("root rotation or changed trust anchor is unsupported within the selected store")
            if summary["networkId"] != self.state["networkId"]:
                raise PublicationError("new release must advance sequence in the same network with a new release ID")
            if previous is None or self.child_name(previous) != self.state["lastPublication"]["name"]:
                raise PublicationError("baseline must be the exact last verified publication in the selected store")

    def bind_previous(self, previous, documents, summary):
        last = self.state["lastPublication"]
        root_document = next(doc for name, doc in documents.items() if name.endswith(".root.json"))
        if (summary["networkId"] != self.state["networkId"]
                or root_document["signed"]["version"] != last["metadataVersions"]["root"]
                or summary["releaseId"] != last["releaseId"] or summary["sequence"] != last["sequence"]
                or {r: documents[r + ".json"]["signed"]["version"] for r in ROLES}
                != {r: last["metadataVersions"][r] for r in ROLES}
                or {r: documents[r + ".json"]["signed"]["expires"] for r in ROLES} != last["expires"]):
            raise PublicationError("verified baseline disagrees with ledger high-water state")
        lock = documents["targets.json"]["signed"]["targets"][summary["tuf"]["targetPath"]]
        if lock["custom"]["keroseneReleaseLockCanonicalDigest"] != last["releaseLockCanonicalDigest"]:
            raise PublicationError("verified baseline disagrees with ledger release digest")
        if bundle_bytes_digest(previous, self.limits, self.deadline) != last["bytesDigest"]:
            raise PublicationError("exact last publication bytes differ from the ledger")

    def atomic_json(self, name, value, *, replace=False):
        raw = canonical_bytes(value)
        if len(raw) > self.limits.ledger_bytes:
            raise PublicationError("publication ledger/reservation byte limit exceeded")
        temporary = ".ledger-tmp-" + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fchmod(stream.fileno(), 0o600)
                os.fsync(stream.fileno())
            if replace:
                os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            else:
                rename_noreplace(self.fd, temporary, name)
            os.fsync(self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def reserve(self, staged, output, root_raw, summary, versions, expires):
        self.assert_pinned()
        if self.read_ledger() != self.state_raw:
            raise PublicationError("publication ledger changed while locked")
        generation = integer(1 if self.state is None else self.state["generation"] + 1)
        self.candidate = {"schema": LEDGER_SCHEMA, "generation": generation,
                          "trustedRootDigest": bytes_digest(root_raw), "networkId": summary["networkId"],
                          "lastPublication": {"name": self.child_name(output),
                              "bytesDigest": bundle_bytes_digest(staged, self.limits, self.deadline),
                              "releaseId": summary["releaseId"], "sequence": summary["sequence"],
                              "releaseLockCanonicalDigest": summary["_publicationLockDigest"],
                              "metadataVersions": dict(versions), "expires": dict(expires)}}
        if self.state is not None and versions["root"] != self.state["lastPublication"]["metadataVersions"]["root"]:
            raise PublicationError("ledger root version cannot change")
        self.validate_ledger(canonical_bytes(self.candidate))
        if time.monotonic() > self.deadline:
            raise PublicationError("publication deadline exceeded")
        self.atomic_json(RESERVATION_FILE, {"schema": "kerosene.publication-reservation/v1",
                         "baseLedgerDigest": bytes_digest(self.state_raw) if self.state_raw is not None else None,
                         "candidate": self.candidate})

    def commit(self):
        # State and its parent must be durable before clearing the reservation.
        # Failures before that point leave a marker for manual re-verification.
        self.assert_pinned()
        if self.read_ledger() != self.state_raw:
            raise PublicationError("publication ledger changed while locked")
        self.atomic_json(LEDGER_FILE, self.candidate, replace=self.state is not None)
        os.unlink(RESERVATION_FILE, dir_fd=self.fd)
        os.fsync(self.fd)


def expiry(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise PublicationError("expiry must be UTC YYYY-MM-DDTHH:MM:SSZ")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PublicationError("invalid UTC expiry") from error


class SigningSession:
    def __init__(self, scratch, limits, deadline):
        self.scratch, self.limits, self.deadline = scratch, limits, deadline
        self.executable = shutil.which("openssl")
        if self.executable is None:
            raise PublicationError("OpenSSL with Ed25519 support is required")
        self.count = 0
        self.private_hashes = set()

    def run(self, args):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise PublicationError("publication deadline exceeded")
        # Commands are fixed, offline OpenSSL operations; never shell commands.
        try:
            result = subprocess.run([self.executable, *args], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=min(self.limits.command_seconds, remaining), check=False)
        except subprocess.TimeoutExpired as error:
            raise PublicationError("OpenSSL signing operation exceeded its time bound") from error
        if result.returncode:
            raise PublicationError("OpenSSL key/signature operation failed")

    def key(self, reference):
        raw = read_bytes(reference, self.limits.key_bytes, private=True)
        self.private_hashes.add(hashlib.sha256(raw).hexdigest())
        if not raw.startswith(b"-----BEGIN PRIVATE KEY-----\n"):
            raise PublicationError("only unencrypted PKCS#8 PEM file references are supported")
        self.count += 1
        path = self.scratch / ("key-" + str(self.count))
        write_new(path, raw)
        path.chmod(0o600)
        public = path.with_suffix(".pub")
        self.run(["pkey", "-in", str(path), "-pubout", "-outform", "DER", "-out", str(public)])
        der = read_bytes(public, 128)
        prefix = bytes.fromhex("302a300506032b6570032100")
        if len(der) != 44 or not der.startswith(prefix):
            raise PublicationError("private signer must be Ed25519")
        return path, der

    def sign(self, payload, key, der):
        message, signature = self.scratch / "message", self.scratch / "signature"
        # Scratch names belong solely to this invocation and contain no keys in output.
        if message.exists():
            message.unlink()
        if signature.exists():
            signature.unlink()
        write_new(message, payload)
        self.run(["pkeyutl", "-sign", "-rawin", "-inkey", str(key),
                  "-in", str(message), "-out", str(signature)])
        raw = read_bytes(signature, 64)
        if len(raw) != 64:
            raise PublicationError("invalid Ed25519 signature length")
        public = self.scratch / "verify.pub"
        if public.exists():
            public.unlink()
        write_new(public, der)
        self.run(["pkeyutl", "-verify", "-rawin", "-pubin", "-keyform", "DER",
                  "-inkey", str(public), "-in", str(message), "-sigfile", str(signature)])
        return raw


def root_policy(stack, document, limits):
    fields(document, ("signed", "signatures"))
    signed = fields(document["signed"], ("_type", "spec_version", "version", "expires",
                                          "consistent_snapshot", "keys", "roles"))
    if signed["consistent_snapshot"] is not False or signed["spec_version"] != "1.0.0":
        raise PublicationError("only TUF 1.0.0 with consistent_snapshot=false is supported")
    fields(signed["roles"], ("root", *ROLES))
    if type(signed["keys"]) is not dict or not 1 <= len(signed["keys"]) <= limits.role_keys * 4:
        raise PublicationError("root key count exceeds bounds")
    root = stack.parse_tuf_root(document, "publication trusted root")
    integer(root["version"])
    for keyids, threshold in root["roles"].values():
        if len(keyids) > limits.role_keys:
            raise PublicationError("role key count exceeds bounds")
        integer(threshold, maximum=limits.role_keys)
    if type(document["signatures"]) is not list or len(document["signatures"]) > limits.role_keys:
        raise PublicationError("root signature count exceeds bounds")
    ids, threshold = root["roles"]["root"]
    stack.verify_tuf_role(document, ids, threshold, root["keys"], "publication trusted root")
    return root


def record(data):
    return {"length": len(data), "hashes": {"sha256": hashlib.sha256(data).hexdigest()}}


def mkdir_staged(staged, path):
    """Create every directory with private mode, including intermediate nodes."""
    fd = open_nolinks(staged, directory=True)
    try:
        for part in path.relative_to(staged).parts:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd)
            os.close(fd)
            fd = child
    finally:
        os.close(fd)


def copy_artifact(source, destination, entry, limits, deadline):
    with os.fdopen(open_nolinks(source), "rb") as stream, open(destination, "xb") as output:
        if os.fstat(stream.fileno()).st_size != entry["size"]:
            raise PublicationError("artifact exact size mismatch")
        count, hasher = 0, hashlib.sha256()
        while True:
            if time.monotonic() > deadline:
                raise PublicationError("publication deadline exceeded")
            chunk = stream.read(min(65536, entry["size"] - count + 1))
            if not chunk:
                break
            count += len(chunk)
            if count > entry["size"] or count > limits.artifact_bytes:
                raise PublicationError("artifact exact size/byte bound exceeded")
            hasher.update(chunk)
            output.write(chunk)
        if count != entry["size"] or hasher.hexdigest() != entry["sha256"]:
            raise PublicationError("artifact exact size/SHA-256 mismatch")
        output.flush()
        os.fchmod(output.fileno(), 0o444)
        os.fsync(output.fileno())


def previous_bundle(previous, root_raw, root, stack, limits, scratch):
    """Authenticate a pinned prior bundle; no root rotation/delegations allowed."""
    previous = absolute(previous)
    fd = open_nolinks(previous / "metadata", directory=True)
    try:
        names = []
        # Stop at the first impossible count; do not materialize an arbitrary
        # operator-supplied directory before checking its bound.
        with os.scandir(fd) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names) > 4:
                    raise PublicationError("previous bundle delegation/rotation/extra metadata is unsupported")
        expected = {"timestamp.json", "snapshot.json", "targets.json", f"{root['version']}.root.json"}
        if len(names) != 4 or set(names) != expected:
            raise PublicationError("previous bundle delegation/rotation/extra metadata is unsupported")
    finally:
        os.close(fd)
    pinned = scratch / "previous-metadata"
    pinned.mkdir(mode=0o700)
    documents, raw = {}, {}
    for name in sorted(expected):
        raw[name], documents[name] = json_file(previous / "metadata" / name, limits)
        write_new(pinned / name, raw[name])
    if raw[f"{root['version']}.root.json"] != root_raw:
        raise PublicationError("root rotation or changed trust anchor is unsupported")
    for role in ROLES:
        doc = documents[role + ".json"]
        fields(doc, ("signed", "signatures"))
        signed = fields(doc["signed"], ("_type", "spec_version", "version", "expires",
                                         "targets" if role == "targets" else "meta"))
        if doc["signed"]["spec_version"] != "1.0.0":
            raise PublicationError("unsupported previous TUF specification")
        integer(signed["version"])
        if type(doc["signatures"]) is not list or len(doc["signatures"]) > limits.role_keys:
            raise PublicationError("previous signature count exceeds bounds")
        if role != "targets":
            fields(signed["meta"], ("targets.json" if role == "snapshot" else "snapshot.json",))
    targets = documents["targets.json"]["signed"]["targets"]
    if type(targets) is not dict or len(targets) > limits.artifacts + 2:
        raise PublicationError("previous target count exceeds bounds")
    locks = [p for p, target in targets.items()
             if type(target) is dict and type(target.get("custom")) is dict
             and "keroseneReleaseLockCanonicalDigest" in target["custom"]]
    if len(locks) != 1:
        raise PublicationError("previous bundle must describe exactly one release")
    safe_member_name(locks[0])
    lock_raw, release = json_file(previous / "targets" / locks[0], limits)
    summary = stack.validate_release(release)
    stack.verify_tuf_metadata_bundle(release, lock_raw, summary, str(pinned),
                                     str(pinned / f"{root['version']}.root.json"))
    return documents, summary


def publish(release_lock, package_descriptor, artifacts_dir, output, *, publication_store,
            trusted_root, signers, packaging_key, versions, expires, previous=None,
            initial=False, limits=Limits()):
    """Publish one immutable offline bundle; return only public summary fields.

    publication_store is an explicit pre-provisioned private directory. The
    directory lock covers baseline checking, signing, publication and commit.
    signers = {role: {root_key_id: explicit_PKCS8_PEM_path}}. No key discovery.
    initial=True requires an empty store and all role versions 1; otherwise
    previous must be the exact last verified child recorded by the ledger.
    """
    deadline = time.monotonic() + limits.total_seconds
    with PublicationStore(publication_store, limits, deadline) as store:
        return _publish(release_lock, package_descriptor, artifacts_dir, output,
                        trusted_root=trusted_root, signers=signers, packaging_key=packaging_key,
                        versions=versions, expires=expires, previous=previous, initial=initial,
                        limits=limits, deadline=deadline, store=store)


def _publish(release_lock, package_descriptor, artifacts_dir, output, *, trusted_root,
             signers, packaging_key, versions, expires, previous, initial, limits,
             deadline, store):
    stack = controller(limits, deadline)
    root_raw, root_doc = json_file(trusted_root, limits)
    root = root_policy(stack, root_doc, limits)
    lock_raw, release = json_file(release_lock, limits)
    summary = stack.validate_release(release)
    summary["_publicationLockDigest"] = bytes_digest(canonical_bytes(release))
    if summary["releaseSchemaVersion"] not in (2, 3):
        raise PublicationError("only detached-target release-lock v2/v3 publication is supported")
    _, descriptor = json_file(package_descriptor, limits)
    fields(descriptor, ("schema", "releaseId", "targetSequence", "releaseLockCanonicalDigest",
                        "deploymentManifestDigest", "artifacts"))
    if (descriptor["schema"] != "kerosene.cell-package/v1" or
            descriptor["releaseId"] != summary["releaseId"] or
            integer(descriptor["targetSequence"]) != summary["sequence"] or
            descriptor["releaseLockCanonicalDigest"] != bytes_digest(canonical_bytes(release))):
        raise PublicationError("package descriptor does not bind this exact release")
    entries = descriptor["artifacts"]
    if type(entries) is not list or not 1 <= len(entries) <= limits.artifacts:
        raise PublicationError("artifact count outside bounds")
    paths, total = set(), 0
    for entry in entries:
        fields(entry, ("path", "size", "sha256"))
        safe_member_name(entry["path"])
        if entry["path"] in paths:
            raise PublicationError("duplicate artifact path")
        paths.add(entry["path"])
        total += integer(entry["size"], minimum=0, maximum=limits.artifact_bytes)
        if type(entry["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise PublicationError("artifact SHA-256 must be lowercase hex")
    if total > limits.total_artifact_bytes:
        raise PublicationError("aggregate artifact byte limit exceeded")
    if any(parent.as_posix() in paths for p in paths for parent in Path(p).parents if str(parent) != "."):
        raise PublicationError("artifact file/directory path collision")
    deployment = next((e for e in entries if e["path"] == "deployment.json"), None)
    if deployment is None or deployment["size"] > limits.metadata_bytes or descriptor["deploymentManifestDigest"] != "sha256:" + deployment["sha256"]:
        raise PublicationError("package requires exact digest-bound deployment.json <= metadata limit")
    fields(versions, ROLES)
    fields(expires, ROLES)
    fields(signers, ROLES)
    if type(initial) is not bool or initial == (previous is not None):
        raise PublicationError("choose explicit initial publication OR previous bundle")
    for role in ROLES:
        integer(versions[role])
        if initial and versions[role] != 1:
            raise PublicationError("initial publication versions must all be 1")
        if expiry(expires[role]) <= dt.datetime.now(dt.timezone.utc):
            raise PublicationError("new metadata expiry is not in the future")
    if not (expiry(expires["timestamp"]) <= expiry(expires["snapshot"]) <=
            expiry(expires["targets"]) <= expiry(root["signed"]["expires"])):
        raise PublicationError("require timestamp <= snapshot <= targets <= root expiry")
    artifacts_dir, output = absolute(artifacts_dir), absolute(output)
    os.close(open_nolinks(artifacts_dir, directory=True))
    store.child_name(output)
    if os.path.lexists(output):
        raise PublicationError("publication output already exists")
    store.check(root_raw, summary, output, previous, initial)
    staged = Path(tempfile.mkdtemp(prefix=".publication-", dir=output.parent))
    try:
        with tempfile.TemporaryDirectory(prefix=".publication-keys-", dir=output.parent) as temp:
            scratch = Path(temp)
            prior = None
            if previous is not None:
                prior, old = previous_bundle(previous, root_raw, root, stack, limits, scratch)
                store.bind_previous(previous, prior, old)
                if old["networkId"] != summary["networkId"] or summary["sequence"] <= old["sequence"] or old["releaseId"] == summary["releaseId"]:
                    raise PublicationError("new release must advance sequence in the same network with a new release ID")
                for role in ROLES:
                    old_signed = prior[role + ".json"]["signed"]
                    if versions[role] <= old_signed["version"] or expiry(expires[role]) <= expiry(old_signed["expires"]):
                        raise PublicationError("metadata versions and expiries must strictly advance")
            session = SigningSession(scratch, limits, deadline)
            loaded = {}
            for role in ROLES:
                references = signers[role]
                ids, threshold = root["roles"][role]
                if type(references) is not dict or not threshold <= len(references) <= limits.role_keys:
                    raise PublicationError("explicit distinct signer references must meet " + role + " threshold")
                loaded[role] = {}
                for keyid, reference in sorted(references.items()):
                    if keyid not in ids:
                        raise PublicationError("signer key ID is not authorized for role " + role)
                    key, der = session.key(reference)
                    if der != root["keys"][keyid]:
                        raise PublicationError("private signer does not match declared TUF key ID")
                    loaded[role][keyid] = (key, der)
            package_key, package_der = session.key(packaging_key)
            if package_der in root["keys"].values():
                raise PublicationError("packaging key must be separate from all TUF authority keys")
            if any(e["sha256"] in session.private_hashes for e in entries):
                raise PublicationError("artifact selection contains private signer material")
            metadata = staged / "metadata"
            releases = staged / "targets" / "releases"
            artifact_output = staged / "targets" / "artifacts" / summary["releaseId"]
            for folder in (metadata, releases, artifact_output):
                mkdir_staged(staged, folder)
            lock_path = summary["tuf"]["targetPath"]
            write_new(staged / "targets" / lock_path, lock_raw)
            manifest_raw = canonical_bytes(descriptor)
            manifest_path = f"releases/{summary['releaseId']}.package.json"
            write_new(staged / "targets" / manifest_path, manifest_raw)
            write_new(staged / "manifest.json", manifest_raw)
            targets = {lock_path: {**record(lock_raw), "custom": {
                "keroseneReleaseLockCanonicalDigest": bytes_digest(canonical_bytes(release))}},
                manifest_path: record(manifest_raw)}
            for entry in sorted(entries, key=lambda e: e["path"]):
                destination = artifact_output / entry["path"]
                mkdir_staged(staged, destination.parent)
                copy_artifact(artifacts_dir / entry["path"], destination, entry, limits, deadline)
                targets[f"artifacts/{summary['releaseId']}/{entry['path']}"] = {
                    "length": entry["size"], "hashes": {"sha256": entry["sha256"]}}
            # Use only the installed lifecycle validator on the pinned deployment bytes.
            json_file(artifact_output / "deployment.json", limits)
            stack.lifecycle.verify_deployment(stack, release, summary, str(artifact_output / "deployment.json"))
            if prior:
                old_targets = prior["targets.json"]["signed"]["targets"]
                for path in set(targets) & set(old_targets):
                    if targets[path] != old_targets[path]:
                        raise PublicationError("immutable target path cannot change")
            signature = session.sign(manifest_raw, package_key, package_der)
            write_new(staged / "manifest.sig", base64.b64encode(signature))
            write_new(staged / "manifest.sig.raw", signature)
            write_new(metadata / f"{root['version']}.root.json", root_raw)
            child_name, child_raw, child_version = None, None, None
            for role in ROLES:
                signed = {"_type": role, "spec_version": "1.0.0", "version": versions[role], "expires": expires[role]}
                if role == "targets":
                    signed["targets"] = targets
                else:
                    signed["meta"] = {child_name: {"version": child_version, **record(child_raw)}}
                document = {"signed": signed, "signatures": [
                    {"keyid": keyid, "sig": session.sign(canonical_bytes(signed), key, der).hex()}
                    for keyid, (key, der) in loaded[role].items()]}
                child_name, child_raw, child_version = role + ".json", canonical_bytes(document), versions[role]
                if len(child_raw) > limits.metadata_bytes:
                    raise PublicationError("generated metadata byte limit exceeded")
                write_new(metadata / child_name, child_raw)
            # Actual consumer validates the complete chain before visibility.
            verified = stack.verify_tuf_metadata_bundle(release, lock_raw, summary, str(metadata),
                                                        str(metadata / f"{root['version']}.root.json"))
            if time.monotonic() > deadline:
                raise PublicationError("publication deadline exceeded")
            for folder, dirs, files in os.walk(staged, topdown=False):
                fsync_directory(folder)
            if time.monotonic() > deadline:
                raise PublicationError("publication deadline exceeded")
            store.reserve(staged, output, root_raw, summary, verified["metadataVersions"], expires)
            store.assert_pinned()
            publish_directory(staged, output, store_fd=store.fd)
            store.commit()
            return {"schema": "kerosene.release-publication-result/v1", "releaseId": summary["releaseId"],
                    "targetSequence": summary["sequence"], "releaseLockCanonicalDigest": descriptor["releaseLockCanonicalDigest"],
                    "packageManifestDigest": bytes_digest(manifest_raw), "artifactCount": len(entries),
                    "metadataVersions": verified["metadataVersions"], "tufSignatureVerified": True,
                    "publicationStoreGeneration": store.candidate["generation"],
                    "publicationBytesDigest": store.candidate["lastPublication"]["bytesDigest"],
                    "packagingSignatureVerified": True, "releaseAuthorized": False,
                    "deploymentExecuted": False, "qualification": "offline-publication-only"}
    finally:
        if staged.exists():
            shutil.rmtree(staged)
