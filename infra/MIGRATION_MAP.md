# Kerosene Infra Migration Map

Este arquivo registra a migração para a infraestrutura canônica em `infra/`.

Regra atual: não criar `archive`, não criar novos Compose, não criar novas imagens e não manter duplicatas legadas como fonte operacional. Caminhos duplicados devem ser apagados depois que as referências ativas forem migradas para os caminhos canônicos abaixo.

## Estado canônico

- Dockerfiles e contrato de imagem: `infra/docker/images/*` e `infra/docker/images.yaml`.
- Compose: `infra/docker/compose/*`.
- Kubernetes/Kustomize: `infra/kubernetes/base`, `infra/kubernetes/overlays`, `infra/kubernetes/components`, `infra/kubernetes/scripts`, `infra/kubernetes/docs` e `infra/kubernetes/examples`.
- Runtime não sensível: `infra/runtime/*`.
- Scripts de alto nível: `infra/scripts/*`, `infra/docker/scripts/*` e `infra/kubernetes/scripts/*`.

## Docker images

| Workload | Caminho canônico |
| --- | --- |
| server | `infra/docker/images/server/Dockerfile` |
| kfe-service | `infra/docker/images/kfe-service/Dockerfile` |
| mpc-sidecar | `infra/docker/images/mpc-sidecar/Dockerfile` |
| vault | `infra/docker/images/vault/Dockerfile` |
| tor | `infra/docker/images/tor/Dockerfile` |
| web-page | `infra/docker/images/web-page/Dockerfile` |

## Docker Compose

| Perfil | Caminho canônico |
| --- | --- |
| local | `infra/docker/compose/local.compose.yaml` |
| local limits | `infra/docker/compose/local.limits.compose.yaml` |
| local KFE | `infra/docker/compose/local.kfe.compose.yaml` |
| hardened | `infra/docker/compose/hardened.compose.yaml` |

## Kubernetes / Kustomize

| Área | Caminho canônico |
| --- | --- |
| base | `infra/kubernetes/base/*` |
| overlays | `infra/kubernetes/overlays/*` |
| components | `infra/kubernetes/components/*` |
| scripts | `infra/kubernetes/scripts/*` |
| docs | `infra/kubernetes/docs/*` |
| examples | `infra/kubernetes/examples/*` |

## Runtime support

| Serviço | Caminho canônico |
| --- | --- |
| bitcoin | `infra/runtime/bitcoin/*` |
| lightning | `infra/runtime/lightning/*` |
| vault | `infra/runtime/vault/*` |
| tor | `infra/runtime/tor/*` |
| postgres | `infra/runtime/postgres/*` |
| web | `infra/runtime/web/*` |
| observability | `infra/runtime/observability/*` |
| host | `infra/runtime/host/*` |

## Regras para o agente

1. Não transferir chaves privadas, certificados reais, secrets ou material sensível sem plano explícito.
2. Não criar arquivos Compose, Dockerfiles, imagens ou manifests novos durante esta limpeza.
3. Atualizar apenas referências de caminho para os artefatos canônicos já existentes.
4. Apagar duplicatas legadas, não arquivar.
5. Validar com `docker compose config` nos Compose afetados e `kubectl kustomize` nos overlays afetados.
