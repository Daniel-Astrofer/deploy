<!--
Kerosene documentation metadata
status: active
audience: internal
owner: deploy
source_of_truth: deploy
last_reviewed: 2026-09-03
-->

# Container packaging

`images.yaml` is the packaging inventory. Each service recipe uses the owning
polyrepo checkout as its build context; Deploy does not copy application source.

```bash
bash infra/docker/build-image.sh <image-key>
```

The command is a packaging helper, not a production rollout. Production accepts
only externally published image references pinned by digest. Docker contexts are
protected by repository-level `.dockerignore` files so build caches, Git data,
local runtime state and key material are not sent to the daemon.

Auxiliary infrastructure such as Tor may use Deploy-owned runtime assets. Secret
values, ceremony output and private Onion identities must never be added here.
