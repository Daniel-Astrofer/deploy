#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).with_name("stack-vault-rebuild-provenance-test.py")
SPEC = importlib.util.spec_from_file_location("vault_rebuild_qualification", MODULE_PATH)
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)


class VaultRebuildQualificationTest(unittest.TestCase):
    def test_normalized_spdx_ignores_document_identity_and_orders_packages(self):
        first = {
            "spdxVersion": "SPDX-2.3",
            "name": "generated-name-one",
            "packages": [
                {"name": "zlib", "versionInfo": "1.2", "externalRefs": []},
                {"name": "app", "versionInfo": "1", "externalRefs": [{
                    "referenceType": "purl", "referenceLocator": "pkg:generic/app@1"}]},
            ],
        }
        second = json.loads(json.dumps(first))
        second["name"] = "generated-name-two"
        second["packages"].reverse()
        self.assertEqual(qualification.normalized_spdx(first), qualification.normalized_spdx(second))

    def test_normalized_spdx_rejects_empty_or_wrong_schema(self):
        for document in ({"spdxVersion": "SPDX-2.2", "packages": []},
                         {"spdxVersion": "SPDX-2.3", "packages": [{}]}):
            with self.subTest(document=document), self.assertRaises(RuntimeError):
                qualification.normalized_spdx(document)

    def test_digest_contract_is_canonical(self):
        digest = qualification.sha256(b"vault")
        self.assertRegex(digest, qualification.DIGEST)
        self.assertEqual(digest, "sha256:" + "e6f0a1fbb43c89196dcfcbef85908f19ab4c5f7cc4f4c452284697757683d7ef")


if __name__ == "__main__":
    unittest.main()
