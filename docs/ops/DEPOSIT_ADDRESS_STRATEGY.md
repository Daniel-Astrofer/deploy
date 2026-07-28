# User Deposit Address Strategy (Omnibus vs Rotating)

Decision framework for deposit address management for USERS bucket.

## Current State

- Single omnibus Taproot address (`tb1p...`) per vault mesh
- Derived from FROST group verifying key via `deposit_info()` in `frost_tr_bitcoin.rs`
- All users deposit to the same address; UTXOs identified via on-chain labeling

## Options

### Omnibus (Current)
**Single `tb1p` address for all USERS deposits.**

| Aspect | Assessment |
|---|---|
| Privacy | Poor — all user UTXOs on one address; chain analysis trivial |
| Complexity | Low — single address to monitor, single key to manage |
| Quantum risk | HIGH — single address accumulates UTXOs for years; sweep requires single coordination event |
| Operational | Simple — one address; deposit attribution via KFE ledger metadata |
| Migration | Hard — all UTXOs on one key; sweep requires all vault members online |

### Rotating Per-User
**Deterministic per-user addresses derived from mesh root `tb1p`.**

| Aspect | Assessment |
|---|---|
| Privacy | Better — per-user addresses at chain level; still linkable via shared inputs |
| Complexity | Higher — N addresses to monitor; per-user derivation path management |
| Quantum risk | LOWER — smaller per-user UTXOs; easier to sweep incrementally |
| Operational | More complex — address indexing; scan per-user addresses for deposits |
| Migration | Easier — per-user sweeps can be staggered; lower coordination cost |

### Rotating Per-Epoch
**Per-user per-epoch addresses: `m/0/<user_id>/<epoch>` BIP-32-like derivation.**

| Aspect | Assessment |
|---|---|
| Privacy | Best — fresh address per user per epoch; forward-looking chain privacy |
| Complexity | Highest — many addresses; UTXO consolidation overhead |
| Quantum risk | LOWEST — smallest UTXOs; easiest to sweep; fresh keys mitigate harvest-now-decrypt-later |
| Operational | Most complex — need robust scanning, indexing, and consolidation strategy |
| Migration | Easiest — granular UTXOs; staggered sweeps per user per epoch |

## Quantum Angle (Critical for PQ Strategy)

The Taproot omnibus key is secp256k1 — NOT post-quantum. An address that stays fixed for years concentrates quantum risk:

1. **Harvest-now-decrypt-later**: All on-chain data captured today is decryptable by future quantum adversaries
2. **Sweep complexity**: Omnibus = all UTXOs must be swept simultaneously when quantum migration triggers
3. **Coordination**: Rotating addresses allow phased migration — each user's UTXOs swept independently

**Rotating is preferred from a PQ standpoint** — smaller UTXOs, granular sweep, fresh derivation each epoch.

## Recommendation

### Phase 1 (Go-Live): Omnibus
- Simple, well-understood, matches current implementation
- Establish deposit monitoring infrastructure before adding complexity
- Track UTXO attribution via KFE ledger metadata

### Phase 2 (Post-Go-Live): Per-User Rotating
- Implement BIP-32-like deterministic derivation from mesh root
- `deposit_address_for(user_id: &str) -> DepositInfo` abstraction
- Add per-user address monitoring and indexing

### Phase 3 (PQ Migration Window): Per-Epoch Rotating
- Fresh derivation per epoch for quantum migration readiness
- Granular UTXO sweep capability

## Abstraction

Regardless of the chosen strategy, the vault mesh exposes a unified interface:

```rust
/// Returns the deposit address for a given user, regardless of omnibus/rotating strategy.
/// Under omnibus: returns the shared mesh address (user_id is ignored).
/// Under rotating: derives a deterministic per-user address from the mesh root key.
fn deposit_address_for(&self, user_id: &str) -> Result<DepositInfo, DomainError>;
```

Current implementation in `FrostTrBitcoinOrchestrator::deposit_info()` always returns the omnibus address. A `deposit_address_for(user_id)` method is planned for Phase 2.

## Related

- `DepositInfo` struct: `backend/kerosene-vault/src/adapters/frost_tr_bitcoin.rs:347`
- `deposit_info()`: `backend/kerosene-vault/src/adapters/frost_tr_bitcoin.rs:405`
- PQ strategy: `docs/VAULT_IMPLEMENTATION_PLAN.md` — Tier 0 (Quantum Threat)
