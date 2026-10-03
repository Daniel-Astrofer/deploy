#!/usr/bin/env python3
"""Opt-in PostgreSQL 17 mutual-TLS lab; retains all invocation-owned fixtures.

Run with CELL_REGISTRY_LOCAL_TLS_LAB=true. No existing cluster is touched.
The private retained directory contains synthetic keys, data and diagnostic logs.
"""

import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile


PG_BIN = Path("/usr/lib/postgresql/17/bin")
WORKSPACE = Path("/home/astrofer/workspaces")
HARNESS = Path(__file__).with_name("cell-admission-registry-postgres-test.py")


class LabFailure(Exception):
    """A bounded lab operation failed; details stay in the private logs."""


def run(command, env, log, label, timeout=30):
    with log.open("ab") as output:
        try:
            result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT,
                                    timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise LabFailure(f"{label} timed out; inspect retained logs") from None
    if result.returncode:
        raise LabFailure(f"{label} failed; inspect retained logs")


def certificate(root, name, subject, extensions, env):
    log = root / "fixture.log"
    key = root / f"{name}.key"
    csr = root / f"{name}.csr"
    cert = root / f"{name}.crt"
    ext = root / f"{name}.ext"
    ext.write_text(extensions, encoding="ascii")
    run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(csr), "-subj", subject],
        env, log, f"synthetic {name} request")
    key.chmod(0o600)
    run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(root / "ca.crt"),
         "-CAkey", str(root / "ca.key"), "-set_serial", "2" if name == "server" else "3",
         "-days", "2", "-sha256", "-extfile", str(ext), "-out", str(cert)],
        env, log, f"synthetic {name} certificate")


def interrupted(signum, frame):
    raise LabFailure("lab interrupted")


def main():
    if os.environ.get("CELL_REGISTRY_LOCAL_TLS_LAB") != "true":
        print("Lab not started: set CELL_REGISTRY_LOCAL_TLS_LAB=true to opt in.", file=sys.stderr)
        return 2
    if os.geteuid() == 0:
        print("Lab requires an unprivileged user.", file=sys.stderr)
        return 2
    if not all(os.access(PG_BIN / name, os.X_OK) for name in ("initdb", "pg_ctl", "postgres")):
        print("PostgreSQL 17 binaries are required in /usr/lib/postgresql/17/bin.", file=sys.stderr)
        return 2
    if not shutil.which("openssl") or not HARNESS.is_file():
        print("openssl and the adjacent PostgreSQL test harness are required.", file=sys.stderr)
        return 2

    previous_umask = os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix="cell-registry-tls-lab-",
                                 dir=str(WORKSPACE) if WORKSPACE.is_dir() else None)).resolve()
    data = root / "data"
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PG", "CELL_DATABASE_", "CELL_REGISTRY_TLS_",
                                  "CELL_ADMISSION_REGISTRY_"))}
    env["PATH"] = str(PG_BIN) + os.pathsep + env.get("PATH", os.defpath)
    old_sigterm = signal.signal(signal.SIGTERM, interrupted)
    attempted_start = False
    exit_code = 1
    print(f"Retained private synthetic TLS lab: {root}", flush=True)
    try:
        run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
             "-days", "2", "-keyout", str(root / "ca.key"), "-out", str(root / "ca.crt"),
             "-subj", "/CN=synthetic-registry-lab-ca",
             "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign"],
            env, root / "fixture.log", "synthetic CA generation")
        (root / "ca.key").chmod(0o600)
        certificate(root, "server", "/CN=localhost",
                    "basicConstraints=critical,CA:FALSE\n"
                    "keyUsage=critical,digitalSignature,keyEncipherment\n"
                    "extendedKeyUsage=serverAuth\n"
                    "subjectAltName=DNS:localhost,IP:127.0.0.1\n", env)
        certificate(root, "client", "/CN=synthetic-registry-client",
                    "basicConstraints=critical,CA:FALSE\n"
                    "keyUsage=critical,digitalSignature,keyEncipherment\n"
                    "extendedKeyUsage=clientAuth\n", env)
        run([str(PG_BIN / "initdb"), "-D", str(data), "-U", "postgres",
             "--auth-local=trust", "--auth-host=scram-sha-256", "--no-locale", "--encoding=UTF8"],
            env, root / "fixture.log", "PostgreSQL initialization", timeout=60)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        # The free-port selection has a small race; pg_ctl fails safely if it is taken.
        (data / "postgresql.conf").write_text(
            "listen_addresses = '127.0.0.1'\n"
            f"port = {port}\n"
            f"unix_socket_directories = '{root}'\n"
            "ssl = on\n"
            f"ssl_ca_file = '{root / 'ca.crt'}'\n"
            f"ssl_cert_file = '{root / 'server.crt'}'\n"
            f"ssl_key_file = '{root / 'server.key'}'\n"
            "password_encryption = 'scram-sha-256'\n"
            "statement_timeout = '10s'\n", encoding="ascii")
        (data / "pg_hba.conf").write_text(
            "local all all trust\n"
            "hostnossl all all 127.0.0.1/32 reject\n"
            "hostssl all postgres 127.0.0.1/32 trust clientcert=verify-ca\n"
            "hostssl all all 127.0.0.1/32 scram-sha-256 clientcert=verify-ca\n",
            encoding="ascii")
        attempted_start = True
        run([str(PG_BIN / "pg_ctl"), "-D", str(data), "-l", str(root / "postgres.log"),
             "-w", "-t", "30", "start"],
            env, root / "fixture.log", "PostgreSQL TLS startup", timeout=45)
        env.update(CELL_DATABASE_DISPOSABLE="true", CELL_DATABASE_PGHOST="127.0.0.1",
                   CELL_DATABASE_PGPORT=str(port), CELL_DATABASE_ADMIN_USER="postgres",
                   CELL_DATABASE_ADMIN_PASSWORD="cell-test-only",
                   CELL_REGISTRY_TLS_CA=str(root / "ca.crt"),
                   CELL_REGISTRY_TLS_CERT=str(root / "client.crt"),
                   CELL_REGISTRY_TLS_KEY=str(root / "client.key"))
        run([sys.executable, str(HARNESS)], env, root / "harness.log",
            "registry TLS harness", timeout=300)
        print("Registry TLS harness passed.", flush=True)
        exit_code = 0
    except (LabFailure, KeyboardInterrupt):
        print("TLS lab failed or was interrupted; inspect retained private logs.", file=sys.stderr)
    except (OSError, ValueError):
        print("TLS lab setup failed; fixtures and logs are retained.", file=sys.stderr)
    finally:
        # Only this invocation's data directory is passed to pg_ctl; never search or kill clusters.
        if attempted_start:
            try:
                run([str(PG_BIN / "pg_ctl"), "-D", str(data), "-m", "fast",
                     "-w", "-t", "30", "stop"],
                    env, root / "fixture.log", "owned PostgreSQL shutdown", timeout=45)
            except (LabFailure, OSError):
                print("Owned PostgreSQL shutdown failed; inspect retained logs.", file=sys.stderr)
                exit_code = 1
        signal.signal(signal.SIGTERM, old_sigterm)
        os.umask(previous_umask)
        print(f"Fixtures, database and logs retained at: {root}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
