# Database Migration Plan — Security Audit Hardening (V49)

Phased migration adding 20 columns to `financial.transactions_master` and 3 new tables for notification outbox, network observation logging, and idempotency claims.

Flyway migration file:
`kerosene-kfe:src/main/resources/db/migration/V49__security_audit_hardening.sql`.

---

## New Columns: `financial.transactions_master`

All columns are nullable in Phase 1. No NOT NULL constraint until Phase 5.

| Column | Type | Purpose |
|---|---|---|
| `network_status` | VARCHAR(32) | Granular on-chain state: MEMPOOL, MINED_UNCONFIRMED, CONFIRMED_3, CONFIRMED_6, DROPPED, CONFLICTED, REPLACED, REORGED |
| `accounting_status` | VARCHAR(32) | Ledger state: CREDIT_PENDING, CREDIT_RECEIVED, CREDIT_PROCESSING, CREDIT_CONFIRMED, DEBIT_PREPARED, DEBIT_BROADCAST, SETTLED |
| `business_status` | VARCHAR(32) | Business-domain state: payment lifecycle phases |
| `finality_status` | VARCHAR(32) | Finality guarantee: PENDING, PROVISIONAL, FINAL, REVERSED |
| `network_first_seen_at` | TIMESTAMPTZ | First mempool/chain observation |
| `network_last_seen_at` | TIMESTAMPTZ | Most recent chain probe timestamp |
| `network_not_found_since` | TIMESTAMPTZ | When tx disappeared from mempool/chain |
| `network_not_found_count` | INTEGER DEFAULT 0 | Consecutive probe cycles without tx |
| `block_hash` | VARCHAR(64) | Block containing this tx |
| `block_height` | INTEGER | Block height of containing block |
| `replaced_by_txid` | VARCHAR(64) | RBF replacement txid |
| `finalized_at` | TIMESTAMPTZ | When finality reached (6 confirmations) |
| `reconciliation_reason` | TEXT | Why reconciliation occurred (REORG, DOUBLE_SPEND, POST_RESTART, etc.) |
| `conflicted_at` | TIMESTAMPTZ | When conflict detected (double-spend/reorg) |
| `replacement_txid` | VARCHAR(64) | The replacement tx in RBF scenario |
| `prepared_raw_tx_hash` | VARCHAR(64) | Hash of prepared unsigned tx (for broadcast recovery) |
| `confirmation_monitoring_active` | BOOLEAN DEFAULT true | Whether observer is tracking confirmations |
| `last_chain_probe_at` | TIMESTAMPTZ | Last chain probe timestamp |
| `last_chain_probe_status` | VARCHAR(32) | Result of last probe: OK, NODE_UNAVAILABLE, WALLET_NOT_LOADED, ERROR |
| `mempool_last_seen_at` | TIMESTAMPTZ | Last mempool presence timestamp |

---

## New Tables

### `financial.kfe_financial_notification_outbox`

Transactional outbox for financial event notifications. Ensures at-least-once delivery with idempotent receivers.

| Column | Type | Purpose |
|---|---|---|
| `id` | UUID PK | Internal row ID |
| `event_id` | UUID UNIQUE | Business event ID (idempotency key downstream) |
| `user_id` | BIGINT NOT NULL | Recipient user |
| `transaction_id` | UUID | FK to `financial.transactions_master` (nullable for system events) |
| `event_type` | VARCHAR(64) | PROCESSING, BROADCAST, CONFIRMED, FAILED, REVERSED, CONFLICTED |
| `payload_json` | TEXT | Full event payload (JSON) |
| `status` | VARCHAR(32) | PENDING, DELIVERING, DELIVERED, FAILED, DEAD_LETTER |
| `attempts` | INTEGER | Delivery retry count |
| `next_attempt_at` | TIMESTAMPTZ | Scheduled retry time (backoff) |
| `claimed_by` | VARCHAR(128) | Worker instance that claimed this row |
| `claimed_until` | TIMESTAMPTZ | Lease expiry for claim |
| `created_at` | TIMESTAMPTZ | Row creation time |
| `delivered_at` | TIMESTAMPTZ | Successful delivery timestamp |

### `financial.kfe_network_observation_log`

Append-only audit log of every chain/mempool observation for a transaction.

| Column | Type | Purpose |
|---|---|---|
| `id` | UUID PK | Log entry ID |
| `transaction_id` | UUID NOT NULL | FK to `financial.transactions_master` |
| `txid` | VARCHAR(64) | Bitcoin txid at time of observation |
| `state` | VARCHAR(32) | Observed chain state: MEMPOOL, MINED, CONFIRMED, DROPPED |
| `confirmations` | INTEGER | Confirmation count at observation |
| `block_hash` | VARCHAR(64) | Block hash at observation |
| `block_height` | INTEGER | Block height at observation |
| `observed_at` | TIMESTAMPTZ | Observation timestamp |

### `financial.kfe_idempotency_claim`

Distributed idempotency guard for operations that must execute exactly-once across restarts/multiple workers.

| Column | Type | Purpose |
|---|---|---|
| `claim_key` | VARCHAR(256) PK | Idempotency key (e.g., `deposit:<txid>`, `lightning:<payment_hash>`) |
| `claim_token` | UUID NOT NULL | Unique token for this claim attempt |
| `principal_id` | VARCHAR(128) | Actor claiming (user ID or system) |
| `status` | VARCHAR(32) | CLAIMED, PROCESSING, COMPLETED, EXPIRED |
| `fingerprint_hash` | VARCHAR(64) | Content fingerprint for duplicate detection |
| `response_json` | TEXT | Cached response for idempotent replay |
| `claimed_at` | TIMESTAMPTZ | When claim was created |
| `lease_expires_at` | TIMESTAMPTZ | Lease timeout for incomplete claims |
| `completed_at` | TIMESTAMPTZ | When processing finished |

