# Vault runtime transfer

Source paths:

```text
backend/kerosene-infrastructure/vault/*
backend/kerosene-infrastructure/prod/k8s/vault-raft.yaml
```

Target path:

```text
infra/runtime/vault/*
```

Do not transfer real unseal material, private keys, cert private keys or raft data without an explicit security plan.
