# Product State Mapping

Maps internal `KfeTransactionStatus` enum values to the external product status vocabulary.

---

## 1. External Product States

These 7 states are the stable contract exposed to clients:

| State | Meaning |
|-------|---------|
| `PENDING` | Request received, not yet processing |
| `PROCESSING` | Active work in progress (validation, quorum, broadcast) |
| `CONFIRMING` | Awaiting on-chain confirmations or LN settlement |
| `COMPLETED` | Finalized successfully |
| `FAILED` | Terminal failure, no retry |
| `NEEDS_REVIEW` | Ambiguous outcome, manual intervention required |
| `REVERSED` | Successfully completed then reversed (refund/chargeback) |

---

## 2. Internal-to-External Mapping

```
INTENT                    → PENDING
VALIDATING                → PROCESSING
QUORUM_SYNC               → PROCESSING
LOCKED                    → PROCESSING
EXECUTING                 → PROCESSING
BROADCAST                 → CONFIRMING
CONFIRMING                → CONFIRMING
SETTLED                   → COMPLETED
FINALIZED                 → COMPLETED
FAILED                    → FAILED
CANCELLED                 → FAILED
CONFLICTED                → FAILED (or NEEDS_REVIEW — see §3)
CONFLICTED_RECONCILING    → NEEDS_REVIEW
CONFLICTED_REFUNDED       → COMPLETED (refunded variant)
ABANDONED                 → FAILED
DROPPED                   → FAILED
REQUIRES_RECONCILIATION   → NEEDS_REVIEW
REORG_RECONCILIATION      → NEEDS_REVIEW
REVERSED                  → REVERSED
```

---

## 3. Ambiguity Rules

### CONFLICTED

Maps to `FAILED` by default. Override to `NEEDS_REVIEW` when:
- At least 1 confirmation was seen before the conflict
- Funds may have moved on an alternative chain tip

### CONFLICTED_RECONCILING

Always maps to `NEEDS_REVIEW`. This state means a human is actively investigating.

### CONFLICTED_REFUNDED

Maps to `COMPLETED` with a refunded variant. The `businessStatus` field carries `CONFLICTED_REFUNDED` for client distinction. The product status `COMPLETED` is correct because the flow reached a terminal, successful financial outcome (refund executed).

---

## 4. States Not Yet in Enum

| Internal State | Status | Mapping |
|----------------|--------|---------|
| `FINALIZED` | Reserved for future use | `COMPLETED` |
| `REVERSED` | Reserved for future chargeback/reversal flows | `REVERSED` |

These are documented for forward compatibility. The `KfeProductStatusMapper` handles them defensively: unknown values map to `PENDING` (fail-safe).

---

## 5. Client Contract

- Product status is the **only** status field clients should branch on for UI decisions.
- Internal status is informational/debug only.
- The `displayStatus` field on `KfeTransactionResponse` is a coarse 3-value badge (`PENDING`, `CONFIRMED`, `FAILED`) and is NOT the product status.
- Product status offers 7 distinct states, suitable for detailed UX flows.

---

## 6. Implementation

Java mapping lives at:
`backend/kerosene/kfe-service/src/main/java/com/kerosene/kfe/dto/KfeProductStatusMapper.java`

Usage in `KfeResponseMapper`:

```java
import com.kerosene.kfe.dto.KfeProductStatusMapper;

// In toTransactionResponse():
productStatus = KfeProductStatusMapper.toProductStatus(tx.getStatus());
```
