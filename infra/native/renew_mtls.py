#!/usr/bin/env python3
"""Renew native-local Vault/Node leaf certificates without replacing their keys.

The three members are restarted one at a time. The Kubernetes Secret and its
operator source are updated only after the member certificates are verified.
This procedure is specific to the native-local installation.
"""

from __future__ import annotations

import argparse
import base64
import copy
import fcntl
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import yaml

RENEW_BEFORE_SECONDS = 8 * 60 * 60
CERT_LIFETIME_SECONDS = 24 * 60 * 60
KUBE_CONTEXT = "kind-kerosene-local"
NAMESPACE = "kerosene-staging"
SECRET_NAME = "vault-client-mtls"
GITOPS_IDENTITY = "system:serviceaccount:kerosene-gitops:kerosene-deployer"


class RenewalError(RuntimeError):
    """A precondition or a renewal operation failed."""


def run(args: list[str], *, data: bytes | None = None, timeout: int = 60) -> bytes:
    try:
        result = subprocess.run(args, input=data, capture_output=True, check=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        # Never print stdin: kubectl and openssl can receive private material.
        if args[0] == "kubectl":
            operation = next((part for part in ("get", "replace", "rollout") if part in args), "request")
            if operation == "rollout":
                operation += " " + args[args.index("rollout") + 1]
            label = f"kubectl {operation}"
        else:
            label = f"{args[0]} {args[1]}"
        raise RenewalError(f"command failed: {label}") from exc
    return result.stdout


def atomic_write(path: Path, content: bytes) -> None:
    mode = path.stat().st_mode & 0o777
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def cert_details(cert: Path) -> bytes:
    return run(["openssl", "x509", "-in", str(cert), "-noout", "-subject", "-ext",
                "subjectAltName,keyUsage,extendedKeyUsage,basicConstraints"])


def key_matches(cert: Path, key: Path) -> bool:
    public_cert = run(["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"])
    public_key = run(["openssl", "pkey", "-in", str(key), "-pubout"])
    return public_cert == public_key


def verify_chain_strict(cert: Path, ca: Path) -> None:
    try:
        run(["openssl", "verify", "-x509_strict", "-no_check_time", "-CAfile", str(ca), str(cert)])
    except RenewalError as exc:
        raise RenewalError("strict certificate-chain validation failed; inspect the ceremony CA and leaf extensions") from exc


def is_fresh(cert: Path, seconds: int) -> bool:
    result = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-checkend", str(seconds)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return result.returncode == 0


def issue_leaf(cert: Path, key: Path, ca: Path, ca_key: Path, output: Path, purpose: str) -> None:
    if not key_matches(cert, key):
        raise RenewalError(f"certificate and key do not match: {cert}")
    run(["openssl", "verify", "-no_check_time", "-CAfile", str(ca), str(cert)])
    csr = output.with_suffix(".csr")
    run(["openssl", "x509", "-in", str(cert), "-x509toreq", "-signkey", str(key),
         "-copy_extensions", "copyall", "-out", str(csr)])
    run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(ca_key),
         "-copy_extensions", "copyall", "-sha256", "-days", "1", "-out", str(output)])
    run(["openssl", "verify", "-x509_strict", "-purpose", purpose, "-CAfile", str(ca), str(output)])
    if not is_fresh(output, RENEW_BEFORE_SECONDS) or not key_matches(output, key):
        raise RenewalError(f"new certificate failed validation: {cert}")
    if cert_details(output) != cert_details(cert):
        raise RenewalError(f"certificate identity or extensions changed: {cert}")


def source_update(original: str, old_cert: str, new_cert: str) -> tuple[str, dict]:
    documents = list(yaml.safe_load_all(original))
    matches = [i for i, doc in enumerate(documents) if isinstance(doc, dict)
               and doc.get("metadata", {}).get("name") == SECRET_NAME
               and doc.get("metadata", {}).get("namespace") == NAMESPACE]
    if len(matches) != 1:
        raise RenewalError("operator Secret source is missing or ambiguous")
    index = matches[0]
    if documents[index].get("stringData", {}).get("client.crt") != old_cert:
        raise RenewalError("operator Secret source differs from current KFE certificate")

    def mapping(node):
        return {key.value: value for key, value in node.value}

    root = list(yaml.compose_all(original))[index]
    scalar = mapping(mapping(root)["stringData"])["client.crt"]
    updated = original[:scalar.start_mark.index] + json.dumps(new_cert) + original[scalar.end_mark.index:]
    expected = copy.deepcopy(documents)
    expected[index]["stringData"]["client.crt"] = new_cert
    if list(yaml.safe_load_all(updated)) != expected:
        raise RenewalError("operator Secret source update changed other data")
    return updated, expected[index]


