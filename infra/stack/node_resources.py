"""Generate the six deterministic Node workloads from signed public inputs."""

import base64
import hashlib
import re
from archive import ArchiveError, canonical_bytes, open_regular, strict_json, write_new
import lifecycle

MAX_DOCUMENT = 1024 * 1024


def document(path, label):
    with open_regular(path) as stream:
        raw = stream.read(MAX_DOCUMENT + 1)
    if len(raw) > MAX_DOCUMENT:
        raise ArchiveError(f"{label} byte limit exceeded")
    value = strict_json(raw)
    if not isinstance(value, dict):
        raise ArchiveError(f"{label} must be a JSON object")
    return value


def validate_manifest(value, network, plane):
    if (value.get("network_id") != network or value.get("plane") != plane or
            value.get("threshold") != 2 or not isinstance(value.get("members"), list) or
            len(value["members"]) != 3 or not isinstance(value.get("signatures"), list) or
            len(value["signatures"]) < 2):
        raise ArchiveError(f"{plane} membership must describe a signed 2-of-3 plane")
    endpoints = [member.get("endpoint") if isinstance(member, dict) else None
                 for member in value["members"]]
    if (any(not isinstance(endpoint, str) or not re.fullmatch(
            r"https://[a-z2-7]{56}\.onion:8800", endpoint) for endpoint in endpoints) or
            len(set(endpoints)) != 3):
        raise ArchiveError(f"{plane} membership endpoints must be distinct v3 Onion HTTPS endpoints")
    return endpoints


def validate_attestation(value, network, plane, manifest, payload):
    signable = {key: item for key, item in manifest.items() if key != "signatures"}
    manifest_hash = hashlib.sha256(canonical_bytes(signable)).hexdigest()
    state_root = hashlib.sha256(payload).hexdigest()
    if (value.get("network_id") != network or value.get("plane") != plane or
            value.get("membership_manifest_hash") != manifest_hash or value.get("state_root") != state_root or
            not isinstance(value.get("signatures"), list) or len(value["signatures"]) < 2):
        raise ArchiveError(f"{plane} state attestation is incompatible")


def config_map(namespace, name, data, binary=None):
    result = {"apiVersion": "v1", "kind": "ConfigMap", "immutable": True,
              "metadata": {"name": name, "namespace": namespace}, "data": data}
    if binary:
        result["binaryData"] = binary
    return result


def mode_items(keys):
    return [{"key": key, "path": key, "mode": 256} for key in keys]


