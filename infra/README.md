# Kerosene Infrastructure

Este diretório é a camada canônica de infraestrutura da Kerosene.

A migração é incremental, mas `infra/` é a camada canônica. Duplicatas antigas não devem ser arquivadas: devem ser removidas após validação. Novos contratos e documentação devem nascer aqui.

## Layout alvo

```text
infra/
  docker/       Imagens, contratos de build/tag, wrappers Compose e destino futuro dos arquivos Compose.
  kubernetes/   Kustomize, overlays, scripts de deploy e runbooks Kubernetes.
  runtime/      Configurações de runtime para serviços de apoio: Bitcoin, Vault, Tor, Web/Nginx, Postgres.
  scripts/      Automação separada por domínio: local, docker, kubernetes, db, logs e release.
```

## Regras

- Código de produto fica em `backend/` e `frontend/`.
- Dockerfiles e contratos de imagem ficam sob `infra/docker`.
- Kustomize e deploy Kubernetes ficam sob `infra/kubernetes`.
- Scripts de operação devem sair gradualmente de `scripts/` raiz e entrar em `infra/scripts`, mantendo wrappers de compatibilidade.
- Arquivos sensíveis, certificados, chaves e segredos não devem ser movidos sem plano explícito de rotação.

## Estado atual

`infra/` já contém os caminhos canônicos dos Dockerfiles, Compose files e
manifests Kustomize. Caminhos legados devem permanecer apenas como wrappers
mínimos quando forem necessários para compatibilidade temporária; duplicatas
operacionais devem ser apagadas depois da validação.
