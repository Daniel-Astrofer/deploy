-- Dedicated Bank admission-registry database only, never Core/KFE's database.
-- Fresh provisioning by an external owner; no adoption, cleanup or credentials.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
CREATE SCHEMA cell_admission;
REVOKE ALL ON SCHEMA cell_admission FROM PUBLIC;
CREATE TABLE cell_admission.consumed (
    network_id text NOT NULL CHECK (network_id ~ '^[a-z0-9][a-z0-9._-]{2,127}$'),
    epoch bigint NOT NULL CHECK (epoch BETWEEN 1 AND 9007199254740991),
    nonce text NOT NULL CHECK (nonce ~ '^[0-9a-f]{64}$'),
    cell_id text NOT NULL CHECK (cell_id ~ '^[a-z0-9][a-z0-9._-]{2,127}$'),
    cluster_uid text NOT NULL CHECK (cluster_uid ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
    operator_id text NOT NULL CHECK (operator_id ~ '^[a-z0-9][a-z0-9._-]{2,127}$'),
    change_id text NOT NULL CHECK (change_id ~ '^[a-z0-9][a-z0-9._-]{2,127}$'),
    approval_digest text NOT NULL CHECK (approval_digest ~ '^sha256:[0-9a-f]{64}$'),
    admission_digest text NOT NULL CHECK (admission_digest ~ '^sha256:[0-9a-f]{64}$'),
    issued_at_unix_seconds bigint NOT NULL CHECK (issued_at_unix_seconds > 0),
    expires_at_unix_seconds bigint NOT NULL CHECK (expires_at_unix_seconds <= 9007199254740991),
    consumed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (network_id, epoch, nonce),
    UNIQUE (network_id, cell_id),
    UNIQUE (network_id, cluster_uid),
    UNIQUE (admission_digest),
    CHECK (expires_at_unix_seconds > issued_at_unix_seconds),
    CHECK (expires_at_unix_seconds - issued_at_unix_seconds <= 3600)
);
REVOKE ALL ON TABLE cell_admission.consumed FROM PUBLIC;
COMMENT ON TABLE cell_admission.consumed IS
    'Immutable admission consumption; authenticated Bank verifier only. Never clear on failed install or uninstall. Not signature verification.';
COMMIT;
