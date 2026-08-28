# HashiCorp Vault decision

HashiCorp Vault is not part of the active Kerosene runtime. No active manifest
or service uses port `8200`, `VAULT_ADDR` or Spring Cloud Vault.

Vault Mesh (`kerosene-vault`) owns custody, FROST shares and signing. It must
never be used as a generic secret store. If a generic secret manager is adopted
later, Deploy may configure it only for operational credentials such as DB/API
access, in a separate trust domain and through an explicit production decision.

Pending: select the production secret delivery mechanism and document rotation,
audit, recovery and CI identity. This decision does not belong to the current
source migration and does not authorize an implementation.
