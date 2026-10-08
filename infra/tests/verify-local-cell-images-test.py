#!/usr/bin/env python3

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("verify_local_cell_images", str(ROOT / "verify-local-cell-images"))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class Response:
    def __init__(self, digest):
        self.headers = {"Docker-Content-Digest": digest}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class VerifyLocalCellImagesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "images.json"
        self.digest = "sha256:" + "a" * 64
        self.document = {
            "schema": "kerosene.cell-images/v1",
            "services": {
                name: {"image": f"localhost:5000/kerosene/{name}@{self.digest}"}
                for name in module.SERVICES
            },
        }

    def tearDown(self):
        self.temporary.cleanup()

    def write(self):
        self.path.write_text(json.dumps(self.document), encoding="utf-8")

    def test_verifies_exact_inventory_and_registry_digest(self):
        self.write()
        with patch.object(module.urllib.request, "urlopen", return_value=Response(self.digest)) as call:
            self.assertEqual(set(module.verify(self.path)), module.SERVICES)
        self.assertEqual(call.call_count, 11)

    def test_rejects_missing_service_before_registry_access(self):
        self.document["services"].pop("vault")
        self.write()
        with patch.object(module.urllib.request, "urlopen") as call:
            with self.assertRaisesRegex(ValueError, "exactly the eleven"):
                module.verify(self.path)
        call.assert_not_called()

    def test_rejects_mutable_reference(self):
        self.document["services"]["vault"]["image"] = "localhost:5000/kerosene/vault:complete-cell"
        self.write()
        with self.assertRaisesRegex(ValueError, "sha256 reference"):
            module.verify(self.path)

    def test_rejects_registry_digest_mismatch(self):
        self.write()
        with patch.object(module.urllib.request, "urlopen", return_value=Response("sha256:" + "b" * 64)):
            with self.assertRaisesRegex(ValueError, "registry digest differs"):
                module.verify(self.path)

    def test_rejects_duplicate_json_fields(self):
        self.path.write_text('{"schema":"kerosene.cell-images/v1","schema":"duplicate"}',
                             encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate JSON field"):
            module.verify(self.path)


if __name__ == "__main__":
    unittest.main()
