#!/usr/bin/env python3
"""Opt-in live qualification of both production Node planes over Tor and mTLS.

Every key, certificate, onion identity and state artifact is disposable synthetic
laboratory material. The fixed namespace is never adopted or silently deleted.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


NAMESPACE = "kerosene-node-protocol-qualification"
NETWORK = "kerosene-node-qualification"
PLANES = ("bank", "vault")
MEMBERS = tuple(f"node-{plane}-{index}" for plane in PLANES for index in range(1, 4))
CONTRACT_VERSION = "0.2.0"


def run(argv, *, data=None, env=None, timeout=120, check=True):
    result = subprocess.run(argv, input=data, capture_output=True, env=env,
                            timeout=timeout, check=False)
    if check and result.returncode:
        raise RuntimeError("live qualification command failed: " + " ".join(argv[:5]))
    return result


def kubectl(prefix, args, *, document=None, timeout=120, check=True):
    payload = None if document is None else json.dumps(document, separators=(",", ":")).encode()
    return run(prefix + args, data=payload, timeout=timeout, check=check)


def apply(prefix, document):
    kubectl(prefix, ["apply", "-f", "-"], document=document)


def available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for(prefix, member, command, deadline=300):
    stop = time.monotonic() + deadline
    while time.monotonic() < stop:
        result = run(prefix + ["-n", NAMESPACE, "exec", "deployment/" + member,
                               "-c", "tor", "--", "sh", "-c", command],
                     timeout=15, check=False)
        if result.returncode == 0:
            return result.stdout.decode().strip()
        time.sleep(2)
    raise RuntimeError("timed out waiting for " + member)


def secret(name, values):
    return {"apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": name, "namespace": NAMESPACE}, "type": "Opaque",
            "data": {key: base64.b64encode(value if isinstance(value, bytes) else value.encode()).decode()
                     for key, value in values.items()}}


def field(value):
    value = value if isinstance(value, bytes) else value.encode()
    return struct.pack(">Q", len(value)) + value


def integer(value):
    return struct.pack(">Q", value)


def member_id(public_key):
    return hashlib.sha256(NETWORK.encode() + public_key).hexdigest()


def manifest_signing_bytes(manifest):
    output = field(b"KEROSENE_MEMBERSHIP_MANIFEST_V1")
    output += field(manifest["contract_version"])
    output += field(manifest["network_id"])
    output += field(manifest["plane"])
    output += integer(manifest["epoch"])
    output += field(manifest["phase"])
    output += field(manifest["previous_manifest_hash"])
    output += integer(manifest["threshold"])
    output += integer(len(manifest["members"]))
    for member in manifest["members"]:
        output += field(member["member_id"])
        output += field(member["root_public_key"])
        output += field(member["endpoint"])
    output += field(b"none")
    return output


def attestation_signing_bytes(attestation):
    output = field(b"KEROSENE_STATE_SNAPSHOT_ATTESTATION_V1")
    output += field(attestation["contract_version"])
    output += field(attestation["network_id"])
    output += field(attestation["plane"])
    output += field(attestation["membership_manifest_hash"])
    output += integer(attestation["snapshot_epoch"])
    output += field(attestation["state_root"])
    output += integer(attestation["created_at_epoch_ms"])
    return output


def signed_plane_material(plane, onions, identities):
    members = []
    plane_names = [name for name in MEMBERS if name.startswith("node-" + plane + "-")]
    for name in plane_names:
        public = identities[name].public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        members.append({"member_id": member_id(public), "root_public_key": public.hex(),
                        "endpoint": "https://" + onions[name] + ":8800"})
    manifest = {"contract_version": CONTRACT_VERSION, "network_id": NETWORK,
                "plane": plane, "epoch": 1, "phase": "stable",
                "previous_manifest_hash": "0" * 64, "threshold": 2,
                "members": members, "next_epoch": None, "signatures": []}
    signing = manifest_signing_bytes(manifest)
    for name in plane_names[:2]:
        public = identities[name].public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        manifest["signatures"].append({"signer_id": member_id(public),
                                       "signature": identities[name].sign(signing).hex()})
    manifest_hash = hashlib.sha256(signing).hexdigest()
    payload = ("qualified-state:" + plane + ":epoch-1").encode()
    attestation = {"contract_version": CONTRACT_VERSION, "network_id": NETWORK,
                   "plane": plane, "membership_manifest_hash": manifest_hash,
                   "snapshot_epoch": 1, "state_root": hashlib.sha256(payload).hexdigest(),
                   "created_at_epoch_ms": 1, "signatures": []}
    attestation_signing = attestation_signing_bytes(attestation)
    for name in plane_names[:2]:
        public = identities[name].public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        attestation["signatures"].append({"signer_id": member_id(public),
                                          "signature": identities[name].sign(attestation_signing).hex()})
    return manifest, attestation, payload


def tor_deployment(member, image, runtime=None, vault_image=None):
    labels = {"app": member, "qualification": "kerosene-node-protocol"}
    tor_start = ("rm -f /tmp/tor-ready /tmp/tor.log; tor -f /etc/tor/torrc >/tmp/tor.log 2>&1 & pid=$!; "
                 "while ! grep -q 'Bootstrapped 100% (done): Done' /tmp/tor.log; do "
                 "kill -0 $pid || exit 1; sleep 1; done; "
                 "while [ ! -s /var/lib/tor/node/hostname ]; do kill -0 $pid || exit 1; sleep 1; done; "
                 "cp /var/lib/tor/node/hostname /onion/hostname; chmod 0444 /onion/hostname; touch /tmp/tor-ready; "
                 "tail -n +1 -F /tmp/tor.log & wait $pid")
    tor = {"name": "tor", "image": os.environ.get("KEROSENE_TOR_TEST_IMAGE", "kerosene/tor:staging"),
           "imagePullPolicy": "IfNotPresent", "command": ["sh", "-c", tor_start],
           "ports": [{"name": "socks", "containerPort": 9050}],
           "volumeMounts": [{"name": "tor-config", "mountPath": "/etc/tor/torrc", "subPath": "torrc", "readOnly": True},
                            {"name": "tor-data", "mountPath": "/var/lib/tor"},
                            {"name": "onion-public", "mountPath": "/onion"}]}
    pod = {"automountServiceAccountToken": False, "securityContext": {"fsGroup": 1000},
           "containers": [tor],
           "volumes": [{"name": "tor-config", "configMap": {"name": "tor-config"}},
                       {"name": "tor-data", "persistentVolumeClaim": {"claimName": member + "-tor"}},
                       {"name": "onion-public", "emptyDir": {}}]}
    if runtime is not None:
        plane = runtime["plane"]
        values = {"KEROSENE_NETWORK_ID": NETWORK, "KEROSENE_DISCOVERY_PLANE": plane,
                  "KEROSENE_NODE_LISTEN_ADDR": "127.0.0.1:8800",
                  "KEROSENE_NODE_ONION_HOSTNAME_PATH": "/onion/hostname",
                  "KEROSENE_NODE_ONION_PORT": "8800", "KEROSENE_NODE_ONION_WAIT_TIMEOUT_MS": "300000",
                  "KEROSENE_IDENTITY_KEY_PATH": "/node-data/identity.key",
                  "KEROSENE_PEER_STORE": "/node-data/peer-store", "KEROSENE_LEDGER_DB_PATH": "/node-data/ledger",
                  "KEROSENE_GENESIS_TRUST_BUNDLE": "/trust/genesis.json",
                  "KEROSENE_TLS_CERT_PATH": "/certs/server.crt", "KEROSENE_TLS_KEY_PATH": "/certs/server.key",
                  "KEROSENE_TLS_CLIENT_CA_PATH": "/certs/ca.crt",
                  "KEROSENE_TLS_CLIENT_IDENTITY_PEM": "/certs/client-identity.pem",
                  "KEROSENE_TOR_SOCKS_PROXY": "socks5h://127.0.0.1:9050",
                  "KEROSENE_GENESIS_ENDPOINTS": runtime["peers"], "KEROSENE_DISCOVERY_MIRRORS": "",
                  "KEROSENE_DISCOVERY_INTERVAL_MS": "2000", "KEROSENE_CHALLENGE_TTL_MS": "300000",
                  "KEROSENE_PEER_LIVE_WINDOW_MS": "120000",
                  "KEROSENE_STATE_SNAPSHOT_ATTESTATION_PATH": "/state/attestation.json",
                  "KEROSENE_STATE_SNAPSHOT_PAYLOAD_PATH": "/state/snapshot.bin"}
        init = {"name": "identity-bootstrap", "image": image,
                "command": ["sh", "-c", "set -eu; if [ ! -f /node-data/identity.key ]; then cp /identity/identity.key /node-data/identity.key; fi; chmod 600 /node-data/identity.key"],
                "volumeMounts": [{"name": "node-data", "mountPath": "/node-data"},
                                 {"name": "identity", "mountPath": "/identity", "readOnly": True}]}
        node = {"name": "node", "image": image, "imagePullPolicy": "IfNotPresent",
                "env": [{"name": key, "value": value} for key, value in values.items()],
                "readinessProbe": {"exec": {"command": ["/usr/local/bin/kerosene-node", "--health-probe"]},
                                   "initialDelaySeconds": 2, "periodSeconds": 3, "timeoutSeconds": 6,
                                   "failureThreshold": 100},
                "volumeMounts": [{"name": "tor-data", "mountPath": "/var/lib/tor"},
                                 {"name": "onion-public", "mountPath": "/onion", "readOnly": True},
                                 {"name": "node-data", "mountPath": "/node-data"},
                                 {"name": "identity", "mountPath": "/identity", "readOnly": True},
                                 {"name": "certs", "mountPath": "/certs", "readOnly": True},
                                 {"name": "trust", "mountPath": "/trust", "readOnly": True},
                                 {"name": "state", "mountPath": "/state", "readOnly": True}]}
        pod["initContainers"] = [init]
        pod["containers"].append(node)
        pod["volumes"].extend([
            {"name": "node-data", "persistentVolumeClaim": {"claimName": member + "-data"}},
            {"name": "identity", "secret": {"secretName": member + "-identity", "defaultMode": 0o440}},
            {"name": "certs", "secret": {"secretName": member + "-certs", "defaultMode": 0o440}},
            {"name": "trust", "configMap": {"name": "genesis"}},
            {"name": "state", "configMap": {"name": plane + "-state"}}])
        if plane == "vault" and vault_image is not None:
            vault_values = {
                "KEROSENE_ENV": "production", "VAULT_CEREMONY_MODE": "production",
                "VAULT_NODE_ID": runtime["member_id"], "VAULT_NODE_TIER": "domestic", "ATTESTATION_MODE": "software",
                "VAULT_MEASUREMENT_PIN": "a" * 64, "VAULT_LISTEN_ADDR": "127.0.0.1:7801",
                "VAULT_DATA_DIR": "/vault-data", "VAULT_AUTH_MODE": "mtls", "VAULT_TRANSPORT": "tor",
                "VAULT_SOCKS_PROXY": "socks5h://127.0.0.1:9050", "VAULT_GENESIS_N": "3",
                "VAULT_DKG_MODE": "distributed_wire", "VAULT_SHARE_STORE": "aead_disk",
                "VAULT_TLS_VERIFY_MODE": "onion_or_spiffe",
                "VAULT_MTLS_TRUST_DOMAIN": "kerosene.node.qualification", "BITCOIN_NETWORK": "testnet3",
                "VAULT_TLS_CERT_PATH": "/certs/server.crt", "VAULT_TLS_KEY_PATH": "/certs/server.key",
                "VAULT_TLS_CLIENT_CA_PATH": "/certs/ca.crt", "VAULT_TLS_CLIENT_CERT_PATH": "/certs/client.crt",
                "VAULT_TLS_CLIENT_KEY_PATH": "/certs/client.key",
                "VAULT_HEALTH_PROBE_URL": "https://localhost:7801/v1/local-health",
                "VAULT_KEROSENE_NODE_URL": "https://localhost:8800",
                "VAULT_KEROSENE_NETWORK_ID": NETWORK,
                "VAULT_KEROSENE_NODE_MEMBER_ID": runtime["member_id"],
                "VAULT_KEROSENE_NODE_CLIENT_IDENTITY_PEM": "/certs/client-identity.pem",
                "VAULT_KEROSENE_NODE_CA_PATH": "/certs/ca.crt", "VAULT_KEROSENE_SERVICE_PORT": "7801"}
            vault_env = [{"name": key, "value": value} for key, value in vault_values.items()]
            for variable, key in (("VAULT_AUDIT_PUBKEY_ALLOWLIST", "audit-pubkeys"),
                                  ("VAULT_TLS_PEER_SPIFFE_ID", "tls-peer-spiffe-ids"),
                                  ("VAULT_ATTESTATION_ROOT", "attestation-root"),
                                  ("VAULT_DATA_PASSPHRASE", "data-passphrase")):
                vault_env.append({"name": variable, "valueFrom": {
                    "secretKeyRef": {"name": member + "-vault-runtime", "key": key}}})
            vault = {"name": "vault", "image": vault_image, "imagePullPolicy": "IfNotPresent",
                     "env": vault_env,
                     "readinessProbe": {"exec": {"command": ["/usr/local/bin/kerosene-vault", "--health-probe"]},
                                        "initialDelaySeconds": 2, "periodSeconds": 3, "timeoutSeconds": 6,
                                        "failureThreshold": 120},
                     "volumeMounts": [{"name": "vault-data", "mountPath": "/vault-data"},
                                      {"name": "certs", "mountPath": "/certs", "readOnly": True}]}
            pod["containers"].append(vault)
            pod["volumes"].append({"name": "vault-data", "persistentVolumeClaim": {
                "claimName": member + "-vault-data"}})
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": member, "namespace": NAMESPACE},
            "spec": {"replicas": 1, "strategy": {"type": "Recreate"},
                     "selector": {"matchLabels": {"app": member}},
                     "template": {"metadata": {"labels": labels}, "spec": pod}}}


def curl_json(curl, method, url, body=None, attempts=50):
    command = curl + (["-X", method] if method != "GET" else [])
    if body is not None:
        command += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    command += ["--write-out", "\n%{http_code}", url]
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    last = None
    for _ in range(attempts):
        last = run(command, data=payload, timeout=40, check=False)
        content, separator, status = last.stdout.rpartition(b"\n")
        if last.returncode == 0 and separator and status.startswith(b"2"):
            return json.loads(content)
        time.sleep(3)
    code = "unknown" if last is None else str(last.returncode)
    detail = "" if last is None else last.stderr.decode(errors="replace").strip().replace("\n", " ")[:400]
    raise RuntimeError("authenticated onion request failed: curl-exit-" + code +
                       (" (" + detail + ")" if detail else ""))


def main():
    if os.environ.get("KEROSENE_RUN_NODE_PROTOCOL_KIND_TEST") != "1":
        print("SKIP: set KEROSENE_RUN_NODE_PROTOCOL_KIND_TEST=1")
        return 0
    kubeconfig = Path(os.environ.get("KUBECONFIG", ""))
    context = os.environ.get("KEROSENE_TEST_KUBE_CONTEXT", "")
    if not kubeconfig.is_absolute() or not kubeconfig.is_file() or not context:
        raise RuntimeError("live qualification requires explicit KUBECONFIG and context")
    vault_source = Path(os.environ.get("KEROSENE_VAULT_SOURCE", ""))
    cert_script = vault_source / "scripts/ceremony/gen_mtls_certs.sh"
    if not cert_script.is_file():
        raise RuntimeError("KEROSENE_VAULT_SOURCE must name the inspected Vault checkout")
    image = os.environ.get("KEROSENE_NODE_TEST_IMAGE", "kerosene/node:cell-protocol-attested-v2")
    vault_image = os.environ.get("KEROSENE_VAULT_TEST_IMAGE", "kerosene/vault:cell-protocol-financial-ready-v2")
    prefix = ["kubectl", "--kubeconfig", str(kubeconfig), "--context", context, "--request-timeout=30s"]
    forward_prefix = ["kubectl", "--kubeconfig", str(kubeconfig), "--context", context]
    if run(prefix + ["get", "namespace", NAMESPACE], timeout=30, check=False).returncode == 0:
        raise RuntimeError("qualification namespace already exists; refusing adoption")
    evidence = {"schema": "kerosene.node-vault-protocol-kind-qualification/v1",
                "nodeImage": image, "vaultImage": vault_image,
                "transport": "tor", "auth": "mtls", "planes": {}, "vaultQuorum": {}}
    with tempfile.TemporaryDirectory(prefix="kerosene-node-protocol-") as temporary:
        root = Path(temporary)
        try:
            apply(prefix, {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}})
            torrc = "\n".join(("SocksPort 0.0.0.0:9050", "DataDirectory /var/lib/tor",
                                "HiddenServiceDir /var/lib/tor/node", "HiddenServiceVersion 3",
                                "HiddenServicePort 8800 127.0.0.1:8800",
                                "HiddenServicePort 7801 127.0.0.1:7801", "Log notice stdout", ""))
            apply(prefix, {"apiVersion": "v1", "kind": "ConfigMap",
                           "metadata": {"name": "tor-config", "namespace": NAMESPACE}, "data": {"torrc": torrc}})
            for member in MEMBERS:
                for suffix in ("tor", "data"):
                    apply(prefix, {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
                                   "metadata": {"name": member + "-" + suffix, "namespace": NAMESPACE},
                                   "spec": {"accessModes": ["ReadWriteOnce"],
                                            "resources": {"requests": {"storage": "128Mi"}}}})
                if member.startswith("node-vault-"):
                    apply(prefix, {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
                                   "metadata": {"name": member + "-vault-data", "namespace": NAMESPACE},
                                   "spec": {"accessModes": ["ReadWriteOnce"],
                                            "resources": {"requests": {"storage": "128Mi"}}}})
                apply(prefix, tor_deployment(member, image))
            onions = {member: wait_for(prefix, member, "test -s /var/lib/tor/node/hostname && cat /var/lib/tor/node/hostname")
                      for member in MEMBERS}
            if len(set(onions.values())) != len(MEMBERS) or any(len(value) != 62 or not value.endswith(".onion") for value in onions.values()):
                raise RuntimeError("Tor did not produce six distinct v3 onion identities")
            bootstrap_pods = {
                member: kubectl(prefix, ["-n", NAMESPACE, "get", "pod", "-l", "app=" + member,
                                           "-o", "jsonpath={.items[0].metadata.name}"]).stdout.decode()
                for member in MEMBERS}

            identities = {member: Ed25519PrivateKey.generate() for member in MEMBERS}
            identity_ids = {member: member_id(identity.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw))
                for member, identity in identities.items()}
            planes = {plane: [name for name in MEMBERS if name.startswith("node-" + plane + "-")]
                      for plane in PLANES}
            trust = {plane: {"threshold": 2, "members": [
                {"member_id": identity_ids[name],
                 "root_public_key": identities[name].public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()}
                for name in names]} for plane, names in planes.items()}
            genesis = {"contract_version": CONTRACT_VERSION, "network_id": NETWORK,
                       "bank": trust["bank"], "vault": trust["vault"], "created_at_epoch_ms": 1}
            apply(prefix, {"apiVersion": "v1", "kind": "ConfigMap",
                           "metadata": {"name": "genesis", "namespace": NAMESPACE},
                           "data": {"genesis.json": json.dumps(genesis, separators=(",", ":"))}})
            material = {plane: signed_plane_material(plane, onions, identities) for plane in PLANES}
            for plane, (_, attestation, payload) in material.items():
                apply(prefix, {"apiVersion": "v1", "kind": "ConfigMap",
                               "metadata": {"name": plane + "-state", "namespace": NAMESPACE},
                               "data": {"attestation.json": json.dumps(attestation, separators=(",", ":")),
                                        "snapshot.bin": payload.decode()}})

            certs = root / "certs"
            env = os.environ.copy()
            env.update(VAULT_CEREMONY_MTLS_OUT=str(certs),
                       VAULT_MTLS_NODE_IDS=",".join(MEMBERS),
                       VAULT_MTLS_NODE_MEMBER_IDS=",".join(identity_ids[member] for member in MEMBERS),
                       VAULT_MTLS_TRUST_DOMAIN="kerosene.node.qualification",
                       VAULT_LAB_MTLS_ONION_SANS=",".join(onions.values()))
            run([str(cert_script)], env=env, timeout=120)
            for member in MEMBERS:
                private = identities[member].private_bytes(
                    serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                    serialization.NoEncryption()).hex() + "\n"
                apply(prefix, secret(member + "-identity", {"identity.key": private}))
                cert = certs / "nodes" / member
                client_identity = (cert / "client.crt").read_bytes() + b"\n" + (cert / "client.key").read_bytes()
                apply(prefix, secret(member + "-certs", {
                    "ca.crt": (certs / "ca.crt").read_bytes(),
                    "server.crt": (cert / "server.crt").read_bytes(),
                    "server.key": (cert / "server.key").read_bytes(),
                    "client.crt": (cert / "client.crt").read_bytes(),
                    "client.key": (cert / "client.key").read_bytes(),
                    "client-identity.pem": client_identity}))
                plane = member.split("-")[1]
                peers = ",".join("https://" + onions[name] + ":8800" for name in planes[plane] if name != member)
                runtime = {"plane": plane, "peers": peers,
                           "member_id": identity_ids[member]}
                if plane == "vault":
                    apply(prefix, secret(member + "-vault-runtime", {
                        "audit-pubkeys": os.urandom(32).hex(),
                        "tls-peer-spiffe-ids": ",".join(
                            "spiffe://kerosene.node.qualification/vault/" + identity_ids[name]
                            for name in planes["vault"]),
                        "attestation-root": os.urandom(32).hex(),
                        "data-passphrase": os.urandom(48).hex()}))
                apply(prefix, tor_deployment(member, image, runtime, vault_image))
            for member, pod_name in bootstrap_pods.items():
                kubectl(prefix, ["-n", NAMESPACE, "wait", "--for=delete", "pod/" + pod_name,
                                 "--timeout=180s"], timeout=210)
            for member in MEMBERS:
                wait_for(prefix, member, "test -f /tmp/tor-ready", deadline=300)

            port = available_port()
            forward = subprocess.Popen(forward_prefix + ["-n", NAMESPACE, "port-forward", "deployment/node-bank-1",
                                                str(port) + ":9050"], stdout=subprocess.DEVNULL)
            try:
                time.sleep(2)
                if forward.poll() is not None:
                    raise RuntimeError("Tor SOCKS port-forward failed")
                client = certs / "kfe"
                curl = ["curl", "--silent", "--show-error", "--max-time", "30",
                        "--proxy", "socks5h://127.0.0.1:" + str(port),
                        "--cert", str(client / "client.crt"), "--key", str(client / "client.key"),
                        "--cacert", str(certs / "ca.crt")]
                for plane in PLANES:
                    manifest = material[plane][0]
                    for member in planes[plane]:
                        curl_json(curl, "POST", "https://" + onions[member] + ":8800/v1/membership", manifest)
                kubectl(prefix, ["-n", NAMESPACE, "delete", "pod", "-l",
                                 "app in (node-vault-1,node-vault-2,node-vault-3)",
                                 "--wait=true", "--timeout=180s"], timeout=210)
                for member in MEMBERS:
                    kubectl(prefix, ["-n", NAMESPACE, "rollout", "status", "deployment/" + member,
                                     "--timeout=360s"], timeout=390)
                rejection_port = available_port()
                rejection_forward = subprocess.Popen(
                    forward_prefix + ["-n", NAMESPACE, "port-forward",
                                      "deployment/" + planes["vault"][0], str(rejection_port) + ":8800"],
                    stdout=subprocess.DEVNULL)
                rejection_payload = json.dumps(material["bank"][0], separators=(",", ":")).encode()
                rejection_status = None
                try:
                    time.sleep(2)
                    rejection_command = ["curl", "--silent", "--show-error", "--max-time", "15",
                                         "--cert", str(client / "client.crt"), "--key", str(client / "client.key"),
                                         "--cacert", str(certs / "ca.crt"), "-H", "Content-Type: application/json",
                                         "-X", "POST", "--data-binary", "@-", "--write-out", "\n%{http_code}",
                                         "https://localhost:" + str(rejection_port) + "/v1/membership"]
                    rejected = run(rejection_command, data=rejection_payload, timeout=20, check=False)
                    _, separator, status = rejected.stdout.rpartition(b"\n")
                    if rejected.returncode == 0 and separator and status != b"000":
                        rejection_status = status
                finally:
                    rejection_forward.terminate()
                    try:
                        rejection_forward.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        rejection_forward.kill()
                        rejection_forward.wait(timeout=10)
                if rejection_status != b"422":
                    rendered = "transport-unavailable" if rejection_status is None else rejection_status.decode(errors="replace")
                    raise RuntimeError("Vault plane cross-plane manifest rejection differed: HTTP " + rendered)
                for plane in PLANES:
                    evidence["planes"][plane] = {"threshold": 2, "members": {}}
                    for member in planes[plane]:
                        ready = curl_json(curl, "GET", "https://" + onions[member] + ":8800/ready-financial",
                                          attempts=80)
                        expected = (ready.get("plane"), ready.get("verified_members"),
                                    ready.get("required_threshold"), ready.get("financial_ready"))
                        if expected != (plane, 3, 2, True):
                            raise RuntimeError("Node financial readiness contract failed for " + member)
                        evidence["planes"][plane]["members"][member] = {
                            "onion": onions[member], "member_ready": ready.get("member_ready"),
                            "quorum_ready": ready.get("quorum_ready"),
                            "financial_ready": ready.get("financial_ready")}

                def vault_request(member, method, path, body=None, principal="node-vault-1", attempts=60):
                    identity = certs / "nodes" / principal
                    command = ["curl", "--silent", "--show-error", "--max-time", "45",
                               "--proxy", "socks5h://127.0.0.1:" + str(port),
                               "--cert", str(identity / "client.crt"),
                               "--key", str(identity / "client.key"), "--cacert", str(certs / "ca.crt")]
                    if method != "GET":
                        command += ["-X", method]
                    if body is not None:
                        command += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
                    command += ["--write-out", "\n%{http_code}",
                                "https://" + onions[member] + ":7801" + path]
                    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
                    for attempt in range(attempts):
                        result = run(command, data=payload, timeout=50, check=False)
                        content, separator, status = result.stdout.rpartition(b"\n")
                        if result.returncode == 0 and separator and status.startswith(b"2"):
                            return json.loads(content)
                        if result.returncode != 0 or status == b"000":
                            time.sleep(min(2 + attempt, 5))
                            continue
                        raise RuntimeError("Vault request rejected at " + member + path +
                                           " with HTTP " + status.decode(errors="replace"))
                    raise RuntimeError("Vault request transport failed at " + member + path)

                vault_members = planes["vault"]
                vault_ids = {member: identity_ids[member] for member in vault_members}
                vault_names_by_id = {identifier: member for member, identifier in vault_ids.items()}

                def wait_vault_health(member, attempts=80):
                    last = None
                    for _ in range(attempts):
                        last = vault_request(member, "GET", "/v1/health", attempts=1)
                        if (last.get("local_ready"), last.get("configured_members"),
                                last.get("required_threshold"), last.get("financial_ready")) == (True, 3, 2, True):
                            return last
                        time.sleep(3)
                    safe = {key: last.get(key) for key in
                            ("local_ready", "financial_ready", "configured_members", "required_threshold",
                             "peer_count", "peer_reachability")} if isinstance(last, dict) else None
                    raise RuntimeError("integrated Vault quorum did not converge for " + member + ": " + repr(safe))

                for member in vault_members:
                    health = wait_vault_health(member)
                    evidence["vaultQuorum"][member] = {key: health.get(key) for key in
                        ("local_ready", "financial_ready", "configured_members", "required_threshold")}

                session = "integrated-qualification-" + hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16]
                start = {"session_id": session, "max_signers": 3, "min_signers": 2,
                         "roster": list(vault_ids.values()), "fanout": False}
                round1 = {member: vault_request(member, "POST", "/v1/dkg/round1", start,
                                                principal=member)["round1"] for member in vault_members}
                for destination in vault_members:
                    for sender, message in round1.items():
                        vault_request(destination, "POST", "/v1/dkg/round1", message, principal=sender)
                outbound = []
                for member in vault_members:
                    response = vault_request(member, "POST", "/v1/dkg/round2",
                                             {"session_id": session, "deliver": True, "fanout": False},
                                             principal=member)
                    outbound.extend(response.get("outbound", []))
                for message in outbound:
                    recipient_id = message.get("recipient_node_id")
                    sender_id = message.get("sender_node_id")
                    recipient = vault_names_by_id.get(recipient_id)
                    sender = vault_names_by_id.get(sender_id)
                    if recipient is None or sender is None:
                        raise RuntimeError("integrated DKG returned a foreign member")
                    vault_request(recipient, "POST", "/v1/dkg/round2", message, principal=sender)
                for member in vault_members:
                    vault_request(member, "POST", "/v1/dkg/round3",
                                  {"session_id": session, "finalize": True}, principal=member)
                evidence["distributedWireDkgCompleted"] = True

                interrupted = vault_members[-1]
                kubectl(prefix, ["-n", NAMESPACE, "scale", "deployment/" + interrupted, "--replicas=0"])
                kubectl(prefix, ["-n", NAMESPACE, "wait", "--for=delete", "pod", "-l", "app=" + interrupted,
                                 "--timeout=180s"], timeout=210)
                for member in vault_members[:-1]:
                    health = vault_request(member, "GET", "/v1/health", attempts=40)
                    if health.get("financial_ready") is not True:
                        raise RuntimeError("Vault quorum did not survive one member interruption")
                for member in planes["bank"]:
                    ready = curl_json(curl, "GET", "https://" + onions[member] + ":8800/ready-financial",
                                      attempts=20)
                    if ready.get("financial_ready") is not True:
                        raise RuntimeError("Bank Node plane degraded during Vault interruption")
                kubectl(prefix, ["-n", NAMESPACE, "scale", "deployment/" + interrupted, "--replicas=1"])
                kubectl(prefix, ["-n", NAMESPACE, "rollout", "status", "deployment/" + interrupted,
                                 "--timeout=360s"], timeout=390)
                recovered_onion = wait_for(prefix, interrupted,
                                           "test -s /var/lib/tor/node/hostname && cat /var/lib/tor/node/hostname")
                if recovered_onion != onions[interrupted]:
                    raise RuntimeError("Vault/Node identity changed across interruption")
                recovered_node = curl_json(curl, "GET", "https://" + onions[interrupted] + ":8800/ready-financial",
                                           attempts=80)
                recovered_vault = wait_vault_health(interrupted)
                if recovered_node.get("financial_ready") is not True or recovered_vault.get("financial_ready") is not True:
                    raise RuntimeError("integrated Vault/Node member did not recover financial readiness")
                evidence["singleMemberInterruption"] = {
                    "member": interrupted, "identityPreserved": True,
                    "bankPlanePreserved": True, "vaultQuorumPreserved": True, "recovered": True}
            finally:
                forward.terminate()
                try:
                    forward.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    forward.kill()
                    forward.wait(timeout=10)
            print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
            return 0
        finally:
            kubectl(prefix, ["delete", "namespace", NAMESPACE, "--wait=true", "--timeout=180s"],
                    timeout=210, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
