# Timestamp Specification

Canonical rules for all timestamps in Kerosene. Applies to KFE backend, vault mesh, and any adapter.

---

## 1. Core Rule

**All timestamps must be UTC.** No exceptions.

---

## 2. API Layer (Java DTOs)

| Type | Format | Java Type |
|------|--------|-----------|
| API response | ISO-8601 with trailing `Z` | `java.time.Instant` |
| API request (input) | ISO-8601, accepts `Z` or offset | `java.time.Instant` |

Example in JSON:
```json
{
  "createdAt": "2026-07-27T20:00:00Z",
  "updatedAt": "2026-07-27T20:05:30Z"
}
```

Never use `LocalDateTime` in DTO records. `LocalDateTime` has no timezone — it is ambiguous and dangerous in financial APIs.

---

## 3. Database Layer

| Layer | Type | Convention |
|-------|------|------------|
| JPA entity fields | `java.time.LocalDateTime` | Stored as UTC wall-clock |
| Conversion to DTO | `Utc.toInstant(LocalDateTime)` | `com.kerosene.kfe.time.Utc` utility |
| Conversion from DTO | `Utc.toLocalDateTime(Instant)` | Inverse mapping |

The database uses `LocalDateTime` because JPA mappings historically use `TIMESTAMP WITHOUT TIME ZONE`. All values are written and read as UTC. The `Utc` utility enforces this contract.

---

## 4. Required Timestamps Per Entity

### Transaction (`KfeTransactionEntity`)

| Field | Java Type | When Set |
|-------|-----------|----------|
| `createdAt` | `LocalDateTime` | Row insert |
| `updatedAt` | `LocalDateTime` | Every status change |
| `detectedAt` | `LocalDateTime` | When provider first sees the tx |
| `broadcastAt` | `LocalDateTime` | When tx is submitted to network |
| `confirmedAt` | `LocalDateTime` | First on-chain confirmation |
| `settledAt` | `LocalDateTime` | LN settled / on-chain finalized |
| `finalizedAt` | `LocalDateTime` | Terminal state reached (success or failure) |
| `failedAt` | `LocalDateTime` | Failure detected |
| `conflictedAt` | `LocalDateTime` | Conflict detected |

### Wallet (`KfeWalletEntity`)

| Field | Java Type | When Set |
|-------|-----------|----------|
| `createdAt` | `LocalDateTime` | Wallet creation |
| `updatedAt` | `LocalDateTime` | Label change, archive, etc. |

### Address (`KfeWalletAddressEntity`)

| Field | Java Type | When Set |
|-------|-----------|----------|
| `createdAt` | `LocalDateTime` | Address derivation |
| `retiredAt` | `LocalDateTime` | Address marked retired |

---

## 5. Clock Injection for Tests

All time sources must be injectable via `java.time.Clock`:

```java
// Production
@Component
public class KfeClock {
    private final Clock clock;

    public KfeClock(@Value("${kerosene.clock.fixed:}") String fixed) {
        this.clock = (fixed != null && !fixed.isBlank())
            ? Clock.fixed(Instant.parse(fixed), ZoneOffset.UTC)
            : Clock.systemUTC();
    }

    public Clock clock() { return clock; }
    public Instant now() { return Instant.now(clock); }
}
```

Test example:

```java
@Test
void settleTimestampIsUtc() {
    var clock = Clock.fixed(Instant.parse("2026-07-27T20:00:00Z"), ZoneOffset.UTC);
    var svc = new KfeSettlementService(repo, clock);
    var result = svc.settle(txId);
    assertThat(result.getSettledAt()).isEqualTo("2026-07-27T20:00:00Z");
}
```

Never call `Instant.now()`, `LocalDateTime.now()`, `System.currentTimeMillis()`, or `new Date()` directly in business logic. Always use injected `Clock`.

---

## 6. Clock Tolerance

### KFE ↔ LND

LND reports timestamps from its own system clock. KFE stores both KFE-side timestamps AND LND-reported timestamps.

| Source | Field | Tolerance |
|--------|-------|-----------|
| KFE server | `createdAt`, `updatedAt`, `settledAt` | NTP-synced, ±100ms drift acceptable |
| LND | `detectedAt` (LN payments) | May differ from KFE clock by up to 30 seconds |

Reconciliation must account for this tolerance. Never reject a record solely because `lndDetectedAt` is > KFE `createdAt` by a few seconds.

### Vault Mesh

Vault mesh nodes run NTP. DKG ceremony requires all nodes to agree on epoch boundaries with ±2 second tolerance.

---

## 7. Anti-Patterns

| Forbidden | Reason |
|-----------|--------|
| `LocalDateTime.now()` in business logic | No timezone — ambiguous |
| `new Date()` | Legacy, mutable, no zone |
| `System.currentTimeMillis()` | Untestable, no Clock injection |
| `LocalDateTime` in DTO records | Ambiguous on wire |
| Mixing UTC and local time in the same entity | Reconciliation nightmare |
| Timestamps without trailing `Z` in API | Clients assume UTC; missing `Z` breaks this |

---

## 8. Financial Records Rule

**Never use `LocalDateTime` for financial records exposed to external systems.**

Internal DB may use `LocalDateTime` (with UTC convention) because Spring/JPA manages it. But:
- DTOs → always `Instant`
- Audit logs → always `Instant`
- Mesh protocol messages → always `Instant`
- Any data leaving the JVM → always `Instant`
