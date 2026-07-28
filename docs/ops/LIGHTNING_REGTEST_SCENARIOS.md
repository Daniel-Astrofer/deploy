# Lightning Regtest Test Scenarios

14 Lightning Network scenarios for KFE payment pipeline. Validates KFE-LND integration against regtest LND nodes.

All scenarios assume:
- Regtest LND node running (single or multi-node polar/simnet)
- KFE vault mesh with Lightning rail configured
- `kfe_transaction` with `network_status`, `accounting_status`, `payment_hash` columns
- `lightning_liquidity_reservations` table active
- Lightning rail observer polling every 5s + streaming invoice events

---

## Scenario 1: Normal invoice payment

**Description:** KFE pays a standard Lightning invoice via LND. Full lifecycle from intent to settled.

**Setup:**
1. Generate invoice on recipient LND node: `lncli addinvoice --amt=50000`
2. Submit payment request to KFE

**Execution:**
- KFE decodes invoice, validates network + expiry
- Reserves Lightning liquidity (HELD in `lightning_liquidity_reservations`)
- Calls `routerrpc.SendPaymentV2` on LND
- LND routes payment through channel graph
- HTLC settles → invoice marked SETTLED

**Verification:**
- `network_status = 'SETTLED'`
- `accounting_status = 'PAYMENT_COMPLETED'`
- Liquidity reservation: `status = 'CONSUMED'`
- Balance movement: debit for payment amount + fee
- Payment preimage stored for proof

---

## Scenario 2: Zero-amount invoice with amount specified

**Description:** A recipient creates a zero-amount invoice (no amount field). KFE specifies the payment amount.

**Setup:**
1. Generate invoice: `lncli addinvoice --memo="test"` (no `--amt`)
2. Submit payment to KFE with `amount_sats = 25000`

**Execution:**
- KFE detects zero-amount invoice
- Validates sender-specified amount against limits
- Creates liquidity reservation for 25000 sats
- Pays invoice with `amt` field in `SendPaymentRequest`

**Verification:**
- Payment succeeds with specified amount
- Recipient receives exactly 25000 sats
- `network_status = 'SETTLED'`
- No overflow/underflow in reservation

---

## Scenario 3: Expired invoice rejection

**Description:** An invoice has a 60s expiry and KFE attempts payment after expiry. Rejected before LND call.

**Setup:**
1. Generate invoice with `--expiry=60`
2. Wait 65 seconds
3. Submit payment to KFE

**Execution:**
- KFE decodes invoice, extracts expiry timestamp
- Compares `expiry_timestamp` with current time
- Detects expiry before attempting payment

**Verification:**
- Payment rejected with error: "Invoice expired"
- No liquidity reserved
- No LND RPC call made
- `accounting_status = 'INVOICE_EXPIRED'`
- User notified immediately

---

## Scenario 4: Wrong network invoice rejection

**Description:** A mainnet invoice is submitted to a testnet KFE. Rejected on network mismatch.

**Setup:**
1. Generate mainnet invoice (or simulate with wrong network prefix)
2. Submit to testnet-configured KFE

**Execution:**
- KFE decodes invoice, checks `chain` field
- Detects network mismatch

**Verification:**
- Payment rejected: "Invoice network mismatch: expected testnet"
- No state mutation
- `accounting_status = 'WRONG_NETWORK'`

---

## Scenario 5: Already-paid invoice rejection

**Description:** An invoice that was already settled is submitted again. KFE detects duplicate and rejects.

**Setup:**
1. Generate invoice, pay it successfully (Scenario 1)
2. Submit same invoice to KFE again

**Execution:**
- KFE checks local `payment_hash` against completed payments
- Finds existing SETTLED transaction
- Rejects

**Verification:**
- Payment rejected: "Invoice already paid"
- Idempotency: no second payment, no double debit
- Original payment untouched
- Response references original transaction ID

---

## Scenario 6: In-flight payment tracking

**Description:** Track a payment while it's routing (not yet settled). Status transitions: PENDING → IN_FLIGHT → SETTLED.

**Setup:**
1. Generate invoice
2. Start payment (do not settle immediately — add channel delay or hold invoice)

**Execution:**
- Payment starts → `network_status = 'PENDING'`
- LND reports IN_FLIGHT → `network_status = 'IN_FLIGHT'`
- HTLC settles → `network_status = 'SETTLED'`

**Verification:**
- All three state transitions logged in `kfe_network_observation_log`
- User sees "Sending..." → "Routing..." → "Sent" in UI
- Liquidity reservation lifecycle: HELD during PENDING+IN_FLIGHT, CONSUMED on SETTLED
- Each state change timestamped

---

## Scenario 7: Timeout after LND success — payment state recovery

**Description:** LND settles a payment but KFE misses the confirmation (crash/network). KFE recovers on restart via invoice polling.

**Setup:**
1. Start payment
2. Simulate KFE crash after LND sends `SendPaymentV2` but before KFE persists SETTLED
3. Restart KFE

**Execution:**
- On restart, KFE queries `lncli lookupinvoice <payment_hash>`
- Finds invoice state = SETTLED
- Reconciles local state

**Verification:**
- `network_status` updated to `SETTLED` after recovery
- `reconciliation_reason = 'POST_RESTART_RECOVERY'`
- No duplicate payment
- Balance correctly debited (idempotent)
- Audit log: "recovered settled payment after restart"

