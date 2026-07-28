# Bitcoin Regtest Test Scenarios

15 Bitcoin on-chain scenarios for KFE deposit/withdrawal pipeline. Validates KFE ledger integration against a Bitcoin Core regtest node.

All scenarios assume:
- Regtest Bitcoin Core node running (e.g., `bitcoind -regtest -rpcuser=...`)
- KFE vault mesh active with FROST group key
- `kfe_transaction` table with `network_status`, `accounting_status`, `finality_status` columns
- Network observer polling mempool + blocks every 5s

---

## Scenario 1: Zero-conf deposit — detected in mempool

**Description:** A deposit tx is broadcast but not yet mined. KFE detects it in the mempool and creates a pending credit entry.

**Setup:**
1. Generate a fresh regtest address from the vault mesh
2. Use `sendtoaddress` to fund it with 0.5 BTC
3. Do NOT mine a block

**Execution:**
- Network observer polls `getrawmempool`
- Detects txid in mempool
- Calls `getrawtransaction` to decode outputs
- Matches output script to vault mesh deposit address

**Verification:**
- `kfe_transaction` row created with `network_status = 'MEMPOOL'`
- `kfe_balance_movement` shows `CREDIT_PENDING`
- No `block_hash` or `block_height` set
- `network_first_seen_at` populated with current timestamp
- `confirmation_monitoring_active = true`

---

## Scenario 2: 1, 3, 6 confirmation deposit — credit progression

**Description:** Track deposit credit as confirmations accumulate: 1 conf (RECEIVED), 3 conf (PROCESSING), 6 conf (CONFIRMED).

**Setup:**
1. Broadcast deposit tx from Scenario 1 (or fresh one)
2. Mine 1 block → observe
3. Mine 2 more (3 total) → observe
4. Mine 3 more (6 total) → observe

**Execution:**
- Observer polls `gettransaction <txid>` after each block
- Updates `network_status` and `accounting_status` per confirmation count

**Verification:**

| Confirmations | network_status | accounting_status |
|---|---|---|
| 1 | MINED_UNCONFIRMED | CREDIT_RECEIVED |
| 3 | CONFIRMED_3 | CREDIT_PROCESSING |
| 6 | CONFIRMED_6 | CREDIT_CONFIRMED |

- `block_hash` and `block_height` set after block 1
- `finalized_at` set at 6 confirmations
- Balance available at each stage per business rules

---

## Scenario 3: RBF replacing a deposit — detection and orphan management

**Description:** A deposit tx is replaced via RBF (Replace-By-Fee). The original tx is orphaned; the replacement is tracked.

**Setup:**
1. Create tx A with `bumpfee`-enabled (opt-in RBF)
2. Broadcast tx A → detected in mempool
3. Create tx B (higher fee, same inputs) with `bumpfee`
4. Broadcast tx B

**Execution:**
- Observer detects tx B in mempool with same inputs as tx A
- Marks tx A as `REPLACED`
- Creates/updates tx B as active deposit

**Verification:**
- Tx A: `network_status = 'DROPPED'`, `replaced_by_txid = <B_txid>`
- Tx B: `network_status = 'MEMPOOL'`
- Only one active credit movement exists (for tx B)
- No double-credit created

---

## Scenario 4: Double-spend attack — conflict detection

**Description:** Two conflicting txs spend the same UTXO. KFE detects the conflict and marks one as invalid.

**Setup:**
1. Prepare two txs that spend the same input
2. Broadcast tx A → detected, credited
3. Broadcast conflicting tx B

**Execution:**
- Observer sees both txs in mempool (or B in block)
- Conflict detection via shared input UTXOs
- Resolves: tx in block wins; if both mempool, first-seen heuristic

**Verification:**
- Conflicting tx marked: `network_status = 'CONFLICTED'`, `conflicted_at` set
- Winning tx credit preserved; losing tx movement reversed
- Audit log entry for conflict event
- `reconciliation_reason = 'DOUBLE_SPEND_CONFLICT'`

