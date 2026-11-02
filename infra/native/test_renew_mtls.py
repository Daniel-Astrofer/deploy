import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

import renew_mtls as renewal


def openssl(*args):
    subprocess.run(["openssl", *map(str, args)], check=True, capture_output=True)


class CertificateTests(unittest.TestCase):
    def test_renewal_preserves_key_subject_and_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ca, ca_key = root / "ca.crt", root / "ca.key"
            key, request, cert, new = (root / name for name in ("server.key", "server.csr", "server.crt", "new.crt"))
            openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "10",
                    "-subj", "/CN=test CA", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                    "-keyout", ca_key, "-out", ca)
            openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=vault-1",
                    "-keyout", key, "-out", request)
            extensions = root / "extensions"
            extensions.write_text("basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                                  "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:vault-1,IP:127.0.0.1,URI:spiffe://test/vault/1\n")
            openssl("x509", "-req", "-in", request, "-CA", ca, "-CAkey", ca_key,
                    "-days", "1", "-extfile", extensions, "-out", cert)
            renewal.issue_leaf(cert, key, ca, ca_key, new, "sslserver")
            self.assertTrue(renewal.is_fresh(new, renewal.RENEW_BEFORE_SECONDS))
            self.assertTrue(renewal.key_matches(new, key))
            self.assertEqual(renewal.cert_details(cert), renewal.cert_details(new))

    def test_rejects_wrong_key_before_issuing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ca, ca_key, wrong = root / "ca.crt", root / "ca.key", root / "wrong.key"
            openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3",
                    "-subj", "/CN=test", "-keyout", ca_key, "-out", ca)
            openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", wrong)
            output = root / "must-not-exist.crt"
            with self.assertRaises(renewal.RenewalError):
                renewal.issue_leaf(ca, wrong, ca, ca_key, output, "sslclient")
            self.assertFalse(output.exists())

    def test_strict_validation_rejects_ca_without_key_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ca, ca_key, leaf, key, request = (root / name for name in
                                              ("ca.crt", "ca.key", "leaf.crt", "leaf.key", "leaf.csr"))
            openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "10",
                    "-subj", "/CN=test CA", "-keyout", ca_key, "-out", ca)
            openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=leaf",
                    "-keyout", key, "-out", request)
            ext = root / "extensions"
            ext.write_text("basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                           "extendedKeyUsage=clientAuth\nsubjectAltName=URI:spiffe://test/client\n")
            openssl("x509", "-req", "-in", request, "-CA", ca, "-CAkey", ca_key,
                    "-days", "1", "-extfile", ext, "-out", leaf)
            with self.assertRaisesRegex(renewal.RenewalError, "strict certificate-chain"):
                renewal.verify_chain_strict(leaf, ca)

    def test_configured_vault_peers_do_not_imply_financial_readiness(self):
        node = {"local_ready": True, "quorum_ready": True, "live_members": 3}
        vault = {"local_ready": True, "peer_count": 2, "financial_ready": False}
        with mock.patch.object(renewal, "get_json", side_effect=[node, vault]):
            self.assertFalse(renewal.member_health(1, object()))
        vault["financial_ready"] = True
        with mock.patch.object(renewal, "get_json", side_effect=[node, vault]):
            self.assertTrue(renewal.member_health(1, object()))
        node["live_members"] = 2
        with mock.patch.object(renewal, "get_json", side_effect=[node, vault]):
            self.assertFalse(renewal.member_health(1, object()))


class SourceAndRecoveryTests(unittest.TestCase):
    def source(self, cert):
        return yaml.safe_dump_all([
            {"kind": "Secret", "metadata": {"name": "unrelated"}, "stringData": {"value": "keep"}},
            {"kind": "Secret", "metadata": {"name": renewal.SECRET_NAME, "namespace": renewal.NAMESPACE},
             "stringData": {"ca.crt": "CA\n", "client.crt": cert, "client.key": "KEY\n"}},
        ], explicit_start=True, sort_keys=False)

    def secret(self, cert):
        return {"data": {key: base64.b64encode(value.encode()).decode() for key, value in
                         {"ca.crt": "CA\n", "client.crt": cert, "client.key": "KEY\n"}.items()}}

    def test_only_target_secret_changes(self):
        original = self.source("OLD\n")
        updated, selected = renewal.source_update(original, "OLD\n", "NEW\n")
        before, after = list(yaml.safe_load_all(original)), list(yaml.safe_load_all(updated))
        self.assertEqual(before[0], after[0])
        self.assertEqual(after[1]["stringData"]["client.crt"], "NEW\n")
        self.assertEqual(selected, after[1])
        with self.assertRaises(renewal.RenewalError):
            renewal.source_update(original, "WRONG\n", "NEW\n")

    def recovery_fixture(self, root):
        state, ops = root / "state", root / "ops"
        (state / "run").mkdir(parents=True)
        backup = state / "cert-backups/native-mtls-test"
        (backup / "kfe").mkdir(parents=True)
        ops.mkdir()
        (backup / "kfe/client.crt").write_text("OLD\n")
        (backup / "new-kfe-client.crt").write_text("NEW\n")
        (backup / "native-application-secrets.yaml").write_text(self.source("OLD\n"))
        (ops / "native-application-secrets.yaml").write_text(
            renewal.source_update(self.source("OLD\n"), "OLD\n", "NEW\n")[0])
        ca, key, local = root / "ca.crt", root / "key", root / "client.crt"
        ca.write_text("CA\n"); key.write_text("KEY\n"); local.write_text("NEW\n")
        marker = state / "run/native-mtls-kfe-rollout.pending"
        marker.write_text(json.dumps({"backup": str(backup)}))
        return state, ops, ca, local, key, marker

    def test_recovery_restores_local_files_if_secret_was_not_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            state, ops, ca, local, key, marker = self.recovery_fixture(Path(directory))
            with mock.patch.object(renewal, "kube_secret", return_value=self.secret("OLD\n")):
                renewal.recover_pending(state, ops, ca, local, key)
            self.assertEqual(local.read_text(), "OLD\n")
            self.assertEqual((ops / "native-application-secrets.yaml").read_text(), self.source("OLD\n"))
            self.assertFalse(marker.exists())

    def test_recovery_finishes_rollout_if_secret_was_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            state, ops, ca, local, key, marker = self.recovery_fixture(Path(directory))
            local.write_text("OLD\n")
            with mock.patch.object(renewal, "kube_secret", return_value=self.secret("NEW\n")), \
                    mock.patch.object(renewal, "rollout_kfe", side_effect=lambda path: path.unlink()) as rollout:
                renewal.recover_pending(state, ops, ca, local, key)
            self.assertEqual(local.read_text(), "NEW\n")
            self.assertEqual(list(yaml.safe_load_all((ops / "native-application-secrets.yaml").read_text()))[1]
                             ["stringData"]["client.crt"], "NEW\n")
            rollout.assert_called_once()
            self.assertFalse(marker.exists())

    def test_recovery_preserves_concurrent_operator_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            state, ops, ca, local, key, marker = self.recovery_fixture(Path(directory))
            source = ops / "native-application-secrets.yaml"
            source.write_text(source.read_text() + "# operator change\n")
            before = source.read_text()
            with mock.patch.object(renewal, "kube_secret", return_value=self.secret("OLD\n")):
                with self.assertRaises(renewal.RenewalError):
                    renewal.recover_pending(state, ops, ca, local, key)
            self.assertEqual(source.read_text(), before)
            self.assertTrue(marker.exists())


class RotationFlowTests(unittest.TestCase):
    def test_complete_rotation_is_serial_and_updates_only_expected_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, ops = root / "state", root / "ops"
            certs = state / "ceremony-certs"
            (state / "run").mkdir(parents=True)
            (certs / "kfe").mkdir(parents=True)
            ops.mkdir()
            ca, ca_key = certs / "ca.crt", certs / "ca.key"
            openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "10",
                    "-subj", "/CN=test CA", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                    "-keyout", ca_key, "-out", ca)
            openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", root / "key")
            for index in (1, 2, 3):
                (certs / f"nodes/vault-{index}").mkdir(parents=True)
                (state / f"node/member-{index}").mkdir(parents=True)
            paths = [(certs / f"nodes/vault-{index}/{kind}", kind)
                     for index in (1, 2, 3) for kind in ("server", "client")]
            paths.append((certs / "kfe/client", "client"))
            for number, (path, kind) in enumerate(paths):
                key, req, crt, ext = (path.with_suffix(suffix) for suffix in (".key", ".csr", ".crt", ".ext"))
                key.write_bytes((root / "key").read_bytes())
                openssl("req", "-new", "-key", key, "-subj", f"/CN=leaf-{number}", "-out", req)
                purpose = "serverAuth" if kind == "server" else "clientAuth"
                ext.write_text(f"basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                               f"extendedKeyUsage={purpose}\nsubjectAltName=IP:127.0.0.1,URI:spiffe://test/{number}\n")
                openssl("x509", "-req", "-in", req, "-CA", ca, "-CAkey", ca_key,
                        "-days", "1", "-extfile", ext, "-out", crt)
            for index in (1, 2, 3):
                identity = state / f"node/member-{index}/client-identity.pem"
                identity.write_bytes((certs / f"nodes/vault-{index}/client.crt").read_bytes()
                                     + (certs / f"nodes/vault-{index}/client.key").read_bytes())
            old_cert = (certs / "kfe/client.crt").read_text()
            source_secret = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                             "metadata": {"name": renewal.SECRET_NAME, "namespace": renewal.NAMESPACE},
                             "stringData": {"ca.crt": ca.read_text(), "client.crt": old_cert,
                                            "client.key": (certs / "kfe/client.key").read_text()}}
            source = ops / "native-application-secrets.yaml"
            source.write_text(yaml.safe_dump(source_secret, sort_keys=False))
            secret = {"metadata": {"name": renewal.SECRET_NAME, "resourceVersion": "1", "annotations": {}},
                      "data": {k: base64.b64encode(v.encode()).decode() for k, v in source_secret["stringData"].items()}}
            deployment = {"spec": {"template": {"spec": {"volumes": [{"name": "mtls", "secret": {"secretName": renewal.SECRET_NAME}}],
                          "containers": [{"name": "kfe-service", "volumeMounts": [{"name": "mtls", "mountPath": "/etc/vault-mtls"}]}]}}},
                          "status": {"availableReplicas": 1}}
            events = []
            original_run = renewal.run

            def fake_run(args, *, data=None, timeout=60):
                if args[0] == "openssl":
                    return original_run(args, data=data, timeout=timeout)
                if args[:2] == ["docker", "restart"]:
                    events.append(args[2])
                    return b""
                if args[0] == "kubectl" and "get" in args:
                    return json.dumps(deployment if "deployment" in args else secret).encode()
                if args[0] == "kubectl" and "replace" in args:
                    replacement = json.loads(data)
                    self.assertEqual(replacement["metadata"]["resourceVersion"], "1")
                    secret.clear(); secret.update(replacement)
                    events.append("secret-replace")
                    return b""
                if args[0] == "kubectl" and "rollout" in args:
                    if args[args.index("rollout") + 1] == "restart":
                        self.assertIn(f"--as={renewal.GITOPS_IDENTITY}", args)
                    events.append("rollout-" + args[args.index("rollout") + 1])
                    return b""
                self.fail(f"unexpected command: {args}")

            with mock.patch.object(renewal, "run", side_effect=fake_run), \
                    mock.patch.object(renewal, "verify_runtime"), \
                    mock.patch.object(renewal, "member_health", return_value=True), \
                    mock.patch.object(renewal, "wait_vault"), mock.patch.object(renewal, "wait_member"):
                renewal.renew(state, ops, force=True)
            self.assertEqual(events, ["kerosene-native-vault-1", "kerosene-native-node-1",
                                      "kerosene-native-vault-2", "kerosene-native-node-2",
                                      "kerosene-native-vault-3", "kerosene-native-node-3",
                                      "secret-replace", "rollout-restart", "rollout-status"])
            self.assertNotEqual((certs / "kfe/client.crt").read_text(), old_cert)
            renewal.verify_secret(secret, ca, certs / "kfe/client.crt", certs / "kfe/client.key")
            self.assertFalse((state / "run/native-mtls-kfe-rollout.pending").exists())

    def test_normal_renewal_rejects_expired_leaves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, ops = root / "state", root / "ops"
            certs = state / "ceremony-certs"
            (state / "run").mkdir(parents=True); (certs / "kfe").mkdir(parents=True); ops.mkdir()
            ca, ca_key = certs / "ca.crt", certs / "ca.key"
            openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "10",
                    "-subj", "/CN=test CA", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                    "-keyout", ca_key, "-out", ca)
            openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", root / "key")
            for index in (1, 2, 3):
                (certs / f"nodes/vault-{index}").mkdir(parents=True)
                (state / f"node/member-{index}").mkdir(parents=True)
            paths = [(certs / f"nodes/vault-{index}/{kind}", kind)
                     for index in (1, 2, 3) for kind in ("server", "client")]
            paths.append((certs / "kfe/client", "client"))
            for number, (path, kind) in enumerate(paths):
                key, req, crt, ext = (path.with_suffix(suffix) for suffix in (".key", ".csr", ".crt", ".ext"))
                key.write_bytes((root / "key").read_bytes())
                openssl("req", "-new", "-key", key, "-subj", f"/CN=leaf-{number}", "-out", req)
                purpose = "serverAuth" if kind == "server" else "clientAuth"
                ext.write_text(f"basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                               f"extendedKeyUsage={purpose}\nsubjectAltName=IP:127.0.0.1,URI:spiffe://test/{number}\n")
                openssl("x509", "-req", "-in", req, "-CA", ca, "-CAkey", ca_key,
                        "-not_before", "20260101000000Z", "-not_after", "20260102000000Z",
                        "-extfile", ext, "-out", crt)
            for index in (1, 2, 3):
                identity = state / f"node/member-{index}/client-identity.pem"
                identity.write_bytes((certs / f"nodes/vault-{index}/client.crt").read_bytes()
                                     + (certs / f"nodes/vault-{index}/client.key").read_bytes())
            source_secret = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                             "metadata": {"name": renewal.SECRET_NAME, "namespace": renewal.NAMESPACE},
                             "stringData": {"ca.crt": ca.read_text(), "client.crt": (certs / "kfe/client.crt").read_text(),
                                            "client.key": (certs / "kfe/client.key").read_text()}}
            (ops / "native-application-secrets.yaml").write_text(yaml.safe_dump(source_secret, sort_keys=False))
            raw_secret = {"metadata": {"name": renewal.SECRET_NAME, "resourceVersion": "1", "annotations": {}},
                          "data": {k: base64.b64encode(v.encode()).decode() for k, v in source_secret["stringData"].items()}}
            with mock.patch.object(renewal, "kube_secret", return_value=raw_secret):
                with self.assertRaisesRegex(renewal.RenewalError, "--recover-expired"):
                    renewal.renew(state, ops, force=False)

if __name__ == "__main__":
    unittest.main()
