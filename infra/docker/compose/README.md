# Kerosene Docker Compose

Este diretório é o destino canônico dos arquivos Compose da Kerosene.

## Estado de transição

Os Compose files canônicos já foram transferidos para este diretório. Os paths
internos foram reescritos para a nova localização e os builds principais passam
pelos Dockerfiles de `infra/docker/images`.

Arquivos canônicos:

```text
infra/docker/compose/local.compose.yaml
infra/docker/compose/local.limits.compose.yaml
infra/docker/compose/local.kfe.compose.yaml
```

A topologia `hardened` fica fora do repositório público (ops privado).

Os caminhos legados continuam presentes para compatibilidade com scripts antigos.

## Wrappers canônicos

Use os wrappers em `infra/docker/scripts` para operar Compose a partir da nova camada:

```bash
bash infra/docker/scripts/compose-local.sh up -d
bash infra/docker/scripts/compose-local.sh ps
bash infra/docker/scripts/compose-local-kfe.sh up -d
```

`compose-local-kfe.sh` includes the split KFE overlay. In that mode each shard
starts an API gateway and its Tor hidden service publishes the gateway: Core
routes such as `/auth/**` go to `server-*`, while `/kfe/**`,
`/api/public/kfe/**` and `/api/admin/kfe/**` go to `kfe-service-*`.

## Próxima etapa

Migrar gradualmente scripts raiz para os wrappers canônicos e remover os Compose
legados apenas depois de uma janela de validação.