---

## Scenario 8: Route failure — graceful error

**Description:** LND fails to find a route to the recipient. KFE reports clear error, releases reservation.

**Setup:**
1. Isolate recipient node (close all channels to it)
2. Submit payment to unreachable recipient

**Execution:**
- LND `SendPaymentV2` returns `FAILURE_REASON_NO_ROUTE`
- KFE catches failure

**Verification:**
- `network_status = 'ROUTE_FAILURE'`
- Liquidity reservation: `status = 'RELEASED'`
- User error: "No route to recipient"
- Balance NOT deducted (funds released)
- No retry by default (configurable max attempts)

---

## Scenario 9: Fee exceeds limit — rejection

**Description:** LND estimates routing fee > configured maximum. KFE rejects before sending.

**Setup:**
1. Configure `lightning.max_fee_percent = 5` or `max_fee_sats = 1000`
2. Set up route that requires 2000 sat fee for 10000 sat payment (20%)
3. Submit payment

**Execution:**
- KFE calls `QueryRoutes` to estimate fee
- Compares estimate against limits
- Rejects

**Verification:**
- Payment rejected: "Fee exceeds limit: 2000 sat > 1000 sat max"
- `accounting_status = 'FEE_EXCEEDS_LIMIT'`
- No LND payment attempt made
- No liquidity reserved

---

## Scenario 10: LND restart during payment — recovery

**Description:** LND restarts while a payment is IN_FLIGHT. KFE reconnects and recovers payment state.

**Setup:**
1. Start payment → reaches IN_FLIGHT
2. Restart LND (`lncli stop` then restart)
3. Wait for KFE to reconnect

**Execution:**
- KFE detects gRPC stream disconnection
- Reconnects with backoff
- Queries outstanding payments: `lncli listpayments --include_incomplete`
- Resumes tracking

**Verification:**
- Payment completes (or fails gracefully) after LND recovery
- `network_status` reflects final state
- No zombie reservations
- PayStream re-subscribed after reconnection
- `last_chain_probe_status` logs LND unavailability window

---

## Scenario 11: Invoice event lost in stream → recovered by polling

**Description:** The LND invoice subscription stream drops a SETTLED event. KFE recovers via periodic polling.

**Setup:**
1. Start payment normally
2. Suppress/ignore the SETTLED event from PayStream (simulated)
3. Wait for polling cycle

**Execution:**
- Polling cycle runs: `lncli lookupinvoice <payment_hash>`
- Finds SETTLED state
- Reconciles

**Verification:**
- Payment marked SETTLED within one polling interval
- `reconciliation_reason = 'POLLING_RECOVERY'`
- No duplicate processing
- Polling interval: configurable (default 30s, reduce to 5s for test)

---

## Scenario 12: Invoice settled near expiry — reconciliation credit

**Description:** An invoice settles just before expiry. KFE credits as normal but logs near-expiry reconciliation.

**Setup:**
1. Generate invoice with `--expiry=120`
2. Delay payment until 115s (5s before expiry)
3. Pay invoice

**Execution:**
- LND settles invoice at t=117s (3s before expiry)
- KFE processes settlement

**Verification:**
- Payment succeeds normally
- `reconciliation_reason = 'NEAR_EXPIRY_SETTLEMENT'`
- Timestamp delta logged: "settled 3s before expiry"
- No special handling required beyond logging

---

## Scenario 13: Underpayment — partial tracking

**Description:** A sender pays less than the invoice amount. LND rejects, KFE captures partial attempt info.

**Setup:**
1. Generate invoice for 50000 sats
2. Attempt payment with `amt=30000` (intentional underpay)

**Execution:**
- LND `SendPaymentV2` fails with `FAILURE_REASON_INCORRECT_PAYMENT_DETAILS`
- KFE logs attempt

**Verification:**
- `network_status = 'REJECTED_UNDERPAYMENT'`
- Error details: "Invoice requires 50000, attempted 30000"
- Liquidity NOT consumed
- No balance deduction

---

## Scenario 14: Idempotency after restart — replay protection

**Description:** After a crash/restart, a payment event is replayed. KFE idempotently ignores duplicate.

**Setup:**
1. Complete a successful payment (Scenario 1)
2. Restart KFE
3. Replay the SETTLED event for the same payment_hash

**Execution:**
- KFE processes event
- Checks `payment_hash` against completed transactions
- Idempotency guard fires

**Verification:**
- Only one transaction row for the payment
- `kfe_idempotency_claim` exists with `claim_key = 'lightning:<payment_hash>'`
- Second event logged as "duplicate, skipped"
- Balance unchanged (not double-debited)

---

## Test Infrastructure Notes

- Use Polar (Lightning regtest orchestration) or manual `lncli` on regtest
- `lncli addinvoice --amt=<sats> --expiry=<seconds>` for invoice generation
- `lncli sendpayment --pay_req=<invoice> --amt=<sats>` for manual verification
- `lncli listpayments --include_incomplete` for payment state inspection
- `lncli lookupinvoice <payment_hash>` for invoice state recovery
- LND restart: `lncli stop` + restart daemon, wait for KFE reconnection (~5s)
- Streaming events: subscribe via `lncli subscrib invoices` or gRPC `SubscribeInvoices`
- Polling fallback: configure `lightning.poll_interval_seconds=5` for fast tests
