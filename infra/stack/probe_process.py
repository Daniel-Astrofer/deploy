"""Bounded POSIX probe transport; provides no capability or release qualification."""

import math
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Sequence


MAX_STDOUT = 4096
MAX_STDERR = 4096
MAX_TIMEOUT = 30


class ProbeProcessError(RuntimeError):
    """Generic failure that never includes argv or subprocess output."""


def run_probe(argv: Sequence[str], *, timeout: float = MAX_TIMEOUT) -> bytes:
    """Run controller-owned fixed argv without a shell and return raw stdout.

    The controller must choose/authorize the executable and arguments. This
    helper does not authorize commands, parse capabilities, or open any gates.
    Limits are bytes; stderr is counted and discarded, never returned. A new
    POSIX session lets failure cleanup kill descendants retaining pipe handles.
    """
    if (isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence)
            or not argv or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
            or not argv[0]):
        raise ProbeProcessError("invalid probe arguments")
    if (type(timeout) not in (int, float) or not 0 < timeout <= MAX_TIMEOUT
            or not math.isfinite(timeout)):
        raise ProbeProcessError("invalid probe timeout")

    deadline = time.monotonic() + timeout
    try:
        child = subprocess.Popen(tuple(argv), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 start_new_session=True, bufsize=0)
    except (OSError, ValueError):
        raise ProbeProcessError("probe could not start") from None

    stdout = bytearray()
    stderr_size = 0
    succeeded = False
    try:
        with selectors.DefaultSelector() as selector:
            for stream in (child.stdout, child.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProbeProcessError("probe timed out")
                for key, _ in selector.select(remaining):
                    stream = key.fileobj
                    is_stdout = stream is child.stdout
                    size = len(stdout) if is_stdout else stderr_size
                    limit = MAX_STDOUT if is_stdout else MAX_STDERR
                    try:
                        data = os.read(stream.fileno(), min(4096, limit - size + 1))
                    except BlockingIOError:
                        continue
                    if not data:
                        selector.unregister(stream)
                        continue
                    if size + len(data) > limit:
                        raise ProbeProcessError("probe output limit exceeded")
                    if is_stdout:
                        stdout.extend(data)
                    else:
                        stderr_size += len(data)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeProcessError("probe timed out")
        if child.wait(timeout=remaining) != 0 or stderr_size:
            raise ProbeProcessError("probe failed")
        succeeded = True
        return bytes(stdout)
    except subprocess.TimeoutExpired:
        raise ProbeProcessError("probe timed out") from None
    except OSError:
        raise ProbeProcessError("probe collection failed") from None
    finally:
        # Also runs on interruption; never wait for pipe EOF during cleanup.
        if not succeeded:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait()
        child.stdout.close()
        child.stderr.close()