---

## Scenario 5: Reorg before credit — expectation removal

**Description:** A deposit tx is mined then removed by reorg before reaching 6 confirmations. KFE removes the pending credit.

**Setup:**
1. Broadcast deposit tx, mine 2 blocks
2. Invalidate the block containing the tx (`invalidateblock <hash>`)
3. Mine alternative chain

**Execution:**
- Observer detects tx removed from active chain
- Checks confirmations dropped to 0
- Tx status updated

**Verification:**
- `network_status` transitions: `MINED_UNCONFIRMED` → `DROPPED`
- `network_not_found_since` populated
- Pending credit movement rolled back (if not yet finalized)
- `reconciliation_reason = 'REORG_BEFORE_FINALITY'`

---

## Scenario 6: Reorg after credit — compensating entry

**Description:** A deposit is fully credited (6 conf) then reorg removes it. KFE creates a compensating debit entry.

**Setup:**
1. Broadcast deposit tx, mine 6+ blocks → credit finalized
2. Invalidate blocks back to before the deposit
3. Mine alternative chain (without the deposit tx)

**Execution:**
- Observer detects finalized tx no longer in chain
- Creates compensating entry

**Verification:**
- Original tx: `network_status = 'REORGED'`, `conflicted_at` set
- Compensating debit movement created (reversal)
- `reconciliation_reason = 'REORG_AFTER_FINALITY'`
- User balance correctly reduced
- Audit trail preserves both original credit and reversal

---

## Scenario 7: Transaction removed from mempool — DROPPED state

**Description:** A tx sits in mempool too long and gets evicted. KFE marks it DROPPED.

**Setup:**
1. Broadcast a low-fee tx → detected in mempool
2. Purge mempool (`clearmempool`) or wait for expiry (default 336h in regtest, can lower)
3. Wait for observer to detect absence

**Execution:**
- Observer polls mempool, tx absent
- Retries `getrawtransaction` — fails
- Marks DROPPED

**Verification:**
- `network_status = 'DROPPED'`
- `network_not_found_since` populated
- `network_not_found_count` incremented each probe cycle
- Pending credit movement cancelled
- User notified of deposit failure

---

## Scenario 8: Broadcast with lost response — txid recovery

**Description:** KFE broadcasts a withdrawal but the RPC response is lost (network timeout mid-flight). KFE recovers the txid via mempool scan.

**Setup:**
1. Create withdrawal tx, sign with FROST
2. Simulate RPC timeout during `sendrawtransaction`
3. Tx actually reached mempool (bitcoind processed it)

**Execution:**
- Broadcast fails with timeout — `prepared_raw_tx_hash` set
- Recovery: query mempool for tx matching prepared hash
- Match confirms tx was broadcast

**Verification:**
- `prepared_raw_tx_hash` matches mempool txid
- `network_status` updated to `MEMPOOL` once recovered
- No duplicate broadcast attempted
- Log entry: "tx broadcast confirmed via mempool recovery"

---

## Scenario 9: Fee exceeds reserve — rejection before broadcast

**Description:** A withdrawal would consume more in fees than the reserve allows. KFE rejects before broadcast.

**Setup:**
1. Configure fee reserve limit (e.g., 10% of amount)
2. Create withdrawal where estimated fee > reserve
3. Attempt broadcast

**Execution:**
- Fee estimation before signing
- KFE fee policy check
- Rejection

**Verification:**
- Tx never broadcast (no `sendrawtransaction` call)
- `accounting_status = 'FEE_EXCEEDS_RESERVE'`
- User receives error: "Fee exceeds reserve limit"
- Balance not deducted (tx was never signed)

---

## Scenario 10: Insufficient UTXOs — graceful failure

**Description:** A withdrawal request exceeds available UTXOs. KFE returns a clear error.

**Setup:**
1. Query vault mesh UTXO set — small balance
2. Create withdrawal request for amount > available UTXOs
3. Attempt to build PSBT

