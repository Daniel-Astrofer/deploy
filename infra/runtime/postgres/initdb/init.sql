CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS financial;

-- =============================================
-- AUTH SCHEMA
-- =============================================

-- Vouchers table for onboarding
CREATE TABLE IF NOT EXISTS auth.vouchers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(255) UNIQUE,
    txid VARCHAR(255) UNIQUE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    value_sats INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used_at TIMESTAMP
);

-- Main users table
CREATE TABLE IF NOT EXISTS auth.users_credentials (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    passphrase VARCHAR(255),
    password_hash VARCHAR(255),
    totp_secret VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP,
    is_active BOOLEAN DEFAULT FALSE,
    activated_at TIMESTAMP,
    failed_login_attempts INTEGER DEFAULT 0,
    account_security VARCHAR(20) DEFAULT 'STANDARD',
    passkey_transaction_auth BOOLEAN DEFAULT FALSE,
    test_balance_claimed BOOLEAN DEFAULT FALSE,
    role VARCHAR(32) NOT NULL DEFAULT 'USER',
    platform_cosigner_secret TEXT,
    shamir_total_shares INTEGER,
    shamir_threshold INTEGER,
    multisig_threshold INTEGER NOT NULL DEFAULT 2,
    voucher_id UUID UNIQUE REFERENCES auth.vouchers(id)
);

-- Passkey credentials table (FIDO2 / WebAuthn)
CREATE TABLE IF NOT EXISTS auth.passkey_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    credential_id BYTEA UNIQUE NOT NULL,
    user_handle BYTEA NOT NULL,
    public_key_cose BYTEA NOT NULL,
    device_name VARCHAR(255),
    relying_party_id VARCHAR(255),
    origin_host VARCHAR(255),
    brand VARCHAR(255),
    model VARCHAR(255),
    serial_number VARCHAR(255),
    device_install_id VARCHAR(128),
    platform VARCHAR(128),
    browser VARCHAR(128),
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    first_access_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_access_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    signature_count BIGINT NOT NULL DEFAULT 0,
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS auth.admin_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id BIGINT REFERENCES auth.users_credentials(id) ON DELETE SET NULL,
    device_id VARCHAR(128),
    browser VARCHAR(128),
    sanitized_user_agent VARCHAR(512),
    ip_fingerprint VARCHAR(64),
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(32) NOT NULL,
    reason VARCHAR(255)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_passkey_user_id ON auth.passkey_credentials(user_id);
CREATE INDEX IF NOT EXISTS idx_users_username ON auth.users_credentials(username);
CREATE INDEX IF NOT EXISTS idx_users_credentials_role ON auth.users_credentials(role);
CREATE INDEX IF NOT EXISTS idx_admin_keys_user_status ON auth.admin_keys(user_id, status);
CREATE INDEX IF NOT EXISTS idx_admin_access_attempts_user_status ON auth.admin_access_attempts(user_id, status, expires_at);
CREATE INDEX IF NOT EXISTS idx_admin_access_events_admin_time ON auth.admin_access_events(admin_id, occurred_at DESC);

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

CREATE INDEX IF NOT EXISTS idx_user_app_pin_user_id ON auth.user_app_pin_settings(user_id);
CREATE INDEX IF NOT EXISTS idx_user_app_pin_device_hash ON auth.user_app_pin_settings(device_hash);

-- =============================================
-- FINANCIAL SCHEMA
-- =============================================

-- Wallets table
CREATE TABLE IF NOT EXISTS financial.wallets (
    id BIGSERIAL PRIMARY KEY,
    version BIGINT NOT NULL DEFAULT 0,
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE,
    address VARCHAR(255) NOT NULL, -- Actually used as passphraseHash
    name VARCHAR(255) NOT NULL,
    totp_secret VARCHAR(255) NOT NULL,
    deposit_address VARCHAR(100),
    lightning_address VARCHAR(255),
    external_wallet_reference VARCHAR(255),
    card_number_suffix VARCHAR(4),
    card_issued_at TIMESTAMP,
    card_expires_at TIMESTAMP,
    card_last_rotated_at TIMESTAMP,
    card_sequence INTEGER NOT NULL DEFAULT 1,
    previous_card_number_suffix VARCHAR(4),
    previous_card_expires_at TIMESTAMP,
    xpub TEXT,
    wallet_mode VARCHAR(32) NOT NULL DEFAULT 'KEROSENE',
    last_derived_index INTEGER NOT NULL DEFAULT -1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    UNIQUE(user_id, name)
);

