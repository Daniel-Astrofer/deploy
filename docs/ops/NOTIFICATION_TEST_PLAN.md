# Notification Test Plan

8 validation scenarios for the KFE financial notification pipeline. Ensures correct event emission, ordering, idempotency, and failure isolation.

All tests assume:
- `kfe_financial_notification_outbox` table active
- Notification relay (push/websocket) configured
- Transactional boundaries: notification INSERT only after DB COMMIT
- Dead-letter recovery via periodic job

---

## Validation 1: Event only after commit — rollback does not send

**Description:** A notification is written during a transaction. If the transaction rolls back, no notification event is emitted.

**Setup:**
1. Begin a transaction that creates a deposit entry AND inserts an outbox notification
2. Force rollback (e.g., constraint violation or explicit rollback)
3. Check outbox and push relay

**Execution:**
- INSERT into `kfe_transaction` + `kfe_financial_notification_outbox` in same tx
- Rollback occurs

**Verification:**
- No row in `kfe_financial_notification_outbox` for the event
- No push notification sent
- No user-facing event generated
- Audit log confirms tx rollback, no notification emitted

---

## Validation 2: Retry does not duplicate

**Description:** A notification delivery fails and is retried. Only one notification reaches the user.

**Setup:**
1. Create a successful deposit → notification written to outbox
2. Configure push relay to fail on first attempt (mock HTTP 500)
3. Let retry job pick up and deliver

**Execution:**
- Outbox worker fetches PENDING notification → delivery fails
- Worker increments `attempts`, sets `next_attempt_at` for later
- Second attempt succeeds

**Verification:**
- `attempts = 2`, `delivered_at` set on success
- Only one push notification reaches the user
- Duplicate detection: push relay uses `event_id` as idempotency key
- `kfe_idempotency_claim` row prevents double delivery

---

## Validation 3: Push failure does not rollback ledger

**Description:** A push notification fails after the ledger transaction committed. The ledger state is not rolled back.

**Setup:**
1. Complete a deposit — ledger committed successfully
2. Outbox worker attempts push delivery → fails network/downstream

**Execution:**
- Ledger commit succeeded (transaction + movement persisted)
- Outbox insert also committed (within same tx)
- Worker delivery fails independently

**Verification:**
- Ledger state unchanged (deposit confirmed)
- `kfe_financial_notification_outbox` row: `status = 'PENDING'`, `attempts = 1`
- `next_attempt_at` set for retry
- No compensating ledger entry
- Notification ultimately delivered on retry (or dead-lettered after max attempts)
- User balance correct regardless of notification delivery

---

## Validation 4: Event ordering — PROCESSING before BROADCAST before CONFIRMED

**Description:** For a withdrawal lifecycle, notifications arrive in causal order.

**Setup:**
1. Submit a withdrawal request
2. Track all outbox notifications by `event_id` order

**Execution:**
- Withdrawal created → `event_type = 'PROCESSING'`
- Transaction broadcast to network → `event_type = 'BROADCAST'`
- Transaction confirmed (6 blocks) → `event_type = 'CONFIRMED'`

**Verification:**
- Three distinct outbox rows with monotonically increasing `created_at`
- Event order: PROCESSING → BROADCAST → CONFIRMED
- Each event has unique `event_id`, linked by `transaction_id`
- Outbox worker delivers in FIFO order (by `created_at`)
- User sees sequential status updates in UI

---

## Validation 5: Conflict generates compensating notification

**Description:** When a transaction is marked CONFLICTED (double-spend, reorg), a compensating notification is emitted.

**Setup:**
1. Deposit credited and confirmed → CONFIRMED notification sent
2. Reorg removes the deposit (Scenario 6 from Bitcoin tests)
3. Compensating debit entry created

**Execution:**
- Conflict detected → `network_status = 'CONFLICTED'`
- Compensating movement created
- Notification generated

