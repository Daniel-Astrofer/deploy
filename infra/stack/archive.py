#!/usr/bin/env python3
"""Bounded, integrity-checked release data transport. Never establishes trust."""

import argparse
import contextlib
import ctypes
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
import time
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class ArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    max_archive_bytes: int = 512 * 1024 * 1024
    max_file_bytes: int = 256 * 1024 * 1024
    max_expanded_bytes: int = 1024 * 1024 * 1024
    max_members: int = 10000
    max_path_bytes: int = 240
    max_depth: int = 32
    max_ratio: int = 100
    timeout_seconds: int = 30
    total_seconds: int = 300

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise ArchiveError("limits must be positive integers")


DEFAULT_LIMITS = Limits()


def digest_hex(digest):
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ArchiveError("expected lowercase sha256:<64 hex>")
    return digest[7:]


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ArchiveError("duplicate JSON key")
            result[key] = value
        return result

    def invalid(value):
        raise ArchiveError("non-finite JSON number is unsupported")

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            invalid(value)
        return number

    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_float=finite, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ArchiveError("invalid canonicalizable JSON") from exc


def bytes_digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def private_directory(path):
    """Walk without following symlinks; final namespace must be operator-owned."""
    path = Path(os.path.abspath(path))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ArchiveError("namespace must be owned by this operator and not group/world writable")
    finally:
        os.close(fd)
    return path


def open_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ArchiveError("expected a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def copy_verified(source, destination, digest, size, limits=DEFAULT_LIMITS):
    expected = digest_hex(digest)
    if type(size) is not int or not 0 <= size <= limits.max_archive_bytes:
        raise ArchiveError("invalid size or archive byte limit exceeded")
    hasher = hashlib.sha256()
    count = 0
    deadline = time.monotonic() + limits.total_seconds
    # HTTPResponse.read(n) may internally wait for n bytes while a peer
    # trickles data forever. read1 returns after one underlying buffered read
    # so the total deadline is rechecked between network reads.
    read = getattr(source, "read1", source.read)
    while True:
        if time.monotonic() > deadline:
            raise ArchiveError("transfer total time limit exceeded")
        chunk = read(min(1024 * 1024, size - count + 1))
        if time.monotonic() > deadline:
            raise ArchiveError("transfer total time limit exceeded")
        if not chunk:
            break
        count += len(chunk)
        if count > size:
            raise ArchiveError("exact size mismatch (oversized)")
        hasher.update(chunk)
        if destination is not None:
            destination.write(chunk)
    if count != size or hasher.hexdigest() != expected:
        raise ArchiveError("SHA-256 or exact size mismatch")


def file_record(path, name=None, limits=DEFAULT_LIMITS):
    hasher = hashlib.sha256()
    size = 0
    with open_regular(path) as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > limits.max_archive_bytes:
                raise ArchiveError("file byte limit exceeded")
            hasher.update(chunk)
    return {"path": name or Path(path).name, "digest": "sha256:" + hasher.hexdigest(), "size": size}


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, data):
    """Exclusive staged-tree write; callers publish the containing directory."""
    with open(path, "xb") as stream:
        stream.write(data)
        stream.flush()
        os.fchmod(stream.fileno(), 0o444)
        os.fsync(stream.fileno())


