# Agent rules

- Never commit secrets, shares, macaroons, private keys or production data.
- Reference secret-manager objects from templates.
- Keep examples synthetic and clearly marked.
- Validate Compose/Kubernetes configuration before release.
- Deployment automation must not activate Vault signers automatically.
- Document rollback and recovery for every production-impacting change.
