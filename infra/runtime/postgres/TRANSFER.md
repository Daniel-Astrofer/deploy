# Postgres runtime transfer

Source paths:

```text
backend/kerosene/deploy/postgres/*
backend/kerosene-infrastructure/prod/k8s/postgres-patroni.yaml
```

Target path:

```text
infra/runtime/postgres/*
```

Transfer schema/init scripts only. Do not transfer database data directories or secrets.
