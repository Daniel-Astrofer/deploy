# Adapter API Contract

Canonical API envelope all Kerosene adapters must follow. Applies to KFE REST, vault mesh HTTP, and any future adapter surface.

---

## 1. Success Envelope

```json
{
  "success": true,
  "data": {},
  "requestId": "uuid",
  "timestamp": "2026-07-27T20:00:00Z"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `success` | boolean | yes | Always `true` in success paths |
| `data` | object | yes | Payload. Empty object `{}` for 204-style responses |
| `requestId` | string (UUID) | yes | Echoed from `X-Request-Id` header or generated server-side |
| `timestamp` | string (ISO-8601) | yes | UTC, always trailing `Z` |

---

## 2. Error Envelope

```json
{
  "success": false,
  "error": {
    "code": "PAYMENT_AMOUNT_EXCEEDS_LIMIT",
    "message": "Payment amount exceeds configured policy.",
    "details": {}
  },
  "requestId": "uuid",
  "timestamp": "2026-07-27T20:00:00Z"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `success` | boolean | yes | Always `false` in error paths |
| `error.code` | string | yes | Machine-readable from catalog below |
| `error.message` | string | yes | Human-readable, no stack traces or internal paths |
| `error.details` | object | no | Optional structured context (field violations, provider metadata) |
| `requestId` | string (UUID) | yes | Same as success envelope |
| `timestamp` | string (ISO-8601) | yes | Same as success envelope |

---

## 3. Error Code Catalog

### 3.1 Payment Validation

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `PAYMENT_AMOUNT_EXCEEDS_LIMIT` | 422 | Amount exceeds configured policy limit |
| `PAYMENT_INVOICE_EXPIRED` | 422 | Lightning invoice has expired |
| `PAYMENT_INVOICE_ALREADY_SETTLED` | 409 | Invoice was already paid |
| `PAYMENT_NETWORK_MISMATCH` | 422 | Network (mainnet/testnet/regtest) mismatch |
| `PAYMENT_FEE_EXCEEDS_LIMIT` | 422 | Estimated fee exceeds user-configured cap |
| `PAYMENT_ZERO_AMOUNT_INVOICE` | 422 | Zero-amount invoice with no amount override |

### 3.2 Idempotency

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `IDEMPOTENCY_KEY_MISSING` | 400 | Required `Idempotency-Key` header absent |
| `IDEMPOTENCY_KEY_INVALID` | 400 | Key format invalid (length, charset) |
| `IDEMPOTENCY_CONFLICT` | 409 | Different body for the same key |
| `IDEMPOTENCY_IN_PROGRESS` | 409 | Previous request with same key still processing |

### 3.3 Rate Limiting

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `RATE_LIMIT_EXCEEDED` | 429 | Client rate limit hit. Retry after `Retry-After` header |

### 3.4 Authentication / Authorization

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `AUTH_REQUIRED` | 401 | No credentials provided |
| `AUTH_INVALID_TOKEN` | 401 | Token expired, revoked, or malformed |
| `AUTH_INSUFFICIENT_SCOPES` | 403 | Valid token but missing required scope |

### 3.5 Network / Provider

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `NETWORK_MISMATCH` | 422 | Request targets wrong network vs. server config |
| `BITCOIN_CORE_UNAVAILABLE` | 502 | Bitcoin Core RPC unreachable |
| `LND_UNAVAILABLE` | 502 | LND gRPC unreachable |
| `TRANSACTION_CONFLICTED` | 409 | On-chain tx double-spent or conflicting mempool entry |
| `BROADCAST_DISABLED` | 503 | Broadcast intentionally disabled (maintenance mode) |

### 3.6 Internal

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `INTERNAL_ERROR` | 500 | Unclassified internal failure. Details never expose stack traces or secrets |

---

## 4. Naming Conventions

- **JSON keys**: camelCase (e.g., `requestId`, `blockchainTxid`).
- **Enums in API**: SCREAMING_SNAKE_CASE (e.g., `PAYMENT_AMOUNT_EXCEEDS_LIMIT`).
- **Field namespace**: No prefixing. Names are globally unique within each DTO.
- **Health endpoint**: `/health` (NOT `/healthz`).
- **Versioning**: No `/v1/` path prefix. Adapters version via content negotiation or header (`Accept-Version`) if needed in the future.

---

## 5. Timestamps

- All timestamps: ISO-8601, UTC, trailing `Z`.
- Java type: `java.time.Instant` at DTO layer.
- DB layer: `LocalDateTime` (UTC wall-clock) converted via `Utc.toInstant()`.
- See `docs/ops/TIMESTAMP_SPEC.md` for full rules.

---

## 6. Monetary Values

- All amounts: **satoshis (integer)**.
- NEVER expose BTC as float/decimal in API responses.
- Display conversions (USD, EUR, BRL) are `BigDecimal` fields, clearly named `display*`.
- `network` field required in every financial entity.

---

## 7. Pagination

Two modes supported:

### 7.1 Offset/Limit (default for KFE)

```json
{
  "offset": 0,
  "limit": 50,
  "total": 147,
  "items": []
}
```

Parameters:
- `offset` (query, default 0)
- `limit` (query, default 50, max 200)

### 7.2 Cursor-based (for high-volume streams)

```json
{
  "cursor": "opaque_string",
  "limit": 50,
  "hasMore": true,
  "items": []
}
```

Parameters:
- `cursor` (query, optional) — opaque cursor from previous response
- `limit` (query, default 50, max 200)

Use offset/limit unless the dataset exceeds 10k rows or requires stable iteration under concurrent writes.

---

## 8. Identifier Separation

Internal KFE IDs (UUID) must never equal or share namespace with Bitcoin or Lightning identifiers.

| Internal ID | External ID | Rule |
|-------------|-------------|------|
| `transactionId` (UUID) | `blockchainTxid` (64-char hex), `paymentHash` (64-char hex) | Never the same string |
| `walletId` (UUID) | `xpub` (base58), `mpcPublicKey` (hex) | Never interchangeable |

See `docs/ops/IDENTIFIER_SPEC.md` for complete catalog.

---

## 9. Headers

| Header | Required | Notes |
|--------|----------|-------|
| `Authorization` | per-endpoint | Bearer token |
| `X-Request-Id` | recommended | UUID, echoed in response envelope |
| `Idempotency-Key` | per-mutation | Required on POST/PATCH that mutates money |
| `Content-Type` | yes | `application/json` |
| `Accept` | yes | `application/json` |
