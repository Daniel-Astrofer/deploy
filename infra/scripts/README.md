# Kerosene infra internal scripts

Esta pasta contém helpers internos chamados pelos entrypoints públicos de
`infra/`.

## Interface pública

Não chame scripts desta pasta diretamente no uso normal. Use:

```bash
bash infra/start.sh
bash infra/stop.sh
bash infra/recreate.sh
bash infra/status.sh
bash infra/logs.sh
bash infra/test.sh
```

## Conteúdo

```text
quorum.sh          Dispatcher interno da interface pública.
images.sh          Helper interno para build/import de imagens locais.
host-services.sh   Preflight systemd para containerd, Docker e kubelet.
common.sh          Funções compartilhadas de Docker/Compose/env.
backend-common.sh  Helper interno para scripts que precisam do backend local.
flutter-common.sh  Helper interno para build Flutter controlado.
local/             Compose legado e rotinas específicas de banco/log/reparo.
```

Scripts MCP ficam em `infra/mcp/`, não em Kubernetes.
