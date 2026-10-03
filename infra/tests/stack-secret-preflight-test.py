#!/usr/bin/env python3
"""Secret metadata/key projection tests; no actual credential material."""
import copy
from contextlib import redirect_stdout
import io
import json
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("tested_secret_stack", str(ROOT / "kerosene-stack"))
spec = importlib.util.spec_from_loader(loader.name, loader)
stack = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stack
loader.exec_module(stack)
lifecycle = stack.lifecycle


class SecretPreflightTest(unittest.TestCase):
    def setUp(self):
        self.pod = {"containers": [{"name": "core", "env": [{"valueFrom": {"secretKeyRef": {"name": "database", "key": "password"}}}],
                                    "envFrom": [{"secretRef": {"name": "session"}}]}],
                    "initContainers": [{"name": "init", "env": [{"valueFrom": {"secretKeyRef": {"name": "database", "key": "username"}}}]}],
                    "volumes": [{"secret": {"secretName": "tls", "items": [{"key": "tls.crt"}, {"key": "tls.key"}]}},
                                {"projected": {"sources": [{"secret": {"name": "identity", "items": [{"key": "identity.key"}]}}]}}],
                    "imagePullSecrets": [{"name": "registry"}]}
        self.artifact = {"resources": [{"kind": "Deployment", "metadata": {"namespace": "kerosene-staging"},
                                      "spec": {"template": {"spec": self.pod}}}]}

    def references(self):
        return lifecycle.required_secret_references(stack, self.artifact)

    def test_all_supported_external_secret_paths_and_init_containers(self):
        self.assertEqual(self.references(), {("kerosene-staging", "database"): {"password", "username"},
                                            ("kerosene-staging", "session"): set(), ("kerosene-staging", "tls"): {"tls.crt", "tls.key"},
                                            ("kerosene-staging", "identity"): {"identity.key"}, ("kerosene-staging", "registry"): set()})

    def test_same_name_in_different_namespaces_is_not_merged(self):
        second = copy.deepcopy(self.artifact["resources"][0])
        second["metadata"]["namespace"] = "kerosene-staging-vault"
        self.artifact["resources"].append(second)
        self.assertEqual(len(self.references()), 10)

    def test_optional_reference_does_not_make_other_required_reference_optional(self):
        self.pod["initContainers"][0]["env"][0]["valueFrom"]["secretKeyRef"]["optional"] = True
        self.pod["containers"][0]["envFrom"][0]["secretRef"]["optional"] = True
        references = self.references()
        self.assertEqual(references[("kerosene-staging", "database")], {"password"})
        self.assertNotIn(("kerosene-staging", "session"), references)

    def test_invalid_key_and_nonboolean_optional_rejected(self):
        reference = self.pod["containers"][0]["env"][0]["valueFrom"]["secretKeyRef"]
        for key in ["", None, "../password", "secret\nvalue", "x" * 254]:
            reference["key"] = key
            with self.subTest(key=key), self.assertRaises(stack.ApplyBlockedError):
                self.references()
        reference["key"] = "password"
        for optional in [1, "true", None]:
            reference["optional"] = optional
            with self.subTest(optional=optional), self.assertRaises(stack.ApplyBlockedError):
                self.references()

    def test_probe_emits_only_name_and_key_projection_with_explicit_cluster(self):
        prefix = ["/kubectl", "--kubeconfig", "/private/cell.conf", "--context", "cell-a"]
        with patch.object(lifecycle, "run", return_value=b"database\npassword\nusername\n") as run:
            lifecycle.verify_external_secrets(stack, prefix, {("kerosene-staging", "database"): {"username", "password"}})
            command = run.call_args.args[0]
            self.assertEqual(command[:5], prefix)
            self.assertEqual(command[5:-1], ["-n", "kerosene-staging", "get", "secret", "database", "-o"])
            self.assertTrue(command[-1].startswith("go-template="))
            self.assertNotIn("{{$value}}", command[-1])
            self.assertNotIn("base64", command[-1])

    def test_missing_keys_bad_identity_oversize_and_invalid_encoding_fail_closed(self):
        for output in [b"", b"other-secret\npassword\n", b"database\nusername\n", b"database\npassword\npassword\n", b"\xff", b"x" * 65537]:
            with self.subTest(output=output[:30]), patch.object(lifecycle, "run", return_value=output), self.assertRaisesRegex(stack.ApplyBlockedError, "unavailable or incomplete"):
                lifecycle.verify_external_secrets(stack, ["/kubectl"], {("kerosene-staging", "database"): {"password"}})

    def test_api_failure_does_not_echo_potential_credential_material(self):
        with patch.object(lifecycle, "run", side_effect=RuntimeError("synthetic-sensitive-response")):
            with self.assertRaises(stack.ApplyBlockedError) as raised:
                lifecycle.verify_external_secrets(stack, ["/kubectl"], {("kerosene-staging", "database"): set()})
            self.assertNotIn("synthetic-sensitive-response", str(raised.exception))

    def test_empty_reference_inventory_never_accesses_cluster(self):
        with patch.object(lifecycle, "run") as run:
            lifecycle.verify_external_secrets(stack, ["/kubectl"], {})
            run.assert_not_called()

    def test_preflight_reports_credential_reference_verification_separately(self):
        args = stack.build_parser().parse_args(["preflight", "--cell-dir", "/unit-cell", "--release", "unit.json", "--deployment-manifest", "unit-deployment.json"])
        config = {"cellId": "unit", "trustDigests": {}}
        output = io.StringIO()
        with patch.object(lifecycle, "load_config", return_value=config), patch.object(lifecycle, "verify_bootstrap_trust"), patch.object(stack, "load_and_validate", return_value=({}, {})), patch.object(lifecycle, "verify_deployment", return_value=self.artifact), patch.object(lifecycle.shutil, "which", return_value="/unit-tool"), patch.object(lifecycle, "kubectl_command", return_value=["/bound-kubectl"]), patch.object(lifecycle, "run", return_value=b"ok"), patch.object(lifecycle, "verify_external_secrets") as secrets, redirect_stdout(output):
            self.assertEqual(lifecycle.command_preflight(stack, args), 0)
            secrets.assert_called_once_with(stack, ["/bound-kubectl"], self.references())
        result = json.loads(output.getvalue())
        self.assertTrue(result["externalSecretReferencesVerified"])
        self.assertEqual(result["externalSecretReferenceCount"], 5)
        self.assertFalse(result["financialReadinessVerified"])
        self.assertFalse(result["applyQualified"])

    def test_preflight_requires_manifest_and_release_together(self):
        for flags in [["--release", "unit.json"], ["--deployment-manifest", "unit.json"]]:
            args = stack.build_parser().parse_args(["preflight", "--cell-dir", "/unit-cell", *flags])
            with patch.object(lifecycle, "load_config", return_value={"cellId": "unit", "trustDigests": {}}), patch.object(lifecycle, "verify_bootstrap_trust"), patch.object(lifecycle, "run") as run:
                with self.assertRaisesRegex(stack.ReleaseValidationError, "together"):
                    lifecycle.command_preflight(stack, args)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
