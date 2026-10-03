#!/usr/bin/env python3
"""Explicit isolated loopback PostgreSQL registry lab; retains its database."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

SQL = Path(__file__).resolve().parents[1] / "runtime/postgres/cell-admission-registry.sql"
GRANTS = SQL.with_name("cell-admission-registry-grants.sql")


@unittest.skipUnless(os.environ.get("CELL_DATABASE_DISPOSABLE") == "true", "disposable lab required")
class RegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = os.environ["CELL_DATABASE_PGHOST"]
        cls.port = os.environ["CELL_DATABASE_PGPORT"]
        if cls.host != "127.0.0.1" or not re.fullmatch(r"[0-9]{1,5}", cls.port) or not 0 < int(cls.port) < 65536:
            raise RuntimeError("explicit loopback PostgreSQL required")
        cls.database = "cell_admission_lab_" + uuid.uuid4().hex[:16]
        result = cls.query("CREATE DATABASE " + cls.database, database="postgres")
        if result.returncode:
            raise RuntimeError("synthetic registry database creation failed")
        result = cls.query(SQL.read_text())
        if result.returncode:
            raise RuntimeError("synthetic registry provisioning failed")
        cls.service_role = "cell_registry_user_" + uuid.uuid4().hex[:16]
        cls.service_password = "synthetic-registry-test-only"
        cls.password_file = None
        if os.environ.get("CELL_REGISTRY_TLS_CA"):
            with tempfile.NamedTemporaryFile(dir=Path(os.environ["CELL_REGISTRY_TLS_CA"]).parent,
                    prefix="synthetic-registry-password-", delete=False) as secret:
                secret.write(cls.service_password.encode())
                cls.password_file = secret.name
        if cls.query(f"CREATE ROLE {cls.service_role} LOGIN NOINHERIT PASSWORD '{cls.service_password}'").returncode:
            raise RuntimeError("synthetic registry role creation failed")
        cls.grants_sql = f"\\set registry_database {cls.database}\n\\set service_role {cls.service_role}\n" + GRANTS.read_text()
        if cls.query(cls.grants_sql).returncode:
            raise RuntimeError("synthetic registry role grants failed")

    @classmethod
    def query(cls, sql, database=None, service=False):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "CELL_DATABASE_"))}
        env.update(PGPASSWORD=cls.service_password if service else os.environ["CELL_DATABASE_ADMIN_PASSWORD"], PGCONNECT_TIMEOUT="5",
                   PGOPTIONS="-c statement_timeout=10000")
        if os.environ.get("CELL_REGISTRY_TLS_CA"):
            env.update(PGSSLMODE="verify-full", PGSSLROOTCERT=os.environ["CELL_REGISTRY_TLS_CA"],
                       PGSSLCERT=os.environ["CELL_REGISTRY_TLS_CERT"], PGSSLKEY=os.environ["CELL_REGISTRY_TLS_KEY"])
        return subprocess.run(["psql", "-X", "-q", "-tA", "--no-password", "-v", "ON_ERROR_STOP=1",
            "-h", cls.host, "-p", cls.port, "-U", cls.service_role if service else os.environ["CELL_DATABASE_ADMIN_USER"], "-d", database or cls.database],
            input=sql, env=env, capture_output=True, text=True, timeout=20)

    def insert(self, network, nonce, cell, cluster, epoch=1, digest=None):
        digest = digest or "sha256:" + uuid.uuid4().hex * 2
        return self.query("INSERT INTO cell_admission.consumed "
            "(network_id,epoch,nonce,cell_id,cluster_uid,operator_id,change_id,approval_digest,admission_digest,issued_at_unix_seconds,expires_at_unix_seconds) "
            f"VALUES ('{network}',{epoch},'{nonce}','{cell}','{cluster}','operator-example','change-example',"
            f"'sha256:{'a' * 64}','{digest}',1000,1100)", service=True)

    def test_service_cannot_erase_modify_or_override_consumption_clock(self):
        self.assertEqual(self.query("SELECT count(*) FROM cell_admission.consumed", service=True).returncode, 0)
        for sql in ("DELETE FROM cell_admission.consumed", "TRUNCATE cell_admission.consumed",
                    "UPDATE cell_admission.consumed SET nonce=nonce", "DROP TABLE cell_admission.consumed",
                    "CREATE TABLE cell_admission.forbidden(id int)",
                    "INSERT INTO cell_admission.consumed(consumed_at) VALUES (now())"):
            result = self.query(sql, service=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue("permission denied" in result.stderr or "must be owner" in result.stderr, result.stderr)

    def test_actual_go_consumer_uses_restricted_role(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "CELL_DATABASE_", "CELL_ADMISSION_REGISTRY_"))}
        env.update(CELL_ADMISSION_REGISTRY_DISPOSABLE="true", CELL_ADMISSION_REGISTRY_PORT=self.port,
                   CELL_ADMISSION_REGISTRY_USER=self.service_role, CELL_ADMISSION_REGISTRY_DATABASE=self.database,
                   CELL_ADMISSION_REGISTRY_PASSWORD=self.service_password)
        if self.password_file:
            env.update(CELL_ADMISSION_REGISTRY_TLS_CA=os.environ["CELL_REGISTRY_TLS_CA"],
                       CELL_ADMISSION_REGISTRY_TLS_CERT=os.environ["CELL_REGISTRY_TLS_CERT"],
                       CELL_ADMISSION_REGISTRY_TLS_KEY=os.environ["CELL_REGISTRY_TLS_KEY"],
                       CELL_ADMISSION_REGISTRY_PASSWORD_FILE=self.password_file)
        result = subprocess.run(["go", "test", "-mod=readonly", "-race", "-count=1", "-run", "^Test(AdmissionRegistryPostgres|RealConsensusSignaturesAndCommittedAppState)$", "./..."],
                                cwd=SQL.parents[2] / "governance", env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_concurrent_consumption_has_one_winner(self):
        nonce = uuid.uuid4().hex * 2
        cluster = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: self.insert("bank-race", nonce, "cell-race", cluster), range(2)))
        self.assertEqual(sum(r.returncode == 0 for r in results), 1)
        rejected = next(r for r in results if r.returncode)
        self.assertIn("duplicate key", rejected.stderr)

    def test_consumption_survives_new_sessions_and_conflicting_reassignment(self):
        nonce = uuid.uuid4().hex * 2
        cluster = str(uuid.uuid4())
        self.assertEqual(self.insert("bank-durable", nonce, "cell-durable", cluster).returncode, 0)
        for new_nonce, cell, target, epoch in [(nonce, "other-cell", str(uuid.uuid4()), 1),
                (uuid.uuid4().hex * 2, "cell-durable", str(uuid.uuid4()), 2),
                (uuid.uuid4().hex * 2, "other-cell", cluster, 2)]:
            result = self.insert("bank-durable", new_nonce, cell, target, epoch)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate key", result.stderr)
        result = self.query("SELECT count(*) FROM cell_admission.consumed WHERE network_id='bank-durable'")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "1"))

    def test_reprovisioning_refuses_existing_registry(self):
        before = self.query("SELECT count(*) FROM cell_admission.consumed").stdout
        result = self.query(SQL.read_text())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertEqual(self.query("SELECT count(*) FROM cell_admission.consumed").stdout, before)


if __name__ == "__main__":
    unittest.main()
