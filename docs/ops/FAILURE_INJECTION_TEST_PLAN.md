# Failure Injection Test Plan

System recovery after failure at each step. Target: system converges to correct state without double spend or double credit after restart.

---

## Step 1: Reserve created → system must release on timeout

**Injection:** Kill the process after a reserve row is inserted but before the transaction reaches VALIDATING.

**Expected behavior:** Reserve has a TTL. Reserve sweeper picks up expired reserves and releases the locked funds. Transaction stays in INTENT or transitions to FAILED with audit reason `RESERVE_TIMEOUT`.

**Verification:**
- Reserve row `expires_at` has passed
- Sweeper updated reserve status to `RELEASED`
- Source wallet balance unlocked (available balance restored)
- Transaction status is FAILED
- Audit log shows `RESERVE_TIMEOUT` event

---

## Step 2: Outbox created → outbox processor must retry

**Injection:** Kill the process after an outbox event is persisted but before it is sent.

**Expected behavior:** Outbox processor on restart picks up the `PENDING` event and delivers it. If delivery fails, processor retries with backoff up to the max retry count.

**Verification:**
- Outbox event status transitions from PENDING → SENDING → SENT
- At least one delivery attempt logged
- After max retries exhausted, event status is DEAD_LETTER
- No duplicate delivery (outbox processor uses `SELECT ... FOR UPDATE SKIP LOCKED`)

---

## Step 3: Provider called → timeout handling, no duplicate

**Injection:** Provider HTTP call hangs. KFE timeout fires and aborts the call. The provider actually processed the payment before KFE timed out.

**Expected behavior:** KFE marks the transaction as REQUIRES_RECONCILIATION (not FAILED). Reconciliation later resolves by provider txid/payment_hash lookup. If the provider completed the payment, reconciliation transitions to SETTLED. If not, transitions to FAILED.

**Verification:**
- Transaction status is REQUIRES_RECONCILIATION after timeout
- No double debit — only one provider call was attempted
- Reconciliation scanner picks up the transaction
- After reconciliation, status is SETTLED or FAILED based on provider state
- Audit log records both timeout and reconciliation outcome

---

## Step 4: Provider executed, response lost → reconcile by txid/payment_hash

**Injection:** Provider returns HTTP 200 with payment result. Network drops before KFE reads the full response body.

**Expected behavior:** KFE sees the connection failure. Since the provider may have executed, KFE transitions to REQUIRES_RECONCILIATION. Reconciliation queries the provider by the original payment_hash or request reference. If provider shows the payment completed, reconciliation creates the credit movement and transitions to SETTLED.

**Verification:**
- Transaction status is REQUIRES_RECONCILIATION
- Reconciliation queries provider and finds the completed payment
- Credit movement created with provider reference
- Transaction transitions to SETTLED
- No duplicate provider call
- Idempotency guard fires if reconciliation re-runs

---

## Step 5: Txid persisted → confirmation monitor finds it

**Injection:** Kill the process after the provider txid is persisted in the transaction row but before the confirmation monitor starts tracking it.

**Expected behavior:** On restart, the confirmation monitor scans for transactions in BROADCAST status with a non-null provider_reference. It picks up the transaction and begins polling for confirmations.

**Verification:**
- Transaction with provider_reference and status BROADCAST exists after restart
- Confirmation monitor finds it within the next scan cycle
- Monitor begins polling the provider for confirmation count
- Transaction eventually reaches CONFIRMING and then SETTLED

---

## Step 6: Movement persisted → idempotent, no double credit

**Injection:** Kill the process after a debit movement is persisted but before the credit movement is persisted (half-written settlement).

**Expected behavior:** On restart, the reconciliation or settlement scanner detects the incomplete transaction. It re-evaluates and completes the missing credit movement. The debit movement is idempotent (detected by txid + movement type) so it is not duplicated.

