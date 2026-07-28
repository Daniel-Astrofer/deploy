# Proof of Reserves — Solvency Snapshot Model

Documents KFE's proof-of-reserves methodology. This is NOT a balance check — it verifies that on-chain assets controlled by the vault mesh cover ledger liabilities.

## Solvency Invariant

```
eligibleAssets >= liabilities + safetyBuffer
```

Where:
- `eligibleAssets`: confirmed UTXOs controlled by vault mesh + conservative Lightning channel balances
- `liabilities`: sum of user AVAILABLE + LOCKED + PENDING credits + fees owed + in-flight withdrawals
- `safetyBuffer`: safety buffer in basis points (e.g., 5000 = 50% extra assets)

If the invariant is violated, settlement is blocked (fail-closed).

## Asset Model

### Confirmed and Controlled UTXOs
- Source: `scantxoutset` / descriptor from vault mesh (USERS bucket Taproot key)
- Filter: confirmed only (min confirmations per Bitcoin network policy)
- Exclusion: unconfirmed, mempool-only, or UTXOs not controlled by USERS descriptor

### Conservative Lightning Channel Balance
- Only confirmed channel state (not pending HTLCs)
- Local balance minus reserve (anchor outputs, dust limits)
- Channel must be `active` per LND RPC

### Other Eligible Assets
- On-chain deposits ≥1 confirmation but < min credit confirmations (PENDING by ledger)
- Funds in-flight from channel closes (sweep transactions confirmed)

## Liability Model

### User AVAILABLE Balance
- Sum of `available_sats` across all non-WATCH_ONLY wallets
- Must match ledger at snapshot block

### User LOCKED Balance
- Sum of `locked_sats` + `auto_hold_sats` across all non-WATCH_ONLY wallets
- Locked funds are owed to users but not currently spendable

### User PENDING Credits
- Sum of `pending_sats` (deposits detected but not yet credited)
- In-flight transactions that will become available after confirmations

### Fees Owed
- SYSTEM_PROFIT wallet balance representing collected fees
- This is a liability: profit must be backed by real assets until segregated

### In-Flight Withdrawals
- Transactions in EXECUTING/VALIDATING status
- Amounts deducted from available but not yet broadcast/confirmed

## Configuration

```properties
kfe.reserves.proof-of-reserves.enabled=true
kfe.reserves.proof-of-reserves.safety-buffer-bps=5000  # 50% buffer
kfe.reserves.proof-of-reserves.minimum-coverage-ratio=1.0  # assets >= liabilities
```

## Snapshot Metadata

Each PoR snapshot binds:
- Block hash and height at snapshot time
- Constitution hash of the vault mesh
- Timestamp (UTC)
- Asset/liability breakdown

## Integration Points

- `KfeProofOfReservesService`: computes the solvency snapshot
- `BinarySettlementGate.evaluateReservaMat()`: enforces the invariant at settlement
- `KfeReserveOverviewService`: reports assets and liabilities separately (no cross-summing)

## Failure Modes

| Condition | Gate Result | Remediation |
|-----------|-------------|-------------|
| `eligibleAssets < liabilities` | FAIL (INSOLVENT) | Stop all outbound. Investigate asset shortage. |
| `eligibleAssets < liabilities + safetyBuffer` | FAIL (BELOW_BUFFER) | Block outbound above cap. Top up assets. |
| Vault mesh unreachable | FAIL (ASSETS_UNAVAILABLE) | Block settlement until mesh online with quorum. |
| Ledger query error | FAIL (LIABILITIES_UNAVAILABLE) | Block settlement. Investigate DB integrity. |
