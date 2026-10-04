#!/usr/bin/env python3
"""Opt-in live qualification of the production-only three-Vault Tor protocol.

All authority-like material is fresh, disposable laboratory input held in a
temporary directory/Kubernetes namespace. Nothing generated here is reusable
as release, settlement, or production authority.
"""

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time


NAMESPACE = "kerosene-vault-protocol-qualification"
MEMBERS = ("vault-1", "vault-2", "vault-3")


def run(argv, *, data=None, env=None, timeout=120, check=True):
    result = subprocess.run(argv, input=data, capture_output=True, env=env,
                            timeout=timeout, check=False)
    if check and result.returncode:
        raise RuntimeError("live qualification command failed: " + " ".join(argv[:5]))
    return result


def kubectl(prefix, args, *, document=None, timeout=120):
    payload = None if document is None else json.dumps(document, separators=(",", ":")).encode()
    return run(prefix + args, data=payload, timeout=timeout)


def apply(prefix, document):
    kubectl(prefix, ["apply", "-f", "-"], document=document)


def available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for(prefix, member, command, deadline=180):
    stop = time.monotonic() + deadline
    while time.monotonic() < stop:
        result = run(prefix + ["-n", NAMESPACE, "exec", "deployment/" + member,
                               "-c", "tor", "--", "sh", "-c", command],
                     timeout=15, check=False)
        if result.returncode == 0:
            return result.stdout.decode().strip()
        time.sleep(2)
    raise RuntimeError("timed out waiting for " + member)


def secret_document(name, values):
    return {"apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": name, "namespace": NAMESPACE}, "type": "Opaque",
            "data": {key: base64.b64encode(value if isinstance(value, bytes) else value.encode()).decode()
                     for key, value in values.items()}}