**Verification:**
- Outbox row: `event_type = 'CONFLICTED'` or `'REVERSED'`
- `payload_json` includes original transaction details and reversal info
- User notified: "Deposit reversed due to chain reorganization"
- Notification linked to both original and compensating transaction IDs

---

## Validation 6: Lightning and Bitcoin use same event contract

**Description:** Both Lightning payments and on-chain transactions use the same `kfe_financial_notification_outbox` schema and event contract.

**Setup:**
1. Complete a Lightning payment (from Lightning Scenario 1)
2. Complete an on-chain deposit (from Bitcoin Scenario 1)
3. Inspect outbox rows for both

**Execution:**
- Both flows write to `kfe_financial_notification_outbox`
- Compare `event_type` values, `payload_json` structure

**Verification:**
- Both use same table, same columns, same status flow (PENDING → DELIVERED)
- `event_type` vocabulary shared: PROCESSING, BROADCAST, CONFIRMED, FAILED, REVERSED
- `payload_json` follows shared schema: `{event_id, transaction_id, event_type, amount, currency, network, timestamp, details}`
- No Lightning-specific or Bitcoin-specific outbox tables
- Outbox worker processes both uniformly

---

## Validation 7: No notification port in production → startup fails

**Description:** KFE starts in production without notification relay configured. Fast-fail prevents silent notification loss.

**Setup:**
1. Remove notification relay configuration (push service URL, credentials)
2. Set `spring.profiles.active = production`
3. Start KFE

**Execution:**
- Health indicator checks notification relay availability
- Required in production profile

**Verification:**
- Application fails to start
- Error: "Notification relay required in production but not configured"
- Health endpoint returns DOWN with detail: "notification_relay: UNAVAILABLE"
- No partial startup (all or nothing)

---

## Validation 8: Dead-letter recovery after multiple retries

**Description:** A notification fails 5 retry attempts. KFE moves it to dead-letter state for manual recovery.

**Setup:**
1. Configure max retry attempts = 5
2. Create a notification with a permanently failing push target (invalid endpoint)
3. Let outbox worker retry

**Execution:**
- Worker fetches PENDING notification
- Attempt 1-5: all fail (network error, invalid endpoint)
- After attempt 5: status updated

**Verification:**
- `status = 'DEAD_LETTER'`
- `attempts = 5`
- `next_attempt_at` set far in future or NULL
- Dead-letter monitoring alert triggered (≥1 row with DEAD_LETTER for >5 min)
- Manual recovery: operator fixes downstream, resets status to PENDING
- `payload_json` preserved for manual replay
- Admin endpoint: `GET /api/admin/kfe/notifications/dead-letter` lists all DEAD_LETTER entries

---

## Outbox Worker Configuration

| Parameter | Default | Description |
|---|---|---|
| `notification.outbox.poll_interval_ms` | 5000 | Poll interval for PENDING notifications |
| `notification.outbox.max_attempts` | 5 | Max delivery attempts before dead-letter |
| `notification.outbox.retry_backoff_ms` | [1000, 2000, 4000, 8000, 16000] | Backoff per attempt (exponential) |
| `notification.outbox.claim_timeout_ms` | 30000 | Duration a worker claims a row for processing |
| `notification.outbox.shard_count` | 4 | Parallel worker shards for throughput |
| `notification.outbox.dead_letter.alert_threshold_ms` | 300000 | Alert if DEAD_LETTER > 5 min |

## Test Infrastructure

- Mock push relay: `POST /mock/notify` that returns 200 (success) or 500 (failure) on demand
- Outbox inspection: `SELECT * FROM kfe_financial_notification_outbox WHERE transaction_id = :id ORDER BY created_at`
- Dead-letter list: `SELECT * FROM kfe_financial_notification_outbox WHERE status = 'DEAD_LETTER'`
- Force retry: `UPDATE kfe_financial_notification_outbox SET status = 'PENDING', attempts = 0 WHERE id = :id`