def publish_directory(staged, destination):
    """Linux atomic no-replace directory publication; never overwrites output."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise ArchiveError("atomic no-replace publication requires Linux renameat2")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(staged), -100, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))
    fsync_directory(Path(destination).parent)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ArchiveError("mirror redirects are forbidden; configure the final HTTPS authority")


def _mirror_url(base, digest, allow_file):
    if not isinstance(base, str) or any(ord(c) < 33 for c in base) or "\\" in base:
        raise ArchiveError("invalid mirror URL")
    parsed = urlsplit(base)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ArchiveError("mirror URL must have no credentials, query or fragment")
    if parsed.scheme == "https" and parsed.hostname:
        # Accessing port also validates malformed ports before network use.
        parsed.port
    elif parsed.scheme == "file" and allow_file and parsed.netloc in ("", "localhost"):
        if not parsed.path.startswith("/"):
            raise ArchiveError("offline file mirror must be absolute")
    else:
        raise ArchiveError("mirrors require HTTPS; file:// requires explicit offline-file mode")
    return base.rstrip("/") + "/sha256/" + digest_hex(digest)


@contextlib.contextmanager
def _mirror_stream(url, limits):
    if urlsplit(url).scheme == "file":
        with open_regular(unquote(urlsplit(url).path)) as stream:
            yield stream
        return
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    with opener.open(Request(url, headers={"Accept-Encoding": "identity"}),
                     timeout=limits.timeout_seconds) as stream:
        if stream.status != 200 or stream.headers.get("Content-Encoding", "identity") != "identity":
            raise ArchiveError("mirror must return an unencoded HTTP 200 file")
        yield stream


def fetch(cache, digest, size, mirrors=(), *, offline=False,
          allow_file=False, limits=DEFAULT_LIMITS):
    """Return cache/sha256/HEX after rehashing, or fail. Mirror URLs are bases.

    offline=True never opens mirrors. allow_file=True is a separate, explicit
    offline-media import mode and forbids mixing network and local mirrors.
    """
    key = digest_hex(digest)
    if type(size) is not int or not 0 <= size <= limits.max_archive_bytes:
        raise ArchiveError("invalid expected size")
    urls = [_mirror_url(base, digest, allow_file) for base in mirrors]
    if allow_file and any(urlsplit(url).scheme != "file" for url in urls):
        raise ArchiveError("offline-file import cannot use network mirrors")
    cache = private_directory(cache)
    namespace = private_directory(cache / "sha256")
    target = namespace / key
    lockfd = os.open(namespace / (key + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(lockfd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ArchiveError("invalid cache lock")
        # Bounded contention rather than an indefinitely blocked installer.
        deadline = time.monotonic() + limits.total_seconds
        while True:
            try:
                fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ArchiveError("cache lock timeout")
                time.sleep(0.05)
        cache_error = "missing"
        try:
            with open_regular(target) as stream:
                copy_verified(stream, None, digest, size, limits)
            return target
        except (OSError, ArchiveError) as exc:
            cache_error = type(exc).__name__
        if offline:
            raise ArchiveError("offline cache object missing or corrupt: " + digest + " (" + cache_error + ")")
        failures = []
        for index, url in enumerate(urls):
            fd, temporary = tempfile.mkstemp(prefix=".download-", dir=namespace)
            try:
                with os.fdopen(fd, "wb") as destination, _mirror_stream(url, limits) as source:
                    copy_verified(source, destination, digest, size, limits)
                    destination.flush()
                    os.fchmod(destination.fileno(), 0o444)
                    os.fsync(destination.fileno())
                os.replace(temporary, target)
                fsync_directory(namespace)
                return target
            except (OSError, ValueError) as exc:
                # Do not log URLs, credentials or response bodies.
                failures.append("mirror " + str(index + 1) + ": " + type(exc).__name__)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        raise ArchiveError("no verified mirror supplied object " + digest + "; " + ", ".join(failures))
    finally:
        os.close(lockfd)


def safe_member_name(name, limits=DEFAULT_LIMITS):
    if not isinstance(name, str) or not name or name.startswith("/") or "\\" in name or ":" in name:
        raise ArchiveError("unsafe archive path")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ArchiveError("control character in archive path")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ArchiveError("archive path traversal/noncanonical path")
    if len(parts) > limits.max_depth or len(name.encode("utf-8")) > limits.max_path_bytes:
        raise ArchiveError("archive path limit exceeded")
    return PurePosixPath(name)


class _BoundedReader:
    def __init__(self, stream, limit, seconds):
        self.stream = stream
        self.remaining = limit
        self.deadline = time.monotonic() + seconds

    def read(self, size):
        if time.monotonic() > self.deadline:
            raise ArchiveError("extraction time limit exceeded")
        chunk = self.stream.read(min(size, self.remaining + 1))
        self.remaining -= len(chunk)
        if self.remaining < 0:
            raise ArchiveError("tar stream/expansion ratio limit exceeded")
        return chunk


def _exact(stream, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise ArchiveError("truncated tar")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _extract_tar(source, staged, archive_size, limits):
    magic = source.read(2)
    source.seek(0)
    decoded = gzip.GzipFile(fileobj=source, mode="rb") if magic == b"\x1f\x8b" else source
    stream = _BoundedReader(decoded, min(limits.max_expanded_bytes,
                                        archive_size * limits.max_ratio), limits.total_seconds)
    paths = set()
    nodes = set()
    count = total = 0
    try:
        while True:
            header = _exact(stream, 512)
            if header == bytes(512):
                if _exact(stream, 512) != bytes(512):
                    raise ArchiveError("invalid tar terminator")
                while chunk := stream.read(65536):
                    if any(chunk):
                        raise ArchiveError("nonzero trailing tar data")
                break
            count += 1
            if count > limits.max_members:
                raise ArchiveError("archive member limit exceeded")
            # Read one fixed header only. Do NOT let tarfile parse PAX/GNU
            # extension payloads, allocate unbounded names, or resolve links.
            member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
            if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE):
                raise ArchiveError("links, devices, sparse/PAX/GNU and special members forbidden")
            if header[257:263] not in (b"ustar\x00", bytes(6)):
                raise ArchiveError("only POSIX ustar or basic tar headers are supported")
            name = member.name.rstrip("/") if member.isdir() else member.name
            relative = safe_member_name(name, limits)
            if name in paths:
                raise ArchiveError("duplicate archive member")
            paths.add(name)
            for depth in range(1, len(relative.parts) + 1):
                nodes.add(relative.parts[:depth])
                if len(nodes) > limits.max_members:
                    raise ArchiveError("archive filesystem node limit exceeded")
            if member.size < 0 or member.size > limits.max_file_bytes:
                raise ArchiveError("archive file limit exceeded")
            if member.isdir() and member.size:
                raise ArchiveError("directory with payload forbidden")
            total += member.size
            if total > limits.max_expanded_bytes:
                raise ArchiveError("expanded byte limit exceeded")
            target = staged.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if member.isdir():
                target.mkdir(exist_ok=True, mode=0o700)
            else:
                with open(target, "xb") as output:
                    remaining = member.size
                    while remaining:
                        chunk = _exact(stream, min(remaining, 1024 * 1024))
                        output.write(chunk)
                        remaining -= len(chunk)
                    output.flush()
                    os.fchmod(output.fileno(), 0o444)
                    os.fsync(output.fileno())
                padding = (-member.size) % 512
                if padding and any(_exact(stream, padding)):
                    raise ArchiveError("nonzero tar padding")
        for directory, _, _ in os.walk(staged, topdown=False):
            fsync_directory(directory)
    finally:
        if decoded is not source:
            decoded.close()


def extract(archive, destination, digest, size, *, limits=DEFAULT_LIMITS):
    """Verify a private snapshot, extract data only, publish a NEW directory."""
    destination = Path(os.path.abspath(destination))
    private_directory(destination.parent)
    if os.path.lexists(destination):
        raise ArchiveError("extraction destination already exists")
    staged = Path(tempfile.mkdtemp(prefix=".extract-", dir=destination.parent))
    try:
        # Snapshot before parsing closes the verification/extraction TOCTOU.
        with tempfile.TemporaryFile(dir=destination.parent) as snapshot, open_regular(archive) as source:
            copy_verified(source, snapshot, digest, size, limits)
            snapshot.flush()
            os.fsync(snapshot.fileno())
            snapshot.seek(0)
            _extract_tar(snapshot, staged, size, limits)
        publish_directory(staged, destination)
        return destination
    except (tarfile.TarError, UnicodeError, EOFError) as exc:
        raise ArchiveError("invalid tar/gzip archive") from exc
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def install(cache, destination, digest, size, mirrors=(), *, offline=False,
            allow_file=False, limits=DEFAULT_LIMITS):
    """Data-only offline/online installation; no hooks, subprocesses or trust writes."""
    cached = fetch(cache, digest, size, mirrors, offline=offline,
                   allow_file=allow_file, limits=limits)
    return extract(cached, destination, digest, size, limits=limits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "install"))
    parser.add_argument("--cache", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--size", required=True, type=int)
    parser.add_argument("--mirror", action="append", default=[])
    parser.add_argument("--offline", action="store_true", help="cache only, zero network/media reads")
    parser.add_argument("--offline-file", action="store_true", help="explicit file:// import, no HTTPS")
    parser.add_argument("--destination")
    args = parser.parse_args()
    if args.command == "install" and not args.destination:
        parser.error("install requires --destination")
    if args.offline and args.offline_file:
        parser.error("choose cache-only --offline or media-import --offline-file")
    try:
        options = dict(offline=args.offline, allow_file=args.offline_file)
        if args.command == "install":
            result = install(args.cache, args.destination, args.digest, args.size, args.mirror, **options)
        else:
            result = fetch(args.cache, args.digest, args.size, args.mirror, **options)
        print(result)
    except (OSError, ValueError) as exc:
        parser.exit(1, "release archive: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
