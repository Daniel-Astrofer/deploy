# Kerosene Infra Migration Map

Este arquivo orienta a migração incremental. A estrutura final já existe em
`infra/` e a primeira transferência de conteúdo real foi aplicada para
Dockerfiles, Compose e Kustomize, preservando os caminhos legados para
compatibilidade.

## Status da fase atual

- Dockerfiles transferidos para `infra/docker/images/*`.
- `infra/docker/images.yaml` aponta `dockerfile` para `infra/` e mantém
  `legacy_dockerfile` como referência da origem antiga.
- Compose files transferidos para `infra/docker/compose/*` com caminhos
  relativos reescritos para a nova raiz.
- Kustomize `base`, `overlays`, `scripts`, `docs` e `examples` transferidos
  para `infra/kubernetes`.
- Kustomize `components/*` permanece como área de decomposição futura; os
  overlays validados ainda não dependem desses componentes.
- Runtime assets não sensíveis foram copiados para `infra/runtime`; certificados,
  chaves, onion auth e dados persistentes continuam fora da transferência.
- Caminhos legados ainda não foram removidos.

## Docker images

| Workload | Origem atual | Destino alvo |
| --- | --- | --- |
| server | `backend/kerosene-infrastructure/images/app/Dockerfile` | `infra/docker/images/server/Dockerfile` |
| kfe-service | `backend/kerosene-infrastructure/images/kfe/Dockerfile` | `infra/docker/images/kfe-service/Dockerfile` |
| mpc-sidecar | `backend/mpc-sidecar/Dockerfile` | `infra/docker/images/mpc-sidecar/Dockerfile` |
| vault | `backend/kerosene-infrastructure/images/vault/Dockerfile` | `infra/docker/images/vault/Dockerfile` |
| tor | `backend/kerosene/deploy/tor/Dockerfile` | `infra/docker/images/tor/Dockerfile` |
| web-page | Dockerfile temporário em `backend/kerosene-infrastructure/k8s/scripts/import-local-docker-images.sh` | `infra/docker/images/web-page/Dockerfile` |

## Docker Compose

| Origem atual | Destino alvo |
| --- | --- |
| `backend/kerosene-infrastructure/docker-compose.local.yml` | `infra/docker/compose/local.yml` |
| `backend/kerosene-infrastructure/docker-compose.local.limits.yml` | `infra/docker/compose/local.limits.yml` |
| `backend/kerosene-infrastructure/docker-compose.kfe.local.yml` | `infra/docker/compose/local.kfe.yml` |
| `backend/kerosene/deploy/compose/hardened.yml` | `infra/docker/compose/hardened.yml` |

## Kubernetes / Kustomize

| Origem atual | Destino alvo |
| --- | --- |
| `backend/kerosene-infrastructure/k8s/base/*` | `infra/kubernetes/base/*` |
| `backend/kerosene-infrastructure/k8s/overlays/*` | `infra/kubernetes/overlays/*` |
| `backend/kerosene-infrastructure/k8s/scripts/*` | `infra/kubernetes/scripts/*` |
| `backend/kerosene-infrastructure/k8s/docs/*` | `infra/kubernetes/docs/*` |
| `backend/kerosene-infrastructure/k8s/examples/*` | `infra/kubernetes/examples/*` |

## Runtime support

| Origem atual | Destino alvo |
| --- | --- |
| `backend/kerosene-infrastructure/bitcoin/*` | `infra/runtime/bitcoin/*` |
| `backend/kerosene-infrastructure/vault/*` | `infra/runtime/vault/*` |
| `backend/kerosene-infrastructure/web/*` | `infra/runtime/web/*` |
| `backend/kerosene/deploy/tor/*` | `infra/runtime/tor/*` |
| `backend/kerosene/deploy/postgres/*` | `infra/runtime/postgres/*` |
| `backend/kerosene/deploy/observability/*` | `infra/runtime/observability/*` |

## Regras para o agente

1. Não transferir chaves privadas, certificados reais, secrets ou material sensível sem plano explícito.
2. Após cada transferência, atualizar `infra/docker/images.yaml` ou os `kustomization.yaml` correspondentes.
3. Manter wrappers legados até todos os scripts e docs apontarem para `infra/`.
4. Validar com `kubectl kustomize` nos overlays afetados e com `docker compose config` nos Compose afetados.
5. Só remover caminhos legados após validação e depois de atualizar scripts, docs e testes.