def node_workload(name, namespace, plane, network):
    common = {
        "KEROSENE_NETWORK_ID": network, "KEROSENE_DISCOVERY_PLANE": plane,
        "KEROSENE_NODE_LISTEN_ADDR": "127.0.0.1:8800",
        "KEROSENE_NODE_ONION_HOSTNAME_PATH": "/onion/hostname", "KEROSENE_NODE_ONION_PORT": "8800",
        "KEROSENE_IDENTITY_KEY_PATH": "/var/lib/kerosene/identity.key",
        "KEROSENE_PEER_STORE": "/var/lib/kerosene/peer-store", "KEROSENE_LEDGER_DB_PATH": "/var/lib/kerosene/ledger",
        "KEROSENE_GENESIS_TRUST_BUNDLE": "/etc/kerosene/node-genesis/genesis-trust-bundle.json",
        "KEROSENE_TLS_CERT_PATH": "/etc/kerosene/node-mtls/server.crt",
        "KEROSENE_TLS_KEY_PATH": "/etc/kerosene/node-mtls/server.key",
        "KEROSENE_TLS_CLIENT_CA_PATH": "/etc/kerosene/node-mtls/ca.crt",
        "KEROSENE_TLS_CLIENT_IDENTITY_PEM": "/etc/kerosene/node-mtls/client-identity.pem",
        "KEROSENE_INITIAL_MEMBERSHIP_MANIFEST_PATH": "/etc/kerosene/node-membership/manifest.json",
        "KEROSENE_STATE_SNAPSHOT_ATTESTATION_PATH": "/etc/kerosene/node-state/attestation.json",
        "KEROSENE_STATE_SNAPSHOT_PAYLOAD_PATH": "/etc/kerosene/node-state/snapshot.bin",
        "KEROSENE_TOR_SOCKS_PROXY": "socks5h://127.0.0.1:9050", "KEROSENE_CHALLENGE_TTL_MS": "300000"}
    node = {"name": "node", "image": "kerosene-cell.invalid/node:selected",
            "env": [{"name": key, "value": value} for key, value in common.items()] + [
                {"name": "KEROSENE_GENESIS_ENDPOINTS", "valueFrom": {"configMapKeyRef": {"name": f"node-{plane}-bootstrap", "key": "genesis-endpoints"}}},
                {"name": "KEROSENE_DISCOVERY_MIRRORS", "valueFrom": {"configMapKeyRef": {"name": f"node-{plane}-bootstrap", "key": "mirrors"}}}],
            "readinessProbe": {"exec": {"command": ["/usr/local/bin/kerosene-node", "--health-probe"]}, "timeoutSeconds": 6},
            "volumeMounts": [
                {"name": "identity-data", "mountPath": "/var/lib/kerosene"},
                {"name": "onion-public", "mountPath": "/onion", "readOnly": True},
                {"name": "node-identity", "mountPath": "/var/lib/kerosene/identity.key", "subPath": "identity.key", "readOnly": True},
                {"name": "node-genesis", "mountPath": "/etc/kerosene/node-genesis", "readOnly": True},
                {"name": "node-mtls", "mountPath": "/etc/kerosene/node-mtls", "readOnly": True},
                {"name": "initial-membership", "mountPath": "/etc/kerosene/node-membership/manifest.json", "subPath": "manifest.json", "readOnly": True},
                {"name": "state-snapshot", "mountPath": "/etc/kerosene/node-state", "readOnly": True}],
            "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]}}}
    tor = {"name": "tor", "image": "kerosene-cell.invalid/tor:selected",
           "env": [{"name": "KEROSENE_TOR_IDENTITY_SOURCE", "value": "/etc/kerosene/tor-identity"},
                   {"name": "KEROSENE_TOR_HIDDEN_SERVICE_DIR", "value": "/var/lib/tor/node"},
                   {"name": "KEROSENE_TOR_ONION_PUBLISH_PATH", "value": "/onion/hostname"}],
           "readinessProbe": {"exec": {"command": ["sh", "-c", "test -f /tmp/tor-ready"]}},
           "volumeMounts": [{"name": "identity-data", "mountPath": "/var/lib/tor"},
                            {"name": "onion-public", "mountPath": "/onion"},
                            {"name": "tor-config", "mountPath": "/etc/tor/torrc", "subPath": "torrc", "readOnly": True},
                            {"name": "tor-identity", "mountPath": "/etc/kerosene/tor-identity", "readOnly": True}]}
    volumes = [
        {"name": "identity-data", "persistentVolumeClaim": {"claimName": name + "-data"}},
        {"name": "onion-public", "emptyDir": {}},
        {"name": "node-identity", "secret": {"secretName": name + "-identity", "defaultMode": 256, "items": mode_items(("identity.key",))}},
        {"name": "node-genesis", "configMap": {"name": "node-genesis"}},
        {"name": "node-mtls", "secret": {"secretName": name + "-mtls", "defaultMode": 256,
                                           "items": mode_items(("ca.crt", "client-identity.pem", "server.crt", "server.key"))}},
        {"name": "tor-config", "configMap": {"name": name + "-tor"}},
        {"name": "tor-identity", "secret": {"secretName": name + "-onion-identity", "defaultMode": 256,
                                              "items": mode_items(("hostname", "hs_ed25519_public_key", "hs_ed25519_secret_key"))}},
        {"name": "initial-membership", "configMap": {"name": f"node-{plane}-membership"}},
        {"name": "state-snapshot", "configMap": {"name": f"node-{plane}-state"}}]
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": name, "namespace": namespace, "labels": {"app": name}},
            "spec": {"replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": {"app": name}},
                     "template": {"metadata": {"labels": {"app": name}},
                                  "spec": {"automountServiceAccountToken": False,
                                           "containers": [tor, node], "volumes": volumes}}}}


def generate(genesis, planes, snapshots):
    network = genesis.get("network_id")
    if not isinstance(network, str) or not network:
        raise ArchiveError("genesis trust bundle lacks network_id")
    resources = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "kerosene-staging-vault"}}]
    for plane, namespace in (("bank", "kerosene-staging"), ("vault", "kerosene-staging-vault")):
        manifest = planes[plane]
        attestation, payload = snapshots[plane]
        endpoints = validate_manifest(manifest, network, plane)
        validate_attestation(attestation, network, plane, manifest, payload)
        resources.extend([
            config_map(namespace, "node-genesis", {"genesis-trust-bundle.json": canonical_bytes(genesis).decode()}),
            config_map(namespace, f"node-{plane}-bootstrap", {"genesis-endpoints": ",".join(endpoints), "mirrors": ""}),
            config_map(namespace, f"node-{plane}-membership", {"manifest.json": canonical_bytes(manifest).decode()}),
            config_map(namespace, f"node-{plane}-state", {"attestation.json": canonical_bytes(attestation).decode()},
                       {"snapshot.bin": base64.b64encode(payload).decode()})])
        for index in range(1, 4):
            name = f"node-{plane}-{index}"
            resources.extend([
                config_map(namespace, name + "-tor", {"torrc": lifecycle.NODE_TORRC}),
                {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
                 "metadata": {"name": name + "-data", "namespace": namespace},
                 "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "2Gi"}}}},
                node_workload(name, namespace, plane, network)])
    return {"apiVersion": "v1", "kind": "List", "items": resources}


def write(resources, output):
    write_new(output, canonical_bytes(resources) + b"\n")