**Verification:**
- Debit movement exists with provider_reference
- Credit movement was missing and is now created
- Both movements reference the same transaction_id
- Source wallet debited exactly once, destination credited exactly once
- `KfeBalanceMetrics.recordDualCreditSkip` not triggered (no duplicate was attempted)

---

## Step 7: Balance updated → atomic with movement

**Injection:** Kill the process inside the transaction block after the movement row is inserted but before the wallet balance column is updated.

**Expected behavior:** Since movement and balance update share the same DB transaction, the entire transaction rolls back. On restart, the system retries the settlement from scratch. No partial state persists.

**Verification:**
- No movement row exists for the failed transaction
- Wallet balance unchanged
- Transaction status reset to pre-settlement state (LOCKED or EXECUTING)
- Next scheduler tick picks up the transaction and retries settlement
- Balance correctly updated after successful retry
- ACID property verified: movement and balance are atomically committed

---

## Step 8: Status updated → consistent with ledger

**Injection:** Kill the process after the movement is persisted and balance is updated but before the transaction status is updated to SETTLED.

**Expected behavior:** Since status update and movement share the same DB transaction, the entire transaction rolls back. The movement and balance changes are reverted. On restart, the transaction remains in its previous state (BROADCAST or CONFIRMING) and settlement retries.

**Verification:**
- No movement row exists (rolled back)
- Wallet balance unchanged (rolled back)
- Transaction status is BROADCAST or CONFIRMING (not SETTLED)
- Settlement retries on next scheduler cycle
- After successful full settlement: status is SETTLED, movements exist, balance correct
- No inconsistency between ledger state and transaction status

---

## Step 9: Statement created → idempotent

**Injection:** Kill the process after a statement row is created but before the statement id is linked back to the transaction.

**Expected behavior:** On restart, the settlement path re-evaluates. The `recordStatementIfAbsent` method detects the existing statement by txid and returns it without creating a duplicate. The transaction is linked to the existing statement.

**Verification:**
- Exactly one statement row for the txid
- Transaction references the correct statement_id
- Statement fields (amount, direction, timestamp) are correct
- No duplicate statement created on retry

---

## Step 10: Notification event created → outbox delivers

**Injection:** Kill the process after the notification event is created in the outbox but before the outbox processor delivers it.

**Expected behavior:** On restart, the outbox processor scans for PENDING events and delivers them. The notification reaches the user exactly once (dedup by event_id).

**Verification:**
- Outbox event exists with status PENDING before restart
- After restart, outbox processor picks it up
- Event transitions to SENDING → SENT
- User receives notification
- No duplicate notification (event_id is unique in outbox)

---

## Step 11: Notification sent → no double delivery

**Injection:** Kill the process after the push notification is sent but before the outbox event is marked SENT. On restart, the outbox processor sees the event as PENDING again.

**Expected behavior:** Outbox processor retries delivery. The push relay or notification service deduplicates by event_id. The user receives at most one notification. The outbox eventually marks the event as SENT.

**Verification:**
- Outbox event transitions to SENT (even after retry)
- User received exactly one notification (verified in push relay logs)
- Dedup mechanism in push relay fired for the duplicate attempt
- Audit log shows retry attempt and dedup decision

---

## Restart Convergence

**Injection:** Kill the entire process at a random point during settlement of multiple transactions.

**Expected behavior:** After restart, all in-flight transactions converge to a terminal state (SETTLED, FAILED, or CANCELLED) without human intervention. No double spend. No double credit. All balances reconcile.

**Verification:**
- Every transaction with a pre-restart status other than terminal reaches a terminal status within 5 minutes
- `SUM(debit_movements) = SUM(credit_movements)` across all wallets
- Zero orphan movements (movements without matching transaction)
- Zero orphan reserves
- Outbox fully drained (no PENDING events older than max retry window)
- Audit log chain is continuous (no hash chain break)
- Wallet balances match `SUM(credit_movements) - SUM(debit_movements)` for each wallet
