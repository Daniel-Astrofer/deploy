"""Generate three independent production Vault/Tor workloads."""

import re
from archive import ArchiveError, canonical_bytes, write_new
import lifecycle


VAULT_TORRC = lifecycle.VAULT_TORRC


def mode_items(keys):
    return [{"key": key, "path": key, "mode": 256} for key in keys]


def workload(name, network, measurement):
    runtime = name + "-runtime"
    literals = {
        "KEROSENE_ENV": "production", "VAULT_CEREMONY_MODE": "production",
        "VAULT_AUTH_MODE": "mtls", "VAULT_TRANSPORT": "tor",
        "VAULT_DKG_MODE": "distributed_wire", "VAULT_NODE_TIER": "domestic",
        "ATTESTATION_MODE": "software", "VAULT_LISTEN_ADDR": "127.0.0.1:7801",
        "VAULT_GENESIS_N": "3", "VAULT_TLS_VERIFY_MODE": "onion_or_spiffe",
        "BITCOIN_NETWORK": "testnet3", "VAULT_SOCKS_PROXY": "socks5h://127.0.0.1:9050",
        "VAULT_SHARE_STORE": "aead_disk", "VAULT_DATA_DIR": "/var/lib/kerosene-vault",
        "VAULT_MEASUREMENT_PIN": measurement, "VAULT_NODE_ID": name,
        "VAULT_TLS_CERT_PATH": "/etc/kerosene/vault-mtls/server.crt",
        "VAULT_TLS_KEY_PATH": "/etc/kerosene/vault-mtls/server.key",
        "VAULT_TLS_CLIENT_CA_PATH": "/etc/kerosene/vault-mtls/ca.crt",
        "VAULT_TLS_CLIENT_CERT_PATH": "/etc/kerosene/vault-mtls/client.crt",
        "VAULT_TLS_CLIENT_KEY_PATH": "/etc/kerosene/vault-mtls/client.key",
        "VAULT_KEROSENE_NETWORK_ID": network,
        "VAULT_KEROSENE_NODE_CLIENT_IDENTITY_PEM": "/etc/kerosene/vault-mtls/client-identity.pem",
        "VAULT_KEROSENE_NODE_CA_PATH": "/etc/kerosene/vault-mtls/ca.crt"}
    protected = (("VAULT_SEED_PEERS", "seed-peers"),
                 ("VAULT_AUDIT_PUBKEY_ALLOWLIST", "audit-pubkeys"),
                 ("VAULT_TLS_PEER_SPIFFE_ID", "tls-peer-spiffe-ids"),
                 ("VAULT_ATTESTATION_ROOT", "attestation-root"),
                 ("VAULT_DATA_PASSPHRASE", "data-passphrase"),
                 ("VAULT_KEROSENE_NODE_URL", "node-url"))
    vault = {"name": "vault", "image": "kerosene-cell.invalid/vault:selected",
             "env": ([{"name": key, "value": value} for key, value in literals.items()] +
                     [{"name": key, "valueFrom": {"secretKeyRef": {"name": runtime, "key": secret_key}}}
                      for key, secret_key in protected] +
                     [{"name": "VAULT_HEALTH_PROBE_URL", "valueFrom": {
                         "configMapKeyRef": {"name": "vault-probe", "key": "url"}}}]),
             "readinessProbe": {"exec": {"command": ["/usr/local/bin/kerosene-vault", "--health-probe"]},
                                "timeoutSeconds": 6},
             "volumeMounts": [{"name": "identity-data", "mountPath": "/var/lib/kerosene-vault"},
                              {"name": "vault-mtls", "mountPath": "/etc/kerosene/vault-mtls", "readOnly": True}],
             "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                 "capabilities": {"drop": ["ALL"]}}}
    tor = {"name": "tor", "image": "kerosene-cell.invalid/tor:selected",
           "env": [{"name": "KEROSENE_TOR_IDENTITY_SOURCE", "value": "/etc/kerosene/tor-identity"},
                   {"name": "KEROSENE_TOR_HIDDEN_SERVICE_DIR", "value": "/var/lib/tor/vault"},
                   {"name": "KEROSENE_TOR_ONION_PUBLISH_PATH", "value": "/onion/hostname"}],
           "readinessProbe": {"exec": {"command": ["sh", "-c", "test -f /tmp/tor-ready"]}},
           "volumeMounts": [{"name": "identity-data", "mountPath": "/var/lib/tor"},
                            {"name": "onion-public", "mountPath": "/onion"},
                            {"name": "tor-config", "mountPath": "/etc/tor/torrc", "subPath": "torrc", "readOnly": True},
                            {"name": "tor-identity", "mountPath": "/etc/kerosene/tor-identity", "readOnly": True}]}
    volumes = [
        {"name": "identity-data", "persistentVolumeClaim": {"claimName": name + "-data"}},
        {"name": "onion-public", "emptyDir": {}},
        {"name": "vault-mtls", "secret": {"secretName": name + "-mtls", "defaultMode": 256,
                                            "items": mode_items(("ca.crt", "client-identity.pem", "client.crt", "client.key", "server.crt", "server.key"))}},
        {"name": "tor-config", "configMap": {"name": name + "-tor"}},
        {"name": "tor-identity", "secret": {"secretName": name + "-onion-identity", "defaultMode": 256,
                                              "items": mode_items(("hostname", "hs_ed25519_public_key", "hs_ed25519_secret_key"))}}]
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": name, "namespace": "kerosene-staging-vault", "labels": {"app": name}},
            "spec": {"replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": {"app": name}},
                     "template": {"metadata": {"labels": {"app": name}},
                                  "spec": {"automountServiceAccountToken": False,
                                           "containers": [tor, vault], "volumes": volumes}}}}


def generate(network, measurement):
    if not isinstance(network, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", network):
        raise ArchiveError("invalid Vault network ID")
    if not isinstance(measurement, str) or not re.fullmatch(r"[0-9a-f]{64}", measurement):
        raise ArchiveError("Vault software measurement must be 64 lowercase hex")
    resources = [{"apiVersion": "v1", "kind": "ConfigMap", "immutable": True,
                  "metadata": {"name": "vault-probe", "namespace": "kerosene-staging-vault"},
                  "data": {"url": "https://localhost:7801/v1/local-health"}}]
    for index in range(1, 4):
        name = f"vault-{index}"
        resources.extend([
            {"apiVersion": "v1", "kind": "ConfigMap", "immutable": True,
             "metadata": {"name": name + "-tor", "namespace": "kerosene-staging-vault"},
             "data": {"torrc": VAULT_TORRC}},
            {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
             "metadata": {"name": name + "-data", "namespace": "kerosene-staging-vault"},
             "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "2Gi"}}}},
            workload(name, network, measurement)])
    return {"apiVersion": "v1", "kind": "List", "items": resources}


def write(resources, output):
    write_new(output, canonical_bytes(resources) + b"\n")