### Balance Movement Idempotency Constraint

Adds unique constraint on `financial.balance_movements` preventing duplicate movements for the same transaction+type+wallet combination.

Note: V37 already has a partial unique index on `(transaction_id, movement_type)` for credit types only. This adds a full constraint covering all movement types with `wallet_id` as additional discriminator.

```sql
ALTER TABLE financial.balance_movements ADD CONSTRAINT IF NOT EXISTS uq_movement_idempotent
    UNIQUE (transaction_id, movement_type, wallet_id);
```

---

## Migration Phases

### Phase 1 — Add nullable columns (V49)

Deploy migration. All new columns are nullable with defaults. Existing code writes to old columns only. New code paths gated behind feature flag.

**Deployment:** Zero-downtime. No data migration. No application restart required for column adds (PostgreSQL).

**Verification:**
```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'transactions_master'
  AND table_schema = 'financial'
  AND column_name IN ('network_status', 'accounting_status', 'business_status',
                       'finality_status', 'network_first_seen_at', 'block_hash',
                       'replaced_by_txid', 'confirmation_monitoring_active');
```

### Phase 2 — Backfill existing rows

Backfill new columns from existing `status` field for all rows where the old field has data.

```sql
-- Map legacy status to new network_status
UPDATE financial.transactions_master
SET network_status = CASE
    WHEN status = 'EXECUTING' THEN 'MEMPOOL'
    WHEN status = 'SETTLED' THEN 'CONFIRMED_6'
    WHEN status = 'FAILED' THEN 'DROPPED'
    ELSE status
END
WHERE network_status IS NULL;

-- Map to accounting_status using direction + status
UPDATE financial.transactions_master
SET accounting_status = CASE
    WHEN direction = 'INBOUND' AND status = 'EXECUTING' THEN 'CREDIT_PENDING'
    WHEN direction = 'INBOUND' AND status = 'SETTLED' THEN 'CREDIT_CONFIRMED'
    WHEN direction = 'OUTBOUND' AND status = 'INTENT' THEN 'DEBIT_PREPARED'
    WHEN direction = 'OUTBOUND' AND status = 'EXECUTING' THEN 'DEBIT_BROADCAST'
    WHEN direction = 'OUTBOUND' AND status = 'SETTLED' THEN 'SETTLED'
    ELSE accounting_status
END
WHERE accounting_status IS NULL;
```

**Verification:**
```sql
SELECT COUNT(*) AS null_network_status
FROM financial.transactions_master WHERE network_status IS NULL;

SELECT COUNT(*) AS null_accounting_status
FROM financial.transactions_master WHERE accounting_status IS NULL;
```

### Phase 3 — Enable new writers

Deploy code that writes to BOTH old `status` column AND new columns simultaneously. Feature flag: `FEATURE_DUAL_WRITE_ENABLED`.

**Rollback:** Disable flag → writes stop on new columns but old column still has data.

### Phase 4 — Enable new readers

Deploy code that reads from new columns (falling back to old column if new is NULL). Feature flag: `FEATURE_NEW_READ_ENABLED`.

**Rollback:** Disable flag → readers fall back to old column.

### Phase 5 — Add NOT NULL constraints

After all rows backfilled and dual-write confirmed for ≥1 week with no NULL entries:

```sql
ALTER TABLE financial.transactions_master ALTER COLUMN network_status SET NOT NULL;
ALTER TABLE financial.transactions_master ALTER COLUMN accounting_status SET NOT NULL;
ALTER TABLE financial.transactions_master ALTER COLUMN confirmation_monitoring_active SET NOT NULL;
ALTER TABLE financial.transactions_master ALTER COLUMN network_not_found_count SET NOT NULL;
```

This phase requires application deployment window (exclusive lock on ALTER COLUMN SET NOT NULL on large tables).

### Phase 6 — Remove old behavior

After ≥2 weeks of new readers + writers confirmed stable:
- Stop writing to old `status` column
- Remove fallback reads from old column
- Drop old `status` column (or deprecate with `_deprecated` suffix first)

---

## Rollback Plan

| Phase | Rollback Action |
|---|---|
| Phase 1 | Drop new columns (no code depends on them) |
| Phase 2 | Reversible (data loss: backfill undone) |
| Phase 3 | Disable dual-write flag, drop new columns |
| Phase 4 | Disable new-read flag, drop new columns |
| Phase 5 | NOT NULL irreversible without column drop/recreate |
| Phase 6 | Requires restore from backup to recover old column |

---

## Risk Assessment

| Risk | Mitigation |
|---|---|
| Exclusive lock on ALTER TABLE | All adds use `IF NOT EXISTS`; nullable adds are instant in PG ≥11 |
| Large table backfill | Batch UPDATE with `LIMIT 10000` in loop, run during low-traffic window |
| Dual-write overhead | Minimal (extra columns in same UPDATE) |
| Phase 5 lock contention | Schedule during maintenance window; use `SET NOT NULL` with `NOT VALID` then validate later |
| New table growth (observation log) | Partition by month on `observed_at`; configure retention policy (90 days) |
