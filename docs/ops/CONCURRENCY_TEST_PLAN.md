# Concurrency Test Plan

Race condition scenarios and expected behavior for KFE transaction and settlement paths.

---

## Scenario 1: Two payments with same idempotency key

**Setup:** Two concurrent HTTP requests submit a payment with identical `idempotency_key`.

**Concurrent operations:**
- Thread A: `POST /kfe/transactions` with key `abc123`
- Thread B: `POST /kfe/transactions` with key `abc123`

**Expected outcome:** One request creates the transaction and returns `201`. The other receives `409 Conflict` because the idempotency key already maps to an existing transaction.

**Verification:**
- Exactly one `KfeTransactionEntity` row exists for key `abc123`
- One balance movement exists (not two)
- One audit event logged for the winning request
- The losing request audit log shows the idempotency rejection

---

## Scenario 2: Two credits of same txid

**Setup:** Inbound settlement monitor processes the same Bitcoin txid twice (duplicate block notification, relay race).

**Concurrent operations:**
- Worker A: detects txid `dead...beef` and calls `recordCreditIfAbsent`
- Worker B: same txid, same call, concurrent DB access

**Expected outcome:** Only one `CreditMovement` row created. The second call returns the existing row (no insert).

**Verification:**
- `SELECT COUNT(*) FROM credit_movements WHERE provider_reference = 'dead...beef'` returns 1
- The credited wallet balance increments exactly once
- `KfeBalanceMetrics.recordDualCreditSkip` is called for the duplicate

---

## Scenario 3: Two workers settling same transaction

**Setup:** Settlement gate picks up the same `LOCKED` transaction in two concurrent scheduler ticks.

**Concurrent operations:**
- Worker A: transitions LOCKED → EXECUTING, debits source wallet
- Worker B: same transaction, same transition attempt

**Expected outcome:** Worker A wins (status advances to EXECUTING). Worker B sees state is no longer LOCKED and exits. Only one debit recorded.

**Verification:**
- Transaction state is EXECUTING (not SETTLED pre-maturely)
- Source wallet balance decremented exactly once
- Only one debit movement row exists
- State machine rejects the second transition (LOCKED already consumed)

---

## Scenario 4: Confirmation and cancellation concurrent

**Setup:** User cancels a transaction while the confirmation monitor detects 1-conf.

**Concurrent operations:**
- Thread A: confirmation monitor transitions BROADCAST → CONFIRMING
- Thread B: cancel request transitions BROADCAST → CANCELLED

**Expected outcome:** One wins based on DB lock ordering. The loser sees state no longer BROADCAST and aborts. State is consistent — either CONFIRMING or CANCELLED, never both.

**Verification:**
- Final status is CANCELLED or CONFIRMING, not both
- If CANCELLED, no confirmation movement exists
- If CONFIRMING, the cancel attempt left an audit log entry showing it was rejected because state changed
- State machine `transition()` threw `IllegalStateException` for the loser

---

## Scenario 5: Failure and success concurrent

**Setup:** Provider returns success HTTP 200 but the connection drops before KFE reads the response body. Retry logic fires before the first attempt times out.

**Concurrent operations:**
- Thread A: original provider call succeeds, starts persisting SETTLED
- Thread B: retry provider call succeeds, starts persisting SETTLED (again)

**Expected outcome:** First thread to commit wins. Second sees status already SETTLED and skips. No double credit/debit.

**Verification:**
- Final status is SETTLED
- Exactly one credit movement, one debit movement
- Outbox has exactly one notification event
- `KfeBalanceMetrics.recordDualCreditSkip` or equivalent fired for duplicate

---

## Scenario 6: Reconciliation and confirmation monitor concurrent

**Setup:** A BROADCAST transaction triggers both the confirmation monitor and the reconciliation scanner simultaneously.

**Concurrent operations:**
- Worker A (confirmation monitor): polls provider, sees 1-conf, transitions to CONFIRMING
- Worker B (reconciliation): detects the same tx, starts reconciliation path

**Expected outcome:** Confirmation monitor wins (it uses the normal path). Reconciliation sees state is no longer in a reconcilable status and skips.

**Verification:**
- Transaction reaches SETTLED via normal confirmation path
- No reconciliation movement created
- Audit log shows reconciliation aborted due to state change
- No double action — either normal confirmation OR reconciliation, not both

---

## Scenario 7: Duplicate notification

**Setup:** Outbox processor picks up the same notification event twice (crash between dequeue and ack, or dual-watcher race).

**Concurrent operations:**
- Processor A: dequeues notification event `evt-42`, sends push, acks
- Processor B: dequeues same `evt-42`, sends push (potential duplicate)

**Expected outcome:** Exactly one push delivery to the user. The second is deduplicated by the outbox or the push relay.

**Verification:**
- User receives exactly one push notification
- Outbox event status is `SENT` exactly once
- Audit log shows ack from Processor A, duplicate skip from Processor B

---

## Scenario 8: Duplicate fee settlement

**Setup:** Fee settlement job fires twice for the same settled transaction (e.g., during redeploy, clock skew).

**Concurrent operations:**
- Job A: calculates fee for transaction `tx-99`, debits fee from wallet
- Job B: same transaction, same fee calculation, concurrent

**Expected outcome:** Fee is debited exactly once. Second attempt sees existing fee movement and skips.

**Verification:**
- Exactly one fee debit movement for `tx-99`
- `KfeFeeSettlementService.recordFeeSkip` called for duplicate
- Wallet balance reduced by fee amount exactly once
