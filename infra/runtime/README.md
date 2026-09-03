<!--
Kerosene documentation metadata
status: review-required
audience: internal
owner: deploy
source_of_truth: deploy
last_reviewed: 2026-09-03
-->

# Production runtime assets

Only non-secret assets consumed by current production packaging or operations
belong here:

| Path | Contents |
|---|---|
| `tor/` | Default Tor configuration and verified container entrypoint |
| `web/` | Production Nginx configuration used by the web image |
| `observability/prometheus/` | Alert rules referenced by runbooks/private ops |

Application configuration belongs to its service repository. Bitcoin, LND and
PostgreSQL lifecycle scripts belong to the private environment that owns those
dependencies. Certificates, keys, Onion identities, database data and generated
metrics are never stored in this tree.
