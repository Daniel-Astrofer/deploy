-- Keep fresh Postgres volumes aligned with the out-of-band migration script.
-- Everything here must be idempotent because init.sql already creates the base
-- schema and some operators also apply migrations manually to existing volumes.

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 001: Adicionar coluna totp_secret na tabela financial.wallets
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(255) NOT NULL DEFAULT '';

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 002: Critical Performance Indexes (Issue 2.5)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_ledger_wallet_id   ON financial.ledger(wallet_id);
CREATE INDEX IF NOT EXISTS idx_ledger_nonce        ON financial.ledger(nonce);
CREATE INDEX IF NOT EXISTS idx_ledger_last_hash    ON financial.ledger(last_hash);
CREATE INDEX IF NOT EXISTS idx_wallet_user_id      ON financial.wallets(user_id);
CREATE INDEX IF NOT EXISTS idx_ledger_history_receiver ON financial.ledger_transaction_history(receiver_user_id);
CREATE INDEX IF NOT EXISTS idx_ledger_history_sender   ON financial.ledger_transaction_history(sender_identifier);
CREATE INDEX IF NOT EXISTS idx_ledger_history_created  ON financial.ledger_transaction_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_history_txid     ON financial.ledger_transaction_history(blockchain_txid)
    WHERE blockchain_txid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ledger_history_status   ON financial.ledger_transaction_history(status);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 003: Adicionar coluna deposit_address em financial.wallets
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS deposit_address VARCHAR(100) NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_deposit_address
    ON financial.wallets(deposit_address)
    WHERE deposit_address IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 004: Passkey Schema Harmonization
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE auth.passkey_credentials DROP COLUMN IF EXISTS public_key;
ALTER TABLE auth.passkey_credentials ADD COLUMN IF NOT EXISTS device_name VARCHAR(255);
ALTER TABLE auth.passkey_credentials ADD COLUMN IF NOT EXISTS relying_party_id VARCHAR(255);
ALTER TABLE auth.passkey_credentials ADD COLUMN IF NOT EXISTS origin_host VARCHAR(255);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 005: Dev balance claim guard
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE auth.users_credentials
    ADD COLUMN IF NOT EXISTS test_balance_claimed BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE auth.users_credentials
    ADD COLUMN IF NOT EXISTS shamir_total_shares INTEGER;

ALTER TABLE auth.users_credentials
    ADD COLUMN IF NOT EXISTS shamir_threshold INTEGER;

ALTER TABLE auth.users_credentials
    ADD COLUMN IF NOT EXISTS multisig_threshold INTEGER NOT NULL DEFAULT 2;

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 006: XPUB derivation state for deterministic wallet addresses
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS xpub TEXT;

ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS wallet_mode VARCHAR(32) NOT NULL DEFAULT 'KEROSENE';

ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS last_derived_index INTEGER NOT NULL DEFAULT -1;

UPDATE financial.wallets
    SET wallet_mode = 'KEROSENE'
    WHERE wallet_mode IS NULL;

UPDATE financial.wallets
    SET last_derived_index = -1
    WHERE last_derived_index IS NULL;

ALTER TABLE financial.wallets
    ALTER COLUMN last_derived_index SET DEFAULT -1;

ALTER TABLE financial.wallets
    ALTER COLUMN wallet_mode SET DEFAULT 'KEROSENE';

ALTER TABLE financial.wallets
    ALTER COLUMN wallet_mode SET NOT NULL;

ALTER TABLE financial.wallets
    ALTER COLUMN last_derived_index SET NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 007: External network wallet metadata
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS lightning_address VARCHAR(255);

ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS external_wallet_reference VARCHAR(255);

ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS card_number_suffix VARCHAR(4);

ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS card_issued_at TIMESTAMP;

ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS card_expires_at TIMESTAMP;

ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS card_last_rotated_at TIMESTAMP;

ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS card_sequence INTEGER NOT NULL DEFAULT 1;

ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS previous_card_number_suffix VARCHAR(4);

ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS previous_card_expires_at TIMESTAMP;

CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_lightning_address
    ON financial.wallets(lightning_address)
    WHERE lightning_address IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 008: External network transfers and custody events
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS financial.network_transfers (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE,
    wallet_id BIGINT NOT NULL REFERENCES financial.wallets(id) ON DELETE CASCADE,
    wallet_name_snapshot VARCHAR(255) NOT NULL,
    network VARCHAR(32) NOT NULL,
    transfer_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    destination TEXT,
    external_reference VARCHAR(255),
    invoice_data TEXT,
    amount_btc NUMERIC(19, 8),
    network_fee_btc NUMERIC(19, 8),
    platform_fee_btc NUMERIC(19, 8),
    total_debited_btc NUMERIC(19, 8),
    context TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_network_transfers_user_created
    ON financial.network_transfers(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_network_transfers_wallet
    ON financial.network_transfers(wallet_id);

CREATE INDEX IF NOT EXISTS idx_network_transfers_status
    ON financial.network_transfers(status);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 010: Inbound network transfer lifecycle metadata
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE financial.network_transfers
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_network_transfers_type_status
    ON financial.network_transfers(transfer_type, status);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 011: Treasury configuration for real audit xpub scanning
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS financial.treasury_config (
    id BIGSERIAL PRIMARY KEY,
    max_withdraw_limit NUMERIC(19, 8) NOT NULL DEFAULT 1.00000000,
    audit_xpub TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 012: Persistent idempotency for on-chain deposits
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS financial.processed_transactions (
    id UUID PRIMARY KEY,
    txid VARCHAR(128) NOT NULL UNIQUE,
    source VARCHAR(64) NOT NULL,
    processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_processed_transactions_txid
    ON financial.processed_transactions(txid);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 013: Auth account password hash + activation audit timestamp
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE auth.users_credentials
    ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);

UPDATE auth.users_credentials
    SET password_hash = passphrase
    WHERE password_hash IS NULL
      AND passphrase IS NOT NULL;

ALTER TABLE auth.users_credentials
    ADD COLUMN IF NOT EXISTS activated_at TIMESTAMP;

UPDATE auth.users_credentials
    SET activated_at = CURRENT_TIMESTAMP
    WHERE is_active = TRUE
      AND activated_at IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 014: Auth support tables required by the current JPA model
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS auth.users_device (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_users_device_user_id
    ON auth.users_device(user_id);

CREATE TABLE IF NOT EXISTS public.user_backup_codes (
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE,
    code_hash VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS idx_user_backup_codes_user_id
    ON public.user_backup_codes(user_id);

CREATE TABLE IF NOT EXISTS auth.user_app_pin_settings (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE,
    device_hash VARCHAR(128) NOT NULL,
    pin_hash VARCHAR(255),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMP,
    last_verified_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, device_hash)
);

CREATE INDEX IF NOT EXISTS idx_user_app_pin_user_id
    ON auth.user_app_pin_settings(user_id);

CREATE INDEX IF NOT EXISTS idx_user_app_pin_device_hash
    ON auth.user_app_pin_settings(device_hash);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 015: Deposit history table for inbound on-chain tracking
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.deposits (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    txid VARCHAR(255) NOT NULL UNIQUE,
    from_address VARCHAR(255) NOT NULL,
    to_address VARCHAR(255) NOT NULL,
    amount_btc NUMERIC(19, 8) NOT NULL,
    network_fee NUMERIC(19, 8),
    confirmations BIGINT NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_deposits_user_created
    ON public.deposits(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_deposits_status
    ON public.deposits(status);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 016: Audit and treasury support tables
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS financial.merkle_audit (
    id UUID PRIMARY KEY,
    merkle_root VARCHAR(64) NOT NULL,
    ledger_count BIGINT NOT NULL,
    anchor_txid VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_merkle_audit_created_at
    ON financial.merkle_audit(created_at DESC);

CREATE TABLE IF NOT EXISTS financial.siphon_requests (
    id UUID PRIMARY KEY,
    amount NUMERIC(19, 8) NOT NULL,
    requested_at TIMESTAMP NOT NULL,
    executable_after TIMESTAMP NOT NULL,
    status VARCHAR(32) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_siphon_requests_status_eta
    ON financial.siphon_requests(status, executable_after);

CREATE TABLE IF NOT EXISTS financial.platform_revenue (
    id BIGSERIAL PRIMARY KEY,
    accumulated_profit NUMERIC(19, 8) NOT NULL DEFAULT 0,
    hmac_sha256 VARCHAR(255) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 017: Real network transfer reconciliation metadata
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE financial.network_transfers
    ADD COLUMN IF NOT EXISTS invoice_id VARCHAR(128);

ALTER TABLE financial.network_transfers
    ADD COLUMN IF NOT EXISTS blockchain_txid VARCHAR(128);

ALTER TABLE financial.network_transfers
    ADD COLUMN IF NOT EXISTS payment_hash VARCHAR(128);

ALTER TABLE financial.network_transfers
    ADD COLUMN IF NOT EXISTS detected_at TIMESTAMP;

ALTER TABLE financial.network_transfers
    ADD COLUMN IF NOT EXISTS settled_at TIMESTAMP;

ALTER TABLE financial.network_transfers
    ADD COLUMN IF NOT EXISTS confirmations INTEGER NOT NULL DEFAULT 0;

ALTER TABLE financial.network_transfers
    ADD COLUMN IF NOT EXISTS provider_payload TEXT;

UPDATE financial.network_transfers
    SET confirmations = 0
    WHERE confirmations IS NULL;

CREATE INDEX IF NOT EXISTS idx_network_transfers_txid
    ON financial.network_transfers(blockchain_txid);

CREATE INDEX IF NOT EXISTS idx_network_transfers_invoice_id
    ON financial.network_transfers(invoice_id);

CREATE INDEX IF NOT EXISTS idx_network_transfers_payment_hash
    ON financial.network_transfers(payment_hash);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 018: Persistent blockchain address watches for ZMQ reconciliation
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS financial.blockchain_address_watch (
    id UUID PRIMARY KEY,
    transfer_id UUID NOT NULL UNIQUE REFERENCES financial.network_transfers(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE,
    wallet_id BIGINT NOT NULL REFERENCES financial.wallets(id) ON DELETE CASCADE,
    address VARCHAR(128) NOT NULL,
    label VARCHAR(255),
    status VARCHAR(32) NOT NULL DEFAULT 'WATCHING',
    observed_txid VARCHAR(128),
    observed_amount_sats BIGINT,
    confirmations INTEGER NOT NULL DEFAULT 0,
    detected_at TIMESTAMP,
    settled_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_blockchain_watch_address_status
    ON financial.blockchain_address_watch(address, status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_blockchain_watch_transfer
    ON financial.blockchain_address_watch(transfer_id);

CREATE INDEX IF NOT EXISTS idx_blockchain_watch_txid
    ON financial.blockchain_address_watch(observed_txid);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 019: Auditable network transfer events
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS financial.network_transfer_events (
    id UUID PRIMARY KEY,
    transfer_id UUID REFERENCES financial.network_transfers(id) ON DELETE SET NULL,
    user_id BIGINT REFERENCES auth.users_credentials(id) ON DELETE SET NULL,
    event_type VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    reference VARCHAR(255),
    payload TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_network_transfer_events_transfer_created
    ON financial.network_transfer_events(transfer_id, created_at);

CREATE INDEX IF NOT EXISTS idx_network_transfer_events_reference
    ON financial.network_transfer_events(reference);

CREATE INDEX IF NOT EXISTS idx_network_transfer_events_type
    ON financial.network_transfer_events(event_type);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 020: Admin access key flow and authenticated device metadata
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE auth.users_credentials
    ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'USER';

UPDATE auth.users_credentials
    SET role = 'USER'
    WHERE role IS NULL OR role = '';

CREATE INDEX IF NOT EXISTS idx_users_credentials_role
    ON auth.users_credentials(role);

ALTER TABLE auth.passkey_credentials ADD COLUMN IF NOT EXISTS brand VARCHAR(255);
ALTER TABLE auth.passkey_credentials ADD COLUMN IF NOT EXISTS model VARCHAR(255);
ALTER TABLE auth.passkey_credentials ADD COLUMN IF NOT EXISTS serial_number VARCHAR(255);
ALTER TABLE auth.passkey_credentials ADD COLUMN IF NOT EXISTS device_install_id VARCHAR(128);
ALTER TABLE auth.passkey_credentials ADD COLUMN IF NOT EXISTS platform VARCHAR(128);
ALTER TABLE auth.passkey_credentials ADD COLUMN IF NOT EXISTS browser VARCHAR(128);
ALTER TABLE auth.passkey_credentials ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE';
ALTER TABLE auth.passkey_credentials ADD COLUMN IF NOT EXISTS first_access_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE auth.passkey_credentials ADD COLUMN IF NOT EXISTS last_access_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

CREATE TABLE IF NOT EXISTS auth.admin_keys (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE,
    key_material_hash VARCHAR(128) NOT NULL,
    key_fingerprint VARCHAR(64) NOT NULL,
    device_install_id VARCHAR(128),
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    rotated_at TIMESTAMP,
    revoked_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auth.admin_access_devices (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE,
    device_id VARCHAR(128) NOT NULL,
    device_name VARCHAR(255),
    browser VARCHAR(128),
    user_agent VARCHAR(512),
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    first_access_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_access_at TIMESTAMP,
    CONSTRAINT uk_admin_access_device_user_device UNIQUE(user_id, device_id)
);

CREATE TABLE IF NOT EXISTS auth.admin_access_attempts (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE,
    device_row_id UUID NOT NULL REFERENCES auth.admin_access_devices(id) ON DELETE CASCADE,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    browser VARCHAR(128),
    user_agent VARCHAR(512),
    ip_fingerprint VARCHAR(64),
    requested_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    decided_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auth.admin_access_events (
    id UUID PRIMARY KEY,
    admin_id BIGINT REFERENCES auth.users_credentials(id) ON DELETE SET NULL,
    device_id VARCHAR(128),
    browser VARCHAR(128),
    sanitized_user_agent VARCHAR(512),
    ip_fingerprint VARCHAR(64),
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(32) NOT NULL,
    reason VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS idx_admin_keys_user_status
    ON auth.admin_keys(user_id, status);
CREATE INDEX IF NOT EXISTS idx_admin_access_attempts_user_status
    ON auth.admin_access_attempts(user_id, status, expires_at);
CREATE INDEX IF NOT EXISTS idx_admin_access_events_admin_time
    ON auth.admin_access_events(admin_id, occurred_at DESC);
