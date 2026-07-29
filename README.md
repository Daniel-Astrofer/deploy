# Kerosene Deploy

Private deployment templates, orchestration and operational runbooks for
Kerosene.

This repository contains generic Docker/Kubernetes/Tor/observability
configuration extracted from the monorepo. It must not contain runtime secrets,
shares, seed phrases, macaroons, private Onion keys, TPM keys, user data or real
logs. Private visibility is an additional control, not a secret manager.
