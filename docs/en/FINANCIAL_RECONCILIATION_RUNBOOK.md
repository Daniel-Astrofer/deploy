# Financial reconciliation alert

This runbook covers the Prometheus alerts defined in
`infra/runtime/observability/prometheus/financial-alerts.yml`.

1. Identify the alert type, affected environment and first occurrence.
2. Preserve application, KFE, database and provider logs for the alert window.
3. Inspect the affected operation through the supported administrative API or
   CLI. Do not edit financial tables directly.
4. Confirm chain/provider state independently before retrying an operation.
5. Use an idempotent retry or approved reconciliation action. Never create a
   replacement payment to hide an incomplete operation.
6. Record the operator, evidence, action and final state in the incident record.

Escalate immediately when confirmation count regresses, balances disagree, an
operation cannot be correlated by its identifier, or audit evidence is missing.
Do not silence a critical reconciliation alert without an incident reference.
