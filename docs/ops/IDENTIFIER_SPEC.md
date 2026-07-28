# Identifier Specification

Every identifier used across Kerosene modules: format, uniqueness scope, mutability, exposure rules.

---

## 1. Transaction Identifiers

### `transactionId`

| Property | Value |
|----------|-------|
| **Format** | UUID v4 (36-char string) |
| **Max length** | 36 chars |
| **Uniqueness scope** | Global (across all users, wallets, rails) |
| **Mutable** | No |
| **Exposure** | KFE API only — NEVER sent to Bitcoin network |
| **Java type** | `java.util.UUID` |
| **Source** | Generated server-side on `INTENT` creation |

Rule: External systems never see `transactionId`. It is an internal KFE correlation key.

### `providerReference`

| Property | Value |
|----------|-------|
| **Format** | Provider-specific, opaque string |
| **Max length** | 256 chars |
| **Uniqueness scope** | Per provider |
| **Mutable** | No (set once by provider adapter) |
| **Exposure** | API response — informational only |
| **Java type** | `String` |
| **Source** | LND payment index / Bitcoin Core tx index / provider-defined |

### `blockchainTxid`

| Property | Value |
|----------|-------|
| **Format** | 64-char lowercase hex |
| **Max length** | 64 chars |
| **Uniqueness scope** | Global (Bitcoin network) |
| **Mutable** | No (set when broadcast confirmed) |
| **Exposure** | API response — links to block explorer |
| **Java type** | `String` |
| **Source** | Bitcoin Core `gettransaction` / mempool entry |

Never derive `blockchainTxid` from `transactionId` or vice versa. They occupy separate namespaces.

### `paymentHash`

| Property | Value |
|----------|-------|
| **Format** | 64-char lowercase hex (SHA-256) |
| **Max length** | 64 chars |
| **Uniqueness scope** | Global (Lightning Network) |
| **Mutable** | No (set when LN invoice decoded) |
| **Exposure** | API response — links to LN explorer |
| **Java type** | `String` |
| **Source** | LND `lookupInvoice` / decoded bolt11 |

---

## 2. Payment Request Identifiers

### `idempotencyKey`

| Property | Value |
|----------|-------|
| **Format** | Client-provided, alphanumeric `[A-Za-z0-9_-]` |
| **Min length** | 16 chars |
| **Max length** | 128 chars |
| **Uniqueness scope** | Per user, per mutation endpoint |
| **Mutable** | No (set by client on first attempt) |
| **Exposure** | `Idempotency-Key` HTTP header |
| **Java type** | `String` |
| **Source** | Client generates; server validates and stores |

Server validates:
- Length: 16–128 chars
- Charset: `[A-Za-z0-9_-]+`
- Replay: same key + same body → return cached response
- Conflict: same key + different body → `IDEMPOTENCY_CONFLICT` error

### `paymentRequestId`

| Property | Value |
|----------|-------|
| **Format** | UUID v4 |
| **Max length** | 36 chars |
| **Uniqueness scope** | Global |
| **Mutable** | No |
| **Exposure** | KFE API only |
| **Java type** | `java.util.UUID` |
| **Source** | Generated server-side on payment request creation |

### `publicId`

| Property | Value |
|----------|-------|
| **Format** | 18-byte random, base64url-encoded (24 chars) |
| **Max length** | 24 chars |
| **Uniqueness scope** | Global |
| **Mutable** | No |
| **Exposure** | Public URLs (`/kfe/public/payment/{publicId}`) |
| **Java type** | `String` |
| **Source** | Generated server-side; `SecureRandom` 18 bytes → base64url |

Never encode internal state in `publicId`. Use pure random. Avoid sequential or time-based IDs — they leak creation order and rate.

### `quoteId`

| Property | Value |
|----------|-------|
| **Format** | UUID v4 |
| **Max length** | 36 chars |
| **Uniqueness scope** | Global |
| **Mutable** | No |
| **Exposure** | KFE API — fee quote endpoint |
| **Java type** | `java.util.UUID` |
| **Source** | Generated when fee quote is requested |

Fee quotes are ephemeral. A `quoteId` is valid for 30 seconds. After expiry, the client must request a fresh quote.

---

## 3. Wallet / Account Identifiers

### Internal Wallet IDs

| Field | Format | Scope | Mutable |
|-------|--------|-------|---------|
| `walletId` | UUID v4 | Global | No |
| `sourceWalletId` | UUID v4 | Global | No |
| `destinationWalletId` | UUID v4 | Global | No |

### Blockchain-Level Identifiers

| Field | Format | Scope | Notes |
|-------|--------|-------|-------|
| `xpub` | Base58 (BIP-32 extended public key) | Per-wallet | Optional. Present for watch-only wallets. |
| `mpcPublicKey` | Hex | Per-sovereignty-set | Optional. Present for MPC-managed wallets. |
| `address` | Base58 / Bech32(m) | Global (Bitcoin) | Derived per derivation index. Rotated. |

---

## 4. Cross-Cutting Rules

### Separation

- Internal IDs (UUIDs) MUST NOT overlap with Bitcoin/Lightning identifiers in value or namespace.
- A `transactionId` UUID `a1b2c3d4-...` must never equal any `blockchainTxid` or `paymentHash`.
- When logging, always include both internal ID AND external ID for traceability.

### Serialization

| Context | UUID Format | Example |
|---------|-------------|---------|
| Java internal | `java.util.UUID` | `UUID.fromString("a1b2c3d4-...")` |
| JSON API | Lowercase string | `"a1b2c3d4-e5f6-7890-abcd-ef1234567890"` |
| Database (Postgres) | `UUID` column type | Native UUID |
| Database (H2 test) | `UUID` column type | Native UUID |

### Logging

Always log identifiers in structured form:

```java
log.info("Tx settled", kv("transactionId", txId), kv("blockchainTxid", txid), kv("paymentHash", ph));
```

Never concatenate identifiers into log messages — use structured key-value pairs.

---

## 5. Quick Reference

| Identifier | Format | Length | Example |
|------------|--------|--------|---------|
| `transactionId` | UUID v4 | 36 | `a1b2c3d4-e5f6-7890-abcd-ef1234567890` |
| `providerReference` | opaque string | ≤256 | `lnd-payment-12345` |
| `blockchainTxid` | 64-char hex | 64 | `abcdef0123...` (64 chars) |
| `paymentHash` | 64-char hex | 64 | `0123abcdef...` (64 chars) |
| `idempotencyKey` | alphanumeric | 16–128 | `clt-20260727-abc123` |
| `paymentRequestId` | UUID v4 | 36 | `b2c3d4e5-f6a7-8901-bcde-f12345678901` |
| `publicId` | base64url (18B→24) | 24 | `YWJjZGVmZ2hpamtsbW5vcA` |
| `quoteId` | UUID v4 | 36 | `c3d4e5f6-a7b8-9012-cdef-123456789012` |