def kube_secret() -> dict:
    output = run(["kubectl", "--context", KUBE_CONTEXT, "get", "secret", "-n", NAMESPACE,
                  SECRET_NAME, "-o", "json"])
    return json.loads(output)


def verify_secret(secret: dict, ca: Path, cert: Path, key: Path) -> None:
    expected = {"ca.crt": ca, "client.crt": cert, "client.key": key}
    if secret.get("immutable") or set(secret.get("data", {})) != set(expected):
        raise RenewalError("unexpected or immutable KFE Vault Secret")
    for name, path in expected.items():
        if base64.b64decode(secret["data"][name], validate=True) != path.read_bytes():
            raise RenewalError(f"KFE Secret differs from local {name}")


def verify_source_secret(secret: dict, ca: Path, cert: Path, key: Path) -> None:
    expected = {"ca.crt": ca, "client.crt": cert, "client.key": key}
    values = secret.get("stringData", {})
    if set(values) != set(expected) or any(values[name].encode() != path.read_bytes()
                                           for name, path in expected.items()):
        raise RenewalError("operator Secret source differs from local mTLS files")


def get_json(url: str, context: ssl.SSLContext, timeout: int = 40) -> dict:
    try:
        with urlopen(url, context=context, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 503:
            return json.load(exc)
        raise RenewalError(f"health request returned HTTP {exc.code}") from exc
    except (URLError, OSError, ValueError) as exc:
        raise RenewalError("authenticated local health request failed") from exc


def member_health(index: int, context: ssl.SSLContext) -> bool:
    node = get_json(f"https://127.0.0.1:880{index}/ready-quorum", context)
    vault = get_json(f"https://127.0.0.1:780{index}/v1/health", context)
    return (node.get("local_ready") is True and node.get("quorum_ready") is True
            and node.get("live_members", 0) >= 3 and vault.get("local_ready") is True
            and vault.get("financial_ready") is True)


def wait_member(index: int, context: ssl.SSLContext) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if member_health(index, context):
                return
        except RenewalError:
            pass
        time.sleep(3)
    raise RenewalError(f"member {index} did not regain authenticated quorum health")


def wait_vault(index: int, context: ssl.SSLContext) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if get_json(f"https://127.0.0.1:780{index}/v1/live", context).get("live") is True:
                return
        except RenewalError:
            pass
        time.sleep(3)
    raise RenewalError(f"Vault {index} did not regain authenticated liveness")


def rollout_kfe(marker: Path) -> None:
    run(["kubectl", "--context", KUBE_CONTEXT, f"--as={GITOPS_IDENTITY}",
         "rollout", "restart", "deployment/kfe-service", "-n", NAMESPACE])
    run(["kubectl", "--context", KUBE_CONTEXT, "rollout", "status", "deployment/kfe-service", "-n", NAMESPACE,
         "--timeout=8m"], timeout=510)
    marker.unlink()


def recover_pending(state: Path, ops: Path, ca: Path, client_cert: Path, client_key: Path) -> None:
    """Recover a partially published KFE credential before considering another rotation."""
    marker = state / "run/native-mtls-kfe-rollout.pending"
    if not marker.exists():
        return
    backup_root = (state / "cert-backups").resolve()
    backup = Path(json.loads(marker.read_text())["backup"]).resolve()
    if backup.parent != backup_root or not backup.name.startswith("native-mtls-"):
        raise RenewalError("invalid pending rotation backup")
    old_cert, new_cert = backup / "kfe/client.crt", backup / "new-kfe-client.crt"
    source = ops / "native-application-secrets.yaml"
    original_source = (backup / "native-application-secrets.yaml").read_text()
    updated_source, _ = source_update(original_source, old_cert.read_text(), new_cert.read_text())
    if source.read_text() not in (original_source, updated_source):
        raise RenewalError("operator Secret source changed after the interrupted rotation")
    if client_cert.read_bytes() not in (old_cert.read_bytes(), new_cert.read_bytes()):
        raise RenewalError("KFE certificate changed after the interrupted rotation")
    observed = kube_secret()
    try:
        verify_secret(observed, ca, old_cert, client_key)
    except RenewalError:
        verify_secret(observed, ca, new_cert, client_key)
        atomic_write(source, updated_source.encode())
        atomic_write(client_cert, new_cert.read_bytes())
        print("Finishing a previously interrupted KFE certificate rollout.", flush=True)
        rollout_kfe(marker)
    else:
        atomic_write(source, original_source.encode())
        atomic_write(client_cert, old_cert.read_bytes())
        marker.unlink()


def verify_runtime(state: Path) -> None:
    for index in (1, 2, 3):
        expected_dir = str(state / "ceremony-certs" / "nodes" / f"vault-{index}")
        identity = str(state / "node" / f"member-{index}" / "client-identity.pem")
        for kind in ("node", "vault"):
            name = f"kerosene-native-{kind}-{index}"
            info = json.loads(run(["docker", "inspect", name]))[0]
            mounts = {mount["Source"] for mount in info["Mounts"] if mount["Type"] == "bind"}
            env = dict(item.split("=", 1) for item in info["Config"].get("Env", []) if "=" in item)
            prefix = "KEROSENE" if kind == "node" else "VAULT"
            if (not info["State"]["Running"] or expected_dir not in mounts
                    or env.get(f"{prefix}_TLS_CERT_PATH") != f"{expected_dir}/server.crt"
                    or env.get(f"{prefix}_TLS_KEY_PATH") != f"{expected_dir}/server.key"
                    or (kind == "node" and env.get("KEROSENE_TLS_CLIENT_IDENTITY_PEM") != identity)
                    or (kind == "vault" and env.get("VAULT_TLS_CLIENT_CERT_PATH") != f"{expected_dir}/client.crt")
                    or (kind == "vault" and env.get("VAULT_TLS_CLIENT_KEY_PATH") != f"{expected_dir}/client.key")):
                raise RenewalError(f"unexpected runtime configuration for {name}")


def renew(state: Path, ops: Path, force: bool) -> None:
    certs = state / "ceremony-certs"
    ca, ca_key = certs / "ca.crt", certs / "ca.key"
    source = ops / "native-application-secrets.yaml"
    client_cert, client_key = certs / "kfe/client.crt", certs / "kfe/client.key"
    leaves = [(certs / f"nodes/vault-{i}/{kind}.crt", certs / f"nodes/vault-{i}/{kind}.key",
               "sslserver" if kind == "server" else "sslclient")
              for i in (1, 2, 3) for kind in ("server", "client")]
    leaves.append((client_cert, client_key, "sslclient"))
    if not is_fresh(ca, CERT_LIFETIME_SECONDS + RENEW_BEFORE_SECONDS):
        raise RenewalError("ceremony CA is expired or too close to expiry")
    recover_pending(state, ops, ca, client_cert, client_key)
    secret = kube_secret()
    verify_secret(secret, ca, client_cert, client_key)
    source_text = source.read_text()
    _, current_source_secret = source_update(source_text, client_cert.read_text(), client_cert.read_text())
    verify_source_secret(current_source_secret, ca, client_cert, client_key)
    if not key_matches(ca, ca_key):
        raise RenewalError("ceremony CA certificate and key do not match")
    for cert, key, _ in leaves:
        if not key_matches(cert, key):
            raise RenewalError(f"certificate and key do not match: {cert}")
        verify_chain_strict(cert, ca)
    for index in (1, 2, 3):
        identity = state / f"node/member-{index}/client-identity.pem"
        if not key_matches(identity, certs / f"nodes/vault-{index}/client.key"):
            raise RenewalError(f"Node {index} identity does not match its client key")
    identities_current = all(
        run(["openssl", "x509", "-in", str(state / f"node/member-{index}/client-identity.pem"), "-outform", "DER"])
        == run(["openssl", "x509", "-in", str(certs / f"nodes/vault-{index}/client.crt"), "-outform", "DER"])
        for index in (1, 2, 3))
    if not force and identities_current and all(is_fresh(cert, RENEW_BEFORE_SECONDS) for cert, _, _ in leaves):
        print("Native mTLS certificates are valid beyond the renewal window.")
        return

    verify_runtime(state)
    tls = ssl.create_default_context(cafile=str(ca))
    tls.load_cert_chain(str(client_cert), str(client_key))
    if not all(member_health(index, tls) for index in (1, 2, 3)):
        raise RenewalError("member health/quorum precondition failed")
    deployment = json.loads(run(["kubectl", "--context", KUBE_CONTEXT, "get", "deployment",
                                 "-n", NAMESPACE, "kfe-service", "-o", "json"]))
    if deployment.get("status", {}).get("availableReplicas", 0) < 1:
        raise RenewalError("KFE is not available; refusing automatic rotation")
    pod_spec = deployment["spec"]["template"]["spec"]
    secret_volumes = {volume["name"] for volume in pod_spec.get("volumes", [])
                      if volume.get("secret", {}).get("secretName") == SECRET_NAME}
    if not any(mount.get("name") in secret_volumes and mount.get("mountPath") == "/etc/vault-mtls"
               for container in pod_spec.get("containers", []) if container.get("name") == "kfe-service"
               for mount in container.get("volumeMounts", [])):
        raise RenewalError("KFE deployment no longer mounts the expected Vault Secret")

    backup_root = state / "cert-backups"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="native-mtls-", dir=backup_root))
    for cert, _, _ in leaves:
        destination = backup / cert.relative_to(certs)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(cert, destination)
    for index in (1, 2, 3):
        shutil.copy2(state / f"node/member-{index}/client-identity.pem", backup / f"node-{index}-identity.pem")
    shutil.copy2(source, backup / "native-application-secrets.yaml")
    atomic = json.dumps(secret).encode()
    fd = os.open(backup / "vault-client-mtls-secret.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(atomic)

    with tempfile.TemporaryDirectory(prefix="native-mtls-", dir=state / "run") as stage_name:
        stage = Path(stage_name)
        outputs = []
        for number, (cert, key, purpose) in enumerate(leaves):
            output = stage / f"leaf-{number}.crt"
            issue_leaf(cert, key, ca, ca_key, output, purpose)
            outputs.append(output)
        updated_source, source_secret = source_update(source_text, client_cert.read_text(), outputs[-1].read_text())
        for index in (1, 2, 3):
            if not all(member_health(peer, tls) for peer in (1, 2, 3)):
                raise RenewalError("all members must be healthy before each restart")
            for kind, output in zip(("server", "client"), outputs[(index - 1) * 2:index * 2]):
                atomic_write(certs / f"nodes/vault-{index}/{kind}.crt", output.read_bytes())
            identity = state / f"node/member-{index}/client-identity.pem"
            identity_bytes = outputs[(index - 1) * 2 + 1].read_bytes() + (certs / f"nodes/vault-{index}/client.key").read_bytes()
            atomic_write(identity, identity_bytes)
            run(["docker", "restart", f"kerosene-native-vault-{index}"], timeout=45)
            wait_vault(index, tls)
            run(["docker", "restart", f"kerosene-native-node-{index}"], timeout=45)
            wait_member(index, tls)
            for peer in (1, 2, 3):
                if peer != index and not member_health(peer, tls):
                    raise RenewalError(f"member {peer} lost quorum health during rotation")
            print(f"Renewed member {index}; authenticated quorum retained.", flush=True)

        # Recheck Secret resourceVersion and data before writing any KFE material.
        current = kube_secret()
        verify_secret(current, ca, client_cert, client_key)
        if current["metadata"]["resourceVersion"] != secret["metadata"]["resourceVersion"]:
            raise RenewalError("KFE Secret changed during rotation")
        if source.read_text() != source_text:
            raise RenewalError("operator Secret source changed during rotation")
        replacement = copy.deepcopy(current)
        replacement["data"]["client.crt"] = base64.b64encode(outputs[-1].read_bytes()).decode()
        replacement["metadata"].setdefault("annotations", {})["kubectl.kubernetes.io/last-applied-configuration"] = json.dumps(source_secret, separators=(",", ":"))
        shutil.copy2(outputs[-1], backup / "new-kfe-client.crt")
        marker = state / "run/native-mtls-kfe-rollout.pending"
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as pending:
            json.dump({"backup": str(backup)}, pending)
            pending.flush()
            os.fsync(pending.fileno())
        atomic_write(source, updated_source.encode())
        atomic_write(client_cert, outputs[-1].read_bytes())
        try:
            # replace uses the retrieved resourceVersion as an optimistic concurrency check.
            run(["kubectl", "--context", KUBE_CONTEXT, "replace", "-f", "-"],
                data=json.dumps(replacement).encode())
        except RenewalError:
            # Do not guess whether a timed-out replace reached the server.
            recover_pending(state, ops, ca, client_cert, client_key)
            if client_cert.read_bytes() == outputs[-1].read_bytes():
                print(f"Native mTLS rotation recovered after uncertain Secret update; backup: {backup}", flush=True)
                return
            raise
        verify_secret(kube_secret(), ca, client_cert, client_key)
        rollout_kfe(marker)
    print(f"Native mTLS rotation completed; backup: {backup}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="rotate even if certificates are still fresh")
    args = parser.parse_args()
    os.umask(0o077)
    state = Path.home() / ".local/state/kerosene/production-cells"
    ops = Path.home() / ".config/kerosene/native-recovery"
    lock = state / "run/certificate-rotation.lock"
    try:
        with lock.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            renew(state, ops, args.force)
    except (RenewalError, OSError, ValueError, KeyError, yaml.YAMLError, json.JSONDecodeError) as exc:
        print(f"Native mTLS renewal failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
