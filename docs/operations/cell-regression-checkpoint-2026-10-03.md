# Cell controller regression checkpoint — October 3, 2026

Not a complete Cell installation, update or recovery qualification. All execution
capability blockers remain active. No production resource, custody operation,
release authority or signer was provisioned/activated during these checks.

At Deploy revision `b088774`, the coordinator ran:

- `bash infra/tests/kerosene-stack-test.sh`: passed.
- `bash infra/tests/kerosene-stack-evidence-test.sh`: passed.
- `stack-admin-install-test.py` with explicit local built Admin distribution:
  24 tests passed, no skips.
- `stack-publication-test.py` with explicit local built jctl executable:
  45 tests passed, no skips. These use synthetic signing material, not real
  release authorization or live deployment.
- `stack-secret-preflight-test.py`: 10 tests passed.
- `smoke-cluster-binding-test.py`: four tests passed.
- `stack-lifecycle-test.py`: 52 tests passed.
- `stack-database-plan-test.py`: 22 tests passed.
- `python3 -W error infra/tests/stack-probe-process-test.py`: 17 tests passed.

The actual jctl distribution came from the isolated Admin worktree's
`build/install/kerosene-jctl`; this is local executable evidence, not OCI Admin
installation qualification or a claim that hosted CI ran. Initial runs without
explicit consumer inputs skipped one test in each consumer suite; both were
repeated with explicit inputs and passed without skips.

The result protects existing release/publication/preflight interfaces after
controller changes. It does not discharge initial admission, migration execution,
tested recovery, independent Vault rebuild/compatibility, quorum-preserving
Node/Vault rollout or complete operator/UI/service acceptance.

## Real separated-database follow-up

The existing loopback disposable PostgreSQL 17 fixture was verified running on
port 32770. The explicitly opted-in `service-database-grants-postgres-test.py`
passed 13 cases using actual local executable Core/KFE JARs. Fresh synthetic
databases contain exactly 14 successful Core migrations through V14 and 59 KFE
migrations through V58, no failed rows, and migration-role-owned Flyway history.
Permission denials, transactional ACL rollback, concurrent provisioner exclusion
and refusal to adopt existing databases were exercised against PostgreSQL.
Synthetic databases/roles remain retained; no existing database was dropped.
This is not automatic Cell migration execution, TLS/credential provisioning,
legacy ownership conversion or tested data restore.
