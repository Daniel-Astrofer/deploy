# Tor runtime transfer

Source paths:

```text
backend/kerosene/deploy/tor/*
backend/kerosene-infrastructure/prod/tor/*
```

Target path:

```text
infra/runtime/tor/*
```

Do not transfer onion private keys, hostname material tied to a real hidden service, or auth secrets without a rotation plan.