def deployment(member, image, peers=None, cert_secret=None, runtime_secret=None):
    labels = {"app": member, "qualification": "kerosene-vault-protocol"}
    tor_start = ("rm -f /tmp/tor-ready /tmp/tor.log; tor -f /etc/tor/torrc >/tmp/tor.log 2>&1 & pid=$!; "
                 "while ! grep -q 'Bootstrapped 100% (done): Done' /tmp/tor.log; do "
                 "kill -0 $pid || exit 1; sleep 1; done; touch /tmp/tor-ready; "
                 "tail -n +1 -F /tmp/tor.log & wait $pid")
    tor = {"name": "tor", "image": os.environ.get("KEROSENE_TOR_TEST_IMAGE", "kerosene/tor:staging"),
           "imagePullPolicy": "IfNotPresent", "command": ["sh", "-c", tor_start],
           "ports": [{"name": "socks", "containerPort": 9050}],
           "volumeMounts": [{"name": "tor-config", "mountPath": "/etc/tor/torrc", "subPath": "torrc", "readOnly": True},
                            {"name": "tor-data", "mountPath": "/var/lib/tor"}]}
    pod = {"automountServiceAccountToken": False, "containers": [tor],
           "volumes": [{"name": "tor-config", "configMap": {"name": "tor-config"}},
                       {"name": "tor-data", "persistentVolumeClaim": {"claimName": member + "-tor"}}]}
    if peers is not None:
        values = {"KEROSENE_ENV": "production", "VAULT_CEREMONY_MODE": "production",
                  "VAULT_NODE_ID": member, "VAULT_NODE_TIER": "domestic", "ATTESTATION_MODE": "software",
                  "VAULT_MEASUREMENT_PIN": "a" * 64, "VAULT_LISTEN_ADDR": "127.0.0.1:7801",
                  "VAULT_DATA_DIR": "/var/lib/kerosene-vault", "VAULT_AUTH_MODE": "mtls",
                  "VAULT_TRANSPORT": "tor", "VAULT_SOCKS_PROXY": "socks5h://127.0.0.1:9050",
                  "VAULT_GENESIS_N": "3", "VAULT_DKG_MODE": "distributed_wire",
                  "VAULT_SHARE_STORE": "aead_disk", "VAULT_TLS_VERIFY_MODE": "onion_or_spiffe",
                  "VAULT_MTLS_TRUST_DOMAIN": "kerosene.qualification", "BITCOIN_NETWORK": "testnet3",
                  "VAULT_TLS_CERT_PATH": "/certs/server.crt", "VAULT_TLS_KEY_PATH": "/certs/server.key",
                  "VAULT_TLS_CLIENT_CA_PATH": "/certs/ca.crt", "VAULT_TLS_CLIENT_CERT_PATH": "/certs/client.crt",
                  "VAULT_TLS_CLIENT_KEY_PATH": "/certs/client.key",
                  "VAULT_HEALTH_PROBE_URL": "https://localhost:7801/v1/local-health"}
        vault_env = [{"name": key, "value": value} for key, value in values.items()]
        for variable, key in (("VAULT_SEED_PEERS", "seed-peers"),
                              ("VAULT_AUDIT_PUBKEY_ALLOWLIST", "audit-pubkeys"),
                              ("VAULT_ATTESTATION_ROOT", "attestation-root"),
                              ("VAULT_DATA_PASSPHRASE", "data-passphrase")):
            vault_env.append({"name": variable, "valueFrom": {"secretKeyRef": {"name": runtime_secret, "key": key}}})
        vault = {"name": "vault", "image": image, "imagePullPolicy": "IfNotPresent", "env": vault_env,
                 "readinessProbe": {"exec": {"command": ["/usr/local/bin/kerosene-vault", "--health-probe"]},
                                    "initialDelaySeconds": 2, "periodSeconds": 3, "timeoutSeconds": 6,
                                    "failureThreshold": 60},
                 "volumeMounts": [{"name": "vault-data", "mountPath": "/var/lib/kerosene-vault"},
                                  {"name": "certs", "mountPath": "/certs", "readOnly": True}]}
        pod["containers"].append(vault)
        pod["volumes"].extend([
            {"name": "vault-data", "persistentVolumeClaim": {"claimName": member + "-data"}},
            {"name": "certs", "secret": {"secretName": cert_secret, "defaultMode": 0o400}}])
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": member, "namespace": NAMESPACE},
            "spec": {"replicas": 1, "strategy": {"type": "Recreate"},
                     "selector": {"matchLabels": {"app": member}},
                     "template": {"metadata": {"labels": labels}, "spec": pod}}}