-- Ledger table
CREATE TABLE IF NOT EXISTS financial.ledger (
    id SERIAL PRIMARY KEY,
    wallet_id BIGINT NOT NULL REFERENCES financial.wallets(id) ON DELETE CASCADE,
    balance TEXT NOT NULL, -- ALE Encrypted
    balance_signature VARCHAR(256),
    nonce INTEGER NOT NULL DEFAULT 0,
    last_hash VARCHAR(256) NOT NULL,
    context VARCHAR(256) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Ledger Transaction History table
-- LEGACY/EPHEMERAL: this is a readable operational buffer, not a durable user
-- statement. Default application cleanup removes rows older than 24 hours.
-- Durable user-facing history belongs in encrypted mobile storage; backend
-- integrity is maintained through hashes, commitments and Merkle roots.
CREATE TABLE IF NOT EXISTS financial.ledger_transaction_history (
    id UUID PRIMARY KEY,
    sender_identifier VARCHAR(255) NOT NULL,
    sender_user_id BIGINT,
    receiver_identifier VARCHAR(255) NOT NULL,
    receiver_user_id BIGINT,
    transaction_type VARCHAR(50) NOT NULL,
    amount NUMERIC(18, 8) NOT NULL,
    status VARCHAR(50) NOT NULL,
    network_fee NUMERIC(18, 8),
    blockchain_txid VARCHAR(255),
    context TEXT,
    created_at TIMESTAMP NOT NULL,
    confirmations INTEGER
);

-- Ledger Entries (Audit)
CREATE TABLE IF NOT EXISTS financial.ledger_entries (
    id UUID PRIMARY KEY,
    tx_id UUID NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    amount_net NUMERIC(18, 8) NOT NULL,
    fee_amount NUMERIC(18, 8) NOT NULL,
    status VARCHAR(50) NOT NULL,
    created_at TIMESTAMP NOT NULL
);

-- Ledger Transactions (Metadata)
CREATE TABLE IF NOT EXISTS financial.ledger_transactions (
    id BIGSERIAL PRIMARY KEY,
    txid VARCHAR(255) NOT NULL,
    user_id BIGINT NOT NULL,
    to_address TEXT, -- ALE Encrypted
    amount NUMERIC(19, 8),
    message TEXT, -- ALE Encrypted
    created_at TIMESTAMP NOT NULL
);

-- =============================================
-- BITCOIN ACCOUNTS
-- =============================================

CREATE TABLE IF NOT EXISTS financial.bitcoin_accounts (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE,
    type VARCHAR(48) NOT NULL CHECK (type IN ('INTERNAL_CARD','WATCH_ONLY_COLD_WALLET','LIGHTNING_INTERNAL')),
    custody VARCHAR(48) NOT NULL CHECK (custody IN ('KEROSENE_CUSTODIAL','USER_SELF_CUSTODY','WATCH_ONLY')),
    status VARCHAR(48) NOT NULL CHECK (status IN ('ACTIVE','FROZEN','EXPIRED','REPLACED','SAFETY_LOCKED','USER_ACTION_REQUIRED')),
    label VARCHAR(96) NOT NULL,
    risk_tier VARCHAR(32) NOT NULL DEFAULT 'BRONZE',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financial.bitcoin_ledger_accounts (
    id UUID PRIMARY KEY,
    version BIGINT DEFAULT 0,
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE,
    bitcoin_account_id UUID NOT NULL REFERENCES financial.bitcoin_accounts(id) ON DELETE CASCADE,
    currency VARCHAR(16) NOT NULL DEFAULT 'BTC',
    balance_available_sats BIGINT NOT NULL DEFAULT 0 CHECK (balance_available_sats >= 0),
    balance_pending_sats BIGINT NOT NULL DEFAULT 0 CHECK (balance_pending_sats >= 0),
    balance_locked_sats BIGINT NOT NULL DEFAULT 0 CHECK (balance_locked_sats >= 0),
    balance_auto_hold_sats BIGINT NOT NULL DEFAULT 0 CHECK (balance_auto_hold_sats >= 0),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financial.internal_btc_cards (
    id UUID PRIMARY KEY,
    bitcoin_account_id UUID NOT NULL REFERENCES financial.bitcoin_accounts(id) ON DELETE CASCADE,
    ledger_account_id UUID NOT NULL REFERENCES financial.bitcoin_ledger_accounts(id) ON DELETE CASCADE,
    permanent_address_id UUID,
    receiving_policy VARCHAR(48) NOT NULL DEFAULT 'ROTATING',
    daily_limit_sats BIGINT NOT NULL DEFAULT 0 CHECK (daily_limit_sats >= 0),
    monthly_limit_sats BIGINT NOT NULL DEFAULT 0 CHECK (monthly_limit_sats >= 0),
    expires_at TIMESTAMP,
    status VARCHAR(48) NOT NULL CHECK (status IN ('ACTIVE','FROZEN','EXPIRED','REPLACED','SAFETY_LOCKED')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financial.receiving_addresses (
    id UUID PRIMARY KEY,
    card_id UUID NOT NULL REFERENCES financial.internal_btc_cards(id) ON DELETE CASCADE,
    address VARCHAR(128) NOT NULL UNIQUE,
    derivation_path VARCHAR(160) NOT NULL,
    derivation_index INTEGER NOT NULL CHECK (derivation_index >= -1),
    script_type VARCHAR(16) NOT NULL CHECK (script_type IN ('P2WPKH','P2TR')),
    status VARCHAR(48) NOT NULL CHECK (status IN ('UNUSED','ASSIGNED','OBSERVED','EXPIRED','EXPIRED_RECEIVED','USER_ACTION_REQUIRED','SAFETY_LOCKED')),
    first_seen_txid VARCHAR(128),
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financial.receiving_requests (
    id UUID PRIMARY KEY,
    card_id UUID NOT NULL REFERENCES financial.internal_btc_cards(id) ON DELETE CASCADE,
    address_id UUID NOT NULL REFERENCES financial.receiving_addresses(id) ON DELETE CASCADE,
    public_code VARCHAR(48) NOT NULL UNIQUE CHECK (public_code LIKE 'KRS-%'),
    amount_sats BIGINT CHECK (amount_sats IS NULL OR amount_sats > 0),
    expires_at TIMESTAMP,
    one_time BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR(48) NOT NULL CHECK (status IN ('ACTIVE','EXPIRED','MEMPOOL_SEEN','CONFIRMING','PAID','EXPIRED_RECEIVED','AUTO_RESOLUTION_PENDING','USER_ACTION_REQUIRED','HIDDEN','FAILED_SAFE')),
    self_service_reason VARCHAR(160),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at TIMESTAMP,
    purge_after TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP + INTERVAL '24 hours'),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financial.bitcoin_ledger_entries (
    id UUID PRIMARY KEY,
    ledger_account_id UUID NOT NULL REFERENCES financial.bitcoin_ledger_accounts(id) ON DELETE CASCADE,
    direction VARCHAR(16) NOT NULL CHECK (direction IN ('CREDIT','DEBIT')),
    amount_sats BIGINT NOT NULL CHECK (amount_sats > 0),
    status VARCHAR(32) NOT NULL CHECK (status IN ('PENDING','AVAILABLE','LOCKED','AUTO_HOLD','FINALIZED','REVERSED','FAILED_SAFE')),
    source_type VARCHAR(64) NOT NULL,
    source_id VARCHAR(160) NOT NULL,
    idempotency_key VARCHAR(180) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financial.cold_wallets (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES financial.bitcoin_accounts(id) ON DELETE CASCADE,
    descriptor TEXT,
    xpub TEXT,
    fingerprint VARCHAR(32) NOT NULL,
    derivation_path VARCHAR(160) NOT NULL,
    script_policy VARCHAR(32) NOT NULL CHECK (script_policy IN ('SINGLE_SIG','MULTISIG')),
    can_sign BOOLEAN NOT NULL DEFAULT FALSE CHECK (can_sign = FALSE),
    last_scan_height BIGINT NOT NULL DEFAULT 0,
    observed_balance_sats BIGINT NOT NULL DEFAULT 0 CHECK (observed_balance_sats >= 0),
    CHECK ((descriptor IS NOT NULL AND length(trim(descriptor)) > 0) OR (xpub IS NOT NULL AND length(trim(xpub)) > 0)),
    CHECK (fingerprint ~* '^[0-9a-f]{8}$'),
    CHECK (derivation_path LIKE 'm/%'),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financial.cold_wallet_addresses (
    id UUID PRIMARY KEY,
    cold_wallet_id UUID NOT NULL REFERENCES financial.cold_wallets(id) ON DELETE CASCADE,
    address VARCHAR(128) NOT NULL UNIQUE,
    derivation_index INTEGER NOT NULL CHECK (derivation_index >= 0),
    is_change BOOLEAN NOT NULL DEFAULT FALSE,
    observed_balance_sats BIGINT NOT NULL DEFAULT 0 CHECK (observed_balance_sats >= 0),
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financial.cold_wallet_utxos (
    id UUID PRIMARY KEY,
    cold_wallet_id UUID NOT NULL REFERENCES financial.cold_wallets(id) ON DELETE CASCADE,
    txid VARCHAR(128) NOT NULL,
    vout INTEGER NOT NULL CHECK (vout >= 0),
    amount_sats BIGINT NOT NULL CHECK (amount_sats > 0),
    confirmations INTEGER NOT NULL DEFAULT 0 CHECK (confirmations >= 0),
    status VARCHAR(32) NOT NULL CHECK (status IN ('UNSPENT','SPENT','LOCKED')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financial.psbt_workflows (
    id UUID PRIMARY KEY,
    cold_wallet_id UUID NOT NULL REFERENCES financial.cold_wallets(id) ON DELETE CASCADE,
    unsigned_psbt TEXT NOT NULL,
    signed_psbt TEXT,
    destination_outputs_hash VARCHAR(64) NOT NULL CHECK (length(destination_outputs_hash) = 64),
    destination_address VARCHAR(128) NOT NULL,
    amount_sats BIGINT NOT NULL CHECK (amount_sats > 0),
    selected_inputs_hash VARCHAR(64) NOT NULL CHECK (length(selected_inputs_hash) = 64),
    selected_outpoints TEXT NOT NULL DEFAULT '',
    change_output_hash VARCHAR(64) CHECK (change_output_hash IS NULL OR length(change_output_hash) = 64),
    fee_rate BIGINT NOT NULL DEFAULT 0 CHECK (fee_rate >= 0),
    estimated_fee_sats BIGINT NOT NULL DEFAULT 0 CHECK (estimated_fee_sats >= 0),
    status VARCHAR(48) NOT NULL CHECK (status IN ('DRAFT','UNSIGNED_CREATED','WAITING_EXTERNAL_SIGNATURE','SIGNED_SUBMITTED','VALIDATED','REJECTED_TAMPERED','REJECTED_POLICY','BROADCASTED','CONFIRMED','FAILED_SAFE')),
    broadcast_txid VARCHAR(128),
    expires_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP + INTERVAL '24 hours'),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financial.tax_events (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.users_credentials(id) ON DELETE CASCADE,
    jurisdiction VARCHAR(32) NOT NULL DEFAULT 'UNSPECIFIED',
    event_type VARCHAR(48) NOT NULL CHECK (event_type IN ('DEPOSIT_INTERNAL','WITHDRAWAL_SELF_CUSTODY','PAYMENT_SPEND','INTERNAL_TRANSFER','LIGHTNING_OPEN','LIGHTNING_CLOSE','FEE_PAID','COLD_WALLET_OBSERVED_IN','COLD_WALLET_OBSERVED_OUT')),
    asset VARCHAR(16) NOT NULL DEFAULT 'BTC',
    quantity_sats BIGINT NOT NULL CHECK (quantity_sats > 0),
    fair_market_value NUMERIC(24, 8),
    fiat_currency VARCHAR(8),
    cost_basis NUMERIC(24, 8),
    acquisition_date TIMESTAMP,
    disposal_date TIMESTAMP,
    source_txid VARCHAR(128),
    account_id UUID,
    card_id UUID,
    wallet_id UUID,
    classification VARCHAR(64) NOT NULL DEFAULT 'USER_CLASSIFICATION_PENDING',
    metadata_redacted TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    purge_after TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP + INTERVAL '24 hours'),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financial.bitcoin_account_audit_events (
    id UUID PRIMARY KEY,
    actor_type VARCHAR(32) NOT NULL,
    actor_id VARCHAR(128),
    action VARCHAR(96) NOT NULL,
    entity_type VARCHAR(64) NOT NULL,
    entity_id VARCHAR(128) NOT NULL,
    metadata_redacted TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bitcoin_accounts_user ON financial.bitcoin_accounts(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bitcoin_ledger_accounts_account_unique ON financial.bitcoin_ledger_accounts(bitcoin_account_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_internal_btc_cards_account_unique ON financial.internal_btc_cards(bitcoin_account_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_internal_btc_cards_ledger_unique ON financial.internal_btc_cards(ledger_account_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_receiving_requests_public_code ON financial.receiving_requests(public_code);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bitcoin_ledger_entries_idempotency ON financial.bitcoin_ledger_entries(idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cold_wallets_account_unique ON financial.cold_wallets(account_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cold_wallet_addresses_address ON financial.cold_wallet_addresses(address);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cold_wallet_utxos_outpoint ON financial.cold_wallet_utxos(txid, vout);
CREATE INDEX IF NOT EXISTS idx_psbt_workflows_status ON financial.psbt_workflows(status);
CREATE INDEX IF NOT EXISTS idx_tax_events_purge ON financial.tax_events(purge_after);
CREATE INDEX IF NOT EXISTS idx_bitcoin_account_audit_created ON financial.bitcoin_account_audit_events(created_at);
