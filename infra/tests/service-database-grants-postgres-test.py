#!/usr/bin/env python3
"""Opt-in loopback lab: approved local JARs, distinct roles/databases, no cleanup."""
import os
from pathlib import Path
import re
import subprocess
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "runtime/postgres/service-runtime-grants.sql"
CREATE_SQL = ROOT / "runtime/postgres/create-service-databases.sql"


@unittest.skipUnless(os.environ.get("CELL_DATABASE_DISPOSABLE") == "true", "explicit disposable lab required")
class DatabaseGrantsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = os.environ["CELL_DATABASE_PGHOST"]
        cls.port = os.environ["CELL_DATABASE_PGPORT"]
        if cls.host != "127.0.0.1" or not re.fullmatch(r"[0-9]{1,5}", cls.port) or not 0 < int(cls.port) < 65536:
            raise RuntimeError("Only an explicitly bound loopback PostgreSQL lab is allowed")
        cls.admin = os.environ["CELL_DATABASE_ADMIN_USER"]
        cls.admin_password = os.environ["CELL_DATABASE_ADMIN_PASSWORD"]
        cls.password = "synthetic-cell-grants-test-only"
        suffix = uuid.uuid4().hex[:16]
        cls.bindings = {}
        for service in ("core", "kfe"):
            database = f"cell_grants_{service}_{suffix}"
            owner = f"cell_{service}_migration_{suffix}"
            runtime = f"cell_{service}_runtime_{suffix}"
            cls.query("postgres", cls.admin, cls.admin_password,
                      f"CREATE ROLE {owner} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{cls.password}'; "
                      f"CREATE ROLE {runtime} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{cls.password}';")
            jar = Path(os.environ[f"CELL_DATABASE_{service.upper()}_JAR"])
            if not jar.is_absolute() or jar.is_symlink() or not jar.is_file():
                raise RuntimeError("Explicit regular local executable JAR is required")
            cls.bindings[service] = (database, owner, runtime, jar)
        cls.create_databases()
        for service, (database, owner, runtime, jar) in cls.bindings.items():
            # No runtime connection is admitted until migrations and grants finish.
            cls.query(database, runtime, cls.password, "SELECT 1", success=False)
            env = cls.environment(cls.password)
            env.update(SPRING_DATASOURCE_URL=f"jdbc:postgresql://{cls.host}:{cls.port}/{database}",
                       SPRING_DATASOURCE_USERNAME=owner, SPRING_DATASOURCE_PASSWORD=cls.password)
            result = subprocess.run(["java", "-jar", str(jar), "--cell-migration=migrate"],
                                    env=env, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise RuntimeError(f"Synthetic {service} migration failed; no application was started")
            cls.grants(service)

    def test_fresh_databases_contain_complete_successful_owned_migration_history(self):
        for service, count, version in (("core", 14, "14"), ("kfe", 59, "58")):
            database, owner, runtime, _ = self.bindings[service]
            with self.subTest(service=service):
                self.assertEqual(self.query(database, runtime, self.password,
                    "SELECT count(*) FROM public.flyway_schema_history WHERE success"), str(count))
                self.assertEqual(self.query(database, runtime, self.password,
                    "SELECT count(*) FROM public.flyway_schema_history WHERE NOT success"), "0")
                self.assertEqual(self.query(database, runtime, self.password,
                    "SELECT version FROM public.flyway_schema_history ORDER BY installed_rank DESC LIMIT 1"), version)
                self.assertEqual(self.query(database, self.admin, self.admin_password,
                    "SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename='flyway_schema_history'"), owner)

    @classmethod
    def environment(cls, password):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("PG", "CELL_DATABASE_", "SPRING_DATASOURCE_"))}
        env.update(PGPASSWORD=password, PGCONNECT_TIMEOUT="5", PGOPTIONS="-c statement_timeout=10000")
        return env

    @classmethod
    def query(cls, database, user, password, sql, success=True):
        result = subprocess.run(["psql", "-X", "-q", "-tA", "--no-password", "-v", "ON_ERROR_STOP=1",
                                 "-h", cls.host, "-p", cls.port, "-U", user, "-d", database],
                                input=sql, env=cls.environment(password), capture_output=True, text=True, timeout=15)
        if success and result.returncode != 0:
            raise AssertionError("Synthetic database command unexpectedly failed: " + result.stderr)
        if not success and result.returncode == 0:
            raise AssertionError("Forbidden synthetic database operation succeeded")
        if not success and "permission denied" not in result.stderr.lower():
            raise AssertionError("Denial was not a PostgreSQL permission failure: " + result.stderr)
        return result.stdout.strip()

    @classmethod
    def grants(cls, service, expected=None, success=True, target=None):
        database, owner, runtime, _ = cls.bindings[service]
        database = target or database
        result = subprocess.run(["psql", "-X", "-q", "--no-password", "-v", "ON_ERROR_STOP=1",
                                 "-h", cls.host, "-p", cls.port, "-U", cls.admin, "-d", database,
                                 "-v", f"service={service}", "-v", f"expected_database={expected or database}",
                                 "-v", f"migration_role={owner}", "-v", f"runtime_role={runtime}", "-f", str(SQL)],
                                env=cls.environment(cls.admin_password), capture_output=True, text=True, timeout=15)
        if success and result.returncode != 0:
            raise AssertionError("Synthetic service grants failed: " + result.stderr)
        if not success and result.returncode == 0:
            raise AssertionError("Invalid service grants unexpectedly succeeded")

    @classmethod
    def create_databases(cls, bindings=None, success=True):
        bindings = bindings or cls.bindings
        command = ["psql", "-X", "-q", "--no-password", "-v", "ON_ERROR_STOP=1",
                   "-h", cls.host, "-p", cls.port, "-U", cls.admin, "-d", "postgres"]
        for service, (database, owner, runtime, _) in bindings.items():
            for key, value in ((f"{service}_database", database), (f"{service}_migration_role", owner),
                               (f"{service}_runtime_role", runtime)):
                command.extend(["-v", f"{key}={value}"])
        command.extend(["-f", str(CREATE_SQL)])
        result = subprocess.run(command, env=cls.environment(cls.admin_password), capture_output=True, text=True, timeout=60)
        if success and result.returncode != 0:
            raise AssertionError("Fresh synthetic database provisioning failed: " + result.stderr)
        if success is False and result.returncode == 0:
            raise AssertionError("Invalid initial provisioning succeeded")
        return result

    @classmethod
    def fresh_bindings(cls, label):
        suffix = uuid.uuid4().hex[:16]
        bindings = {}
        for service in ("core", "kfe"):
            owner = f"cell_{label}_{service}_owner_{suffix}"
            runtime = f"cell_{label}_{service}_user_{suffix}"
            database = f"cell_{label}_{service}_{suffix}"
            cls.query("postgres", cls.admin, cls.admin_password,
                      f"CREATE ROLE {owner} LOGIN NOINHERIT; CREATE ROLE {runtime} LOGIN NOINHERIT")
            bindings[service] = (database, owner, runtime, None)
        return bindings

    def test_initial_provisioning_refuses_existing_databases_without_changing_history(self):
        database, _, runtime, _ = self.bindings["core"]
        before = self.query(database, runtime, self.password, "SELECT count(*) FROM public.flyway_schema_history")
        result = self.create_databases(success=False)
        self.assertIn("refuses an existing service database", result.stderr)
        after = self.query(database, runtime, self.password, "SELECT count(*) FROM public.flyway_schema_history")
        self.assertEqual(before, after)

    def test_existing_second_database_prevents_creation_of_first_target(self):
        bindings = self.fresh_bindings("collision")
        self.query("postgres", self.admin, self.admin_password, f"CREATE DATABASE {bindings['kfe'][0]}")
        result = self.create_databases(bindings, success=False)
        self.assertIn("refuses an existing service database", result.stderr)
        absent = self.query("postgres", self.admin, self.admin_password,
                            f"SELECT count(*) FROM pg_database WHERE datname='{bindings['core'][0]}'")
        self.assertEqual(absent, "0")

    def test_partial_initial_provisioning_is_preserved_without_automatic_recovery(self):
        # Interruptions can leave Core either disabled (before its ACL) or
        # enabled (before KFE creation). Neither state authorizes adoption.
        for enabled in (False, True):
            with self.subTest(connections_enabled=enabled):
                bindings = self.fresh_bindings("partial")
                database, owner, _, _ = bindings["core"]
                self.query("postgres", self.admin, self.admin_password,
                           f"CREATE DATABASE {database} OWNER {owner} TEMPLATE template0 "
                           f"ALLOW_CONNECTIONS {'true' if enabled else 'false'}")
                catalog = ("SELECT oid::text || ':' || datdba::text || ':' || "
                           "datallowconn::text || ':' || coalesce(datacl::text,'NULL') "
                           f"FROM pg_database WHERE datname='{database}'")
                before = self.query("postgres", self.admin, self.admin_password, catalog)
                self.assertTrue(before)
                result = self.create_databases(bindings, success=False)
                self.assertIn("refuses an existing service database", result.stderr)
                self.assertEqual(self.query("postgres", self.admin, self.admin_password, catalog), before)
                self.assertEqual(self.query("postgres", self.admin, self.admin_password,
                    f"SELECT count(*) FROM pg_database WHERE datname='{bindings['kfe'][0]}'"), "0")

    def test_concurrent_initial_provisioners_have_one_winner_and_no_adoption(self):
        bindings = self.fresh_bindings("race")
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: self.create_databases(bindings, success=None), range(2)))
        self.assertEqual(sum(result.returncode == 0 for result in results), 1)
        rejected = next(result for result in results if result.returncode != 0)
        self.assertIn("refuses an existing service database", rejected.stderr)
        for database, owner, runtime, _ in bindings.values():
            actual = self.query("postgres", self.admin, self.admin_password,
                                f"SELECT r.rolname || ':' || d.datallowconn::text "
                                f"FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba WHERE d.datname='{database}'")
            self.assertEqual(actual, owner + ":true")
            self.assertEqual(self.query("postgres", self.admin, self.admin_password,
                                        f"SELECT has_database_privilege('{runtime}','{database}','CONNECT')"), "f")

    def test_invalid_names_and_role_aliases_cannot_create_targets(self):
        bindings = self.fresh_bindings("inputs")
        original = bindings["core"]
        for name in ("postgres", "BadCase", "x" * 64, "fresh'; DROP DATABASE postgres; --"):
            invalid = dict(bindings)
            invalid["core"] = (name, *original[1:])
            result = self.create_databases(invalid, success=False)
            self.assertIn("Invalid fresh service database or role name", result.stderr)
        invalid = dict(bindings)
        invalid["core"] = (original[0], original[1], original[1], original[3])
        result = self.create_databases(invalid, success=False)
        self.assertIn("all four roles must be distinct", result.stderr)
        for database, _, _, _ in bindings.values():
            self.assertEqual(self.query("postgres", self.admin, self.admin_password,
                                        f"SELECT count(*) FROM pg_database WHERE datname='{database}'"), "0")

    def test_runtime_can_validate_and_run_up_to_date_flyway_without_ddl_rights(self):
        for service, (database, _, runtime, jar) in self.bindings.items():
            env = self.environment(self.password)
            env.update(SPRING_DATASOURCE_URL=f"jdbc:postgresql://{self.host}:{self.port}/{database}",
                       SPRING_DATASOURCE_USERNAME=runtime, SPRING_DATASOURCE_PASSWORD=self.password)
            for mode in ("validate", "migrate"):
                result = subprocess.run(["java", "-jar", str(jar), f"--cell-migration={mode}"],
                                        env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, f"{service} {mode} with restricted runtime role")

    def test_runtime_has_no_ddl_history_write_or_foreign_database_access(self):
        for service, (database, _, runtime, _) in self.bindings.items():
            self.query(database, runtime, self.password, "SELECT count(*) FROM public.flyway_schema_history")
            self.query(database, runtime, self.password, "CREATE TABLE public.forbidden_runtime_ddl(id int)", success=False)
            self.query(database, runtime, self.password, "UPDATE public.flyway_schema_history SET checksum=checksum", success=False)
            other = self.bindings["kfe" if service == "core" else "core"][0]
            self.query(other, runtime, self.password, "SELECT 1", success=False)

    def test_runtime_can_write_own_data_but_not_legacy_other_owner_schema(self):
        core, _, core_user, _ = self.bindings["core"]
        self.query(core, core_user, self.password,
                   "INSERT INTO public.home_ui_override(scope,payload) VALUES ('GLOBAL','{}') RETURNING id")
        self.query(core, self.bindings["core"][1], self.password, "SELECT count(*) FROM financial.wallets")
        self.query(core, core_user, self.password, "SELECT count(*) FROM financial.wallets", success=False)
        kfe, owner, runtime, _ = self.bindings["kfe"]
        self.query(kfe, owner, self.password, "CREATE TABLE financial.synthetic_runtime_grant_probe(id bigserial PRIMARY KEY, value text)")
        self.query(kfe, runtime, self.password, "INSERT INTO financial.synthetic_runtime_grant_probe(value) VALUES ('synthetic') RETURNING id")
        self.query(kfe, owner, self.password, "SELECT count(*) FROM auth.users_credentials")
        self.query(kfe, runtime, self.password, "SELECT count(*) FROM auth.users_credentials", success=False)

    def test_failure_after_database_acl_changes_rolls_back_entire_grant_transaction(self):
        _, owner, runtime, _ = self.bindings["core"]
        target = "cell_grants_incomplete_" + uuid.uuid4().hex[:16]
        self.query("postgres", self.admin, self.admin_password, f"CREATE DATABASE {target} OWNER {owner}")
        # Deliberately incomplete synthetic target: history marker but no auth
        # schema. It must fail after provisional ACL changes, rolling them back.
        self.query(target, owner, self.password, "CREATE TABLE public.flyway_schema_history(installed_rank integer)")
        before = self.query("postgres", self.admin, self.admin_password,
                            f"SELECT coalesce(datacl::text,'null') FROM pg_database WHERE datname='{target}'")
        self.grants("core", target=target, success=False)
        after = self.query("postgres", self.admin, self.admin_password,
                           f"SELECT coalesce(datacl::text,'null') FROM pg_database WHERE datname='{target}'")
        self.assertEqual(before, after)
        self.assertEqual(self.query(target, self.admin, self.admin_password,
                                    f"SELECT has_table_privilege('{runtime}','public.flyway_schema_history','SELECT')"), "f")

    def test_invalid_binding_and_privileged_role_fail_without_partial_grants(self):
        self.grants("core", expected="wrong_synthetic_database", success=False)
        database, _, runtime, _ = self.bindings["core"]
        self.query(database, self.admin, self.admin_password, f"ALTER ROLE {runtime} CREATEDB")
        try:
            self.grants("core", success=False)
        finally:
            self.query(database, self.admin, self.admin_password, f"ALTER ROLE {runtime} NOCREATEDB")
        self.assertEqual(self.query(database, self.admin, self.admin_password,
                                    f"SELECT has_database_privilege('{runtime}',current_database(),'CREATE')"), "f")

    def test_role_membership_cannot_supply_migration_privileges(self):
        database, owner, runtime, _ = self.bindings["core"]
        self.query(database, self.admin, self.admin_password, f"GRANT {owner} TO {runtime}")
        try:
            self.grants("core", success=False)
        finally:
            self.query(database, self.admin, self.admin_password, f"REVOKE {owner} FROM {runtime}")

    def test_column_grants_cannot_bypass_foreign_data_or_history_policy(self):
        database, _, runtime, _ = self.bindings["core"]
        for grant, revoke in ((f"GRANT SELECT (user_id) ON financial.wallets TO {runtime}",
                               f"REVOKE SELECT (user_id) ON financial.wallets FROM {runtime}"),
                              (f"GRANT UPDATE (checksum) ON public.flyway_schema_history TO {runtime}",
                               f"REVOKE UPDATE (checksum) ON public.flyway_schema_history FROM {runtime}")):
            self.query(database, self.admin, self.admin_password, grant)
            try:
                self.grants("core", success=False)
            finally:
                self.query(database, self.admin, self.admin_password, revoke)

    def test_foreign_schema_grants_are_rejected_not_silently_repaired(self):
        database, _, runtime, _ = self.bindings["core"]
        for grant, revoke in ((f"GRANT SELECT ON financial.wallets TO {runtime}",
                               f"REVOKE SELECT ON financial.wallets FROM {runtime}"),
                              (f"GRANT CREATE ON SCHEMA financial TO {runtime}",
                               f"REVOKE CREATE ON SCHEMA financial FROM {runtime}")):
            self.query(database, self.admin, self.admin_password, grant)
            try:
                self.grants("core", success=False)
                check = (f"SELECT has_schema_privilege('{runtime}','financial','CREATE')"
                         if "ON SCHEMA" in grant else
                         f"SELECT has_table_privilege('{runtime}','financial.wallets','SELECT')")
                self.assertEqual(self.query(database, self.admin, self.admin_password, check), "t")
            finally:
                self.query(database, self.admin, self.admin_password, revoke)


if __name__ == "__main__":
    unittest.main()