def main():
    if os.environ.get("KEROSENE_RUN_VAULT_PROTOCOL_KIND_TEST") != "1":
        print("SKIP: set KEROSENE_RUN_VAULT_PROTOCOL_KIND_TEST=1")
        return 0
    kubeconfig = Path(os.environ.get("KUBECONFIG", ""))
    if not kubeconfig.is_absolute() or not kubeconfig.is_file():
        raise RuntimeError("live qualification requires an explicit absolute KUBECONFIG")
    context = os.environ.get("KEROSENE_TEST_KUBE_CONTEXT", "")
    if not context:
        raise RuntimeError("KEROSENE_TEST_KUBE_CONTEXT is required")
    vault_source = Path(os.environ.get("KEROSENE_VAULT_SOURCE", ""))
    cert_script = vault_source / "scripts/ceremony/gen_mtls_certs.sh"
    if not cert_script.is_file():
        raise RuntimeError("KEROSENE_VAULT_SOURCE must name the inspected Vault source checkout")
    image = os.environ.get("KEROSENE_VAULT_TEST_IMAGE", "kerosene/vault:cell-protocol-audit-bookworm")
    prefix = ["kubectl", "--kubeconfig", str(kubeconfig), "--context", context, "--request-timeout=30s"]
    if run(prefix + ["get", "namespace", NAMESPACE], timeout=30, check=False).returncode == 0:
        raise RuntimeError("qualification namespace already exists; refusing to adopt or delete it")
    evidence = {"schema": "kerosene.vault-protocol-kind-qualification/v1", "members": {},
                "image": image, "transport": "tor", "auth": "mtls", "dkg": "distributed_wire"}
    with tempfile.TemporaryDirectory(prefix="kerosene-vault-protocol-") as temporary:
        root = Path(temporary)
        try:
            apply(prefix, {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}})
            torrc = "\n".join(("SocksPort 0.0.0.0:9050", "DataDirectory /var/lib/tor",
                                "HiddenServiceDir /var/lib/tor/vault", "HiddenServiceVersion 3",
                                "HiddenServicePort 7801 127.0.0.1:7801", "Log notice stdout", ""))
            apply(prefix, {"apiVersion": "v1", "kind": "ConfigMap",
                           "metadata": {"name": "tor-config", "namespace": NAMESPACE}, "data": {"torrc": torrc}})
            for member in MEMBERS:
                for suffix in ("tor", "data"):
                    apply(prefix, {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
                                   "metadata": {"name": member + "-" + suffix, "namespace": NAMESPACE},
                                   "spec": {"accessModes": ["ReadWriteOnce"],
                                            "resources": {"requests": {"storage": "128Mi"}}}})
                apply(prefix, deployment(member, image))
            onions = {member: wait_for(prefix, member, "test -s /var/lib/tor/vault/hostname && cat /var/lib/tor/vault/hostname")
                      for member in MEMBERS}
            if len(set(onions.values())) != 3 or any(not value.endswith(".onion") or len(value) != 62 for value in onions.values()):
                raise RuntimeError("Tor did not produce three distinct v3 onion identities")
            certs = root / "certs"
            env = os.environ.copy()
            env.update(VAULT_CEREMONY_MTLS_OUT=str(certs), VAULT_MTLS_NODE_IDS=",".join(MEMBERS),
                       VAULT_MTLS_TRUST_DOMAIN="kerosene.qualification",
                       VAULT_LAB_MTLS_ONION_SANS=",".join(onions.values()))
            run([str(cert_script)], env=env, timeout=120)
            audit_key = secrets.token_hex(32)
            for member in MEMBERS:
                node = certs / "nodes" / member
                apply(prefix, secret_document(member + "-certs", {
                    "ca.crt": (certs / "ca.crt").read_bytes(), "server.crt": (node / "server.crt").read_bytes(),
                    "server.key": (node / "server.key").read_bytes(), "client.crt": (node / "client.crt").read_bytes(),
                    "client.key": (node / "client.key").read_bytes()}))
                peers = ",".join(name + "=https://" + onions[name] + ":7801" for name in MEMBERS if name != member)
                apply(prefix, secret_document(member + "-runtime", {
                    "seed-peers": peers, "audit-pubkeys": audit_key,
                    "attestation-root": secrets.token_hex(32), "data-passphrase": secrets.token_urlsafe(48)}))
                apply(prefix, deployment(member, image, peers, member + "-certs", member + "-runtime"))
            for member in MEMBERS:
                kubectl(prefix, ["-n", NAMESPACE, "rollout", "status", "deployment/" + member, "--timeout=300s"], timeout=330)
                wait_for(prefix, member, "test -f /tmp/tor-ready", deadline=300)
                evidence["members"][member] = {"onion": onions[member], "ready": True}
            port = available_port()
            forward = subprocess.Popen(prefix + ["-n", NAMESPACE, "port-forward", "deployment/vault-1",
                                                str(port) + ":9050"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                time.sleep(2)
                if forward.poll() is not None:
                    raise RuntimeError("Tor SOCKS port-forward failed")
                curl = ["curl", "--fail", "--silent", "--show-error", "--max-time", "30",
                        "--proxy", "socks5h://127.0.0.1:" + str(port), "--cert", str(certs / "kfe/client.crt"),
                        "--key", str(certs / "kfe/client.key"), "--cacert", str(certs / "ca.crt")]
                for member in MEMBERS:
                    health = None
                    last_curl = None
                    for _ in range(60):
                        result = run(curl + ["https://" + onions[member] + ":7801/v1/health"], timeout=40, check=False)
                        last_curl = result
                        if result.returncode == 0:
                            health = json.loads(result.stdout)
                            break
                        time.sleep(3)
                    if not health or health.get("local_ready") is not True:
                        detail = ("unknown" if last_curl is None else "curl-exit-" + str(last_curl.returncode))
                        if isinstance(health, dict):
                            detail += " node_id=" + repr(health.get("node_id")) + " local_ready=" + repr(health.get("local_ready"))
                        raise RuntimeError("authenticated onion health failed for " + member + ": " + detail)
                    if (health.get("configured_members"), health.get("required_threshold"),
                            health.get("financial_ready")) != (3, 2, True):
                        raise RuntimeError("Vault financial quorum is not ready for " + member)
                    evidence["members"][member]["health"] = {key: health.get(key) for key in
                        ("local_ready", "financial_ready", "configured_members", "required_threshold")}
                urls = {member: "https://" + onions[member] + ":7801" for member in MEMBERS}

                def post(member, path, body, principal):
                    node = certs / "nodes" / principal
                    command = ["curl", "--silent", "--show-error", "--max-time", "45",
                               "--proxy", "socks5h://127.0.0.1:" + str(port), "--cert", str(node / "client.crt"),
                               "--key", str(node / "client.key"), "--cacert", str(certs / "ca.crt"),
                               "-H", "Content-Type: application/json", "-X", "POST", "--data-binary", "@-",
                               "--write-out", "\n%{http_code}",
                               urls[member] + path]
                    payload = json.dumps(body, separators=(",", ":")).encode()
                    for attempt in range(5):
                        result = run(command, data=payload, timeout=50, check=False)
                        content, separator, status = result.stdout.rpartition(b"\n")
                        if result.returncode == 0 and separator and status.startswith(b"2"):
                            return json.loads(content)
                        if result.returncode != 0 or status == b"000":
                            time.sleep(2 + attempt)
                            continue
                        raise RuntimeError("authenticated DKG request rejected at " + member + path +
                                           " for " + principal + " with HTTP " + status.decode(errors="replace"))
                    raise RuntimeError("authenticated DKG transport failed at " + member + path + " for " + principal)

                session = "qualification-" + secrets.token_hex(8)
                start = {"session_id": session, "max_signers": 3, "min_signers": 2,
                         "roster": list(MEMBERS), "fanout": False}
                round1 = {member: post(member, "/v1/dkg/round1", start, member)["round1"] for member in MEMBERS}
                for destination in MEMBERS:
                    for sender, message in round1.items():
                        post(destination, "/v1/dkg/round1", message, sender)
                outbound = []
                for member in MEMBERS:
                    response = post(member, "/v1/dkg/round2",
                                    {"session_id": session, "deliver": True, "fanout": False}, member)
                    outbound.extend(response.get("outbound", []))
                for message in outbound:
                    recipient = message.get("recipient_node_id")
                    sender = message.get("sender_node_id")
                    if recipient not in MEMBERS or sender not in MEMBERS:
                        raise RuntimeError("DKG round2 returned a foreign member")
                    post(recipient, "/v1/dkg/round2", message, sender)
                for member in MEMBERS:
                    response = post(member, "/v1/dkg/round3", {"session_id": session, "finalize": True}, member)
                    if not isinstance(response, dict):
                        raise RuntimeError("DKG round3 returned an invalid status")
                evidence["distributedWireDkgCompleted"] = True
            finally:
                forward.terminate()
                try:
                    forward.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    forward.kill()
                    forward.wait(timeout=10)
            print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
        finally:
            kubectl(prefix, ["delete", "namespace", NAMESPACE, "--wait=true", "--timeout=180s"], timeout=210)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