**Execution:**
- Coin selection fails
- Error propagated to KFE

**Verification:**
- No PSBT created
- Error response: "Insufficient funds: available X, requested Y"
- `network_status = 'INSUFFICIENT_UTXOS'`
- User notified with exact shortfall

---

## Scenario 11: Wallet not loaded — error handling

**Description:** Bitcoin Core wallet is encrypted/locked or not loaded. KFE handles gracefully.

**Setup:**
1. Lock wallet: `encryptwallet "passphrase"` then restart without unlock
2. Or: remove wallet file temporarily
3. Attempt deposit monitoring

**Execution:**
- Observer calls `getrawmempool` → RPC error (wallet not loaded)
- Retry with backoff

**Verification:**
- Error logged: "bitcoind wallet not loaded"
- Observer enters degraded mode (no wallet-dependent calls)
- No crash or data corruption
- `last_chain_probe_status = 'WALLET_NOT_LOADED'`
- Retry every 30s until wallet available

---

## Scenario 12: Node temporarily unavailable — retry logic

**Description:** Bitcoin Core node becomes unreachable. KFE retries with exponential backoff.

**Setup:**
1. Stop bitcoind during active monitoring
2. Wait for observer to detect failure
3. Restart bitcoind after 90s

**Execution:**
- Observer RPC calls fail with connection error
- Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s (max)
- Reconnects when node restarts

**Verification:**
- No transactions lost during downtime
- `last_chain_probe_status = 'NODE_UNAVAILABLE'` during outage
- After reconnection: full mempool + block catch-up scan
- Observer returns to normal polling interval
- Audit log records downtime window

---

## Scenario 13: Wrong network — startup rejection

**Description:** KFE is configured for testnet but bitcoind is on mainnet (or vice versa). Startup fails fast.

**Setup:**
1. Configure `bitcoin.network=testnet` in KFE config
2. Point RPC to a mainnet node
3. Start KFE

**Execution:**
- Startup health check calls `getblockchaininfo`
- Compares `chain` field with configured network
- Mismatch detected

**Verification:**
- Application fails to start with clear error
- Error message: "Bitcoin network mismatch: expected testnet, got main"
- No data written, no partial state
- Health endpoint returns DOWN before any business logic loads

---

## Scenario 14: Two outputs to same wallet — correct credit aggregation

**Description:** A single tx sends BTC to two addresses that belong to the same wallet. KFE credits both as a single deposit.

**Setup:**
1. Create tx with two outputs to same vault mesh deposit address
2. Broadcast and mine 6 blocks

**Execution:**
- Observer decodes tx outputs
- Both outputs match vault mesh deposit script
- Aggregates total

**Verification:**
- Single `kfe_transaction` row with aggregated amount
- Single credit movement for total amount
- `transaction_id` links both outputs
- Balance reflects sum of both outputs

---

## Scenario 15: Same txid observed by two monitors — idempotent processing

**Description:** Two observer instances (or two poll cycles) process the same txid. KFE handles idempotently.

**Setup:**
1. Broadcast deposit tx
2. Configure two concurrent observer threads/pods
3. Both detect tx in same mempool poll cycle

**Execution:**
- Observer A processes tx → creates transaction + movement
- Observer B processes same tx → idempotency guard triggers

**Verification:**
- Only one `kfe_transaction` row (unique txid constraint)
- Only one credit movement
- `kfe_idempotency_claim` row with `claim_key = 'deposit:<txid>'`
- Second observer logs: "txid already processed, skipping"
- No duplicate credit, no balance inflating

---

## Test Infrastructure Notes

- All scenarios runnable against a single regtest node
- Use `bitcoin-cli -regtest generate N` for block mining
- Use `bitcoin-cli -regtest invalidateblock <hash>` for reorg simulation
- Use `bitcoin-cli -regtest clearmempool` for mempool purge
- Observer polling interval configurable per test (reduce to 1s for speed)
- Transaction ID: use test fixture wallets seeded from regtest coinbase
