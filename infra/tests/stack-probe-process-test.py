#!/usr/bin/env python3
"""Offline tests using real synthetic subprocesses only."""

from pathlib import Path
import os
import subprocess
import sys
import time
import traceback
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stack"))
import probe_process


class ProbeProcessTest(unittest.TestCase):
    def run_code(self, code, **kwargs):
        return probe_process.run_probe([sys.executable, "-c", code], **kwargs)

    def assert_failure(self, code, message, **kwargs):
        children = []
        real_popen = subprocess.Popen

        def spawn(*args, **options):
            child = real_popen(*args, **options)
            children.append(child)
            return child

        started = time.monotonic()
        with patch.object(probe_process.subprocess, "Popen", side_effect=spawn):
            with self.assertRaisesRegex(probe_process.ProbeProcessError, message) as caught:
                self.run_code(code, timeout=kwargs.get("timeout", 2))
        self.assertLess(time.monotonic() - started, 5)
        self.assertNotIn("PRIVATE", "".join(traceback.format_exception(caught.exception)))
        for child in children:
            self.assertIsNotNone(child.returncode)
            self.assertTrue(child.stdout.closed)
            self.assertTrue(child.stderr.closed)
            with self.assertRaises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)

    def test_happy_and_no_final_newline(self):
        for output in (b"", b'{"synthetic":true}\n', b"no newline", b"\xff\x00"):
            with self.subTest(output=output):
                self.assertEqual(self.run_code(f"import os; os.write(1, {output!r})"), output)

    def test_exact_stdout_limit(self):
        self.assertEqual(self.run_code("import os; os.write(1, b'x' * 4096)"), b"x" * 4096)

    def test_oversized_stdout(self):
        self.assert_failure("import os,time; os.write(1,b'PRIVATE'*10000); time.sleep(60)",
                            "output limit exceeded")

    def test_one_byte_over_each_limit(self):
        for fd in (1, 2):
            with self.subTest(fd=fd):
                self.assert_failure(f"import os; os.write({fd},b'x'*4097)",
                                    "output limit exceeded")

    def test_oversized_stderr(self):
        self.assert_failure("import os,time; os.write(2,b'PRIVATE'*10000); time.sleep(60)",
                            "output limit exceeded")

    def test_any_stderr_rejected(self):
        self.assert_failure("import os; os.write(2,b'PRIVATE')", "probe failed")

    def test_both_streams_are_drained(self):
        self.assert_failure("import os; os.write(2,b'x'*4096); os.write(1,b'y'*4096)",
                            "probe failed")

    def test_concurrent_stream_flood(self):
        self.assert_failure(
            "import os,threading; "
            "t=threading.Thread(target=lambda: os.write(2,b'PRIVATE'*100000)); "
            "t.start(); os.write(1,b'PRIVATE'*100000); t.join()",
            "output limit exceeded")

    def test_stdin_is_closed_and_arguments_are_literal(self):
        argument = "$(PRIVATE); echo PRIVATE"
        self.assertEqual(probe_process.run_probe([
            sys.executable, "-c",
            "import sys; assert sys.stdin.buffer.read() == b''; "
            "sys.stdout.buffer.write(sys.argv[1].encode())", argument]), argument.encode())

    def test_nonzero(self):
        self.assert_failure("import os; os.write(1,b'PRIVATE'); raise SystemExit(7)", "probe failed")

    def test_timeout(self):
        self.assert_failure("import time; time.sleep(60)", "timed out", timeout=0.1)

    def test_timeout_after_pipe_eof(self):
        self.assert_failure("import os,time; os.close(1); os.close(2); time.sleep(60)",
                            "timed out", timeout=0.1)

    def test_descendant_retaining_pipes(self):
        self.assert_failure("import os,time; pid=os.fork(); time.sleep(60) if pid == 0 else None",
                            "timed out", timeout=0.1)

    def test_missing_executable_is_generic(self):
        with self.assertRaises(probe_process.ProbeProcessError) as caught:
            probe_process.run_probe(["/nonexistent/PRIVATE/probe"])
        self.assertEqual(str(caught.exception), "probe could not start")
        self.assertNotIn("PRIVATE", "".join(traceback.format_exception(caught.exception)))

    def test_invalid_inputs_do_not_spawn(self):
        with patch.object(probe_process.subprocess, "Popen", side_effect=AssertionError("spawned")):
            for argv in ([], "echo PRIVATE", [""], [1], ["x\0y"]):
                with self.subTest(argv=argv), self.assertRaises(probe_process.ProbeProcessError):
                    probe_process.run_probe(argv)
            for timeout in (0, -1, 30.01, 10**1000, float("nan"), float("inf"), True, "1"):
                with self.subTest(timeout=timeout), self.assertRaises(probe_process.ProbeProcessError):
                    probe_process.run_probe([sys.executable], timeout=timeout)


if __name__ == "__main__":
    unittest.main()
