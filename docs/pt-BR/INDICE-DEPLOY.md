# Índice do deploy

## Responsabilidade

O `kerosene-deploy` recebe imagens publicadas pelos serviços e define como elas
são configuradas, conectadas, validadas, observadas e promovidas. O
empacotamento local pode usar checkouts externos. Código de negócio, contratos,
custódia e clientes pertencem aos repositórios próprios.

Responsabilidades detalhadas: [Limites dos serviços](LIMITES-DOS-SERVICOS.md).

## Comandos públicos

| Comando | Responsabilidade | Estado |
|---|---|---|
| `bash infra/start.sh` | Inicia o ambiente integrado `local-full` | Entrada local canônica |
| `bash infra/stop.sh` | Para workloads e preserva dados | Entrada local canônica |
| `bash infra/recreate.sh` | Recria o ambiente local | Entrada local canônica |
| `bash infra/status.sh` | Mostra recursos e endpoints | Entrada local canônica |
| `bash infra/logs.sh` | Coleta e mostra logs | Entrada local canônica |
| `bash infra/test.sh` | Valida scripts e manifests | Entrada de validação canônica |
| `bash infra/kubernetes/scripts/deploy.sh <ambiente>` | Executor por ambiente | Interno/CI; uso explícito |

## Entradas de compatibilidade

Os arquivos abaixo continuam executáveis para não quebrar automações antigas,
mas fluxos novos não devem depender deles:

- `infra/start-complete.sh`
- `infra/deploy.sh`
- `infra/kubernetes/deploy.sh`
- `infra/kubernetes/scripts/apply.sh`
- `infra/kubernetes/scripts/deploy-local.sh`
- `infra/kubernetes/scripts/deploy-staging.sh`
- `infra/kubernetes/scripts/start-local.sh`
- `infra/kubernetes/scripts/stop-local.sh`
- `infra/kubernetes/scripts/status-local.sh`
- `infra/kubernetes/scripts/logs-local.sh`
- `infra/kubernetes/import-local-images.sh`
- `infra/kubernetes/validate-local-ha.sh`
- `infra/kubernetes/scripts/wait.sh`
- `infra/kubernetes/scripts/logs-vault.sh`
- `infra/scripts/local/{start,stop,recreate,restart,state}.sh`
- `infra/docker/scripts/compose-local.sh`
- `infra/docker/scripts/compose-local-kfe.sh`

Nenhum wrapper será removido nesta primeira onda.

## Docker

| Caminho | Responsabilidade |
|---|---|
| `infra/docker/images.yaml` | Inventário de imagens para integração local |
| `infra/docker/images/` | Receitas transitórias; releases pertencem aos serviços |
| `infra/docker/compose/vault-mesh-lab.compose.yaml` | Vault Mesh local com token estático |
| `infra/docker/compose/vault-mesh-staging.compose.yaml` | Exercício local semelhante a staging com mTLS |
| `infra/docker/compose/vault-mesh-tor.compose.yaml` | Exercício da mesh via Tor |
| `infra/docker/compose/vault-mesh-ceremony.compose.yaml` | Composição para cerimônia |
| `infra/docker/compose/local.limits.compose.yaml` | Override legado do Compose removido; não funciona sozinho |
| `infra/docker/scripts/` | Helpers de Compose e build |

Compose é usado em desenvolvimento e integração. Produção usa Kubernetes,
overlays privados e imagens fixadas por digest.

`local.limits.compose.yaml` ainda nomeia serviços do antigo Compose local e não
valida sozinho nem junto aos arquivos Compose atuais do Vault. Ele permanece
somente porque `infra/scripts/local/` ainda o referencia.

## Kubernetes

| Caminho | Responsabilidade |
|---|---|
| `infra/kubernetes/base/` | Recursos compartilhados |
| `infra/kubernetes/components/` | Componentes Kustomize reutilizáveis |
| `infra/kubernetes/components/spire/` | Plano de identidade de workloads exclusivo de staging |
| `infra/kubernetes/overlays/local/` | Ambiente local leve |
| `infra/kubernetes/overlays/local-full/` | Ambiente local integrado canônico |
| `infra/kubernetes/overlays/local-ha/` | Topologia local de alta disponibilidade |
| `infra/kubernetes/overlays/staging/` | Runtime público do Core em staging |
| `infra/kubernetes/overlays/staging-vault/` | Runtime independente do Vault em staging |
| `infra/kubernetes/overlays/staging-spiffe/` | Core de staging com mount aditivo da Workload API |
| `infra/kubernetes/overlays/staging-vault-spiffe/` | Vault independente com mount aditivo da Workload API |
| `infra/kubernetes/scripts/` | Deploy, validação, smoke e operação |
| `infra/kubernetes/tests/` | Testes de regressão dos scripts de deploy |

`infra/kubernetes/scripts/deploy-local-ha.sh` é um executor especializado do
ambiente `local-ha`, não um wrapper de compatibilidade. Ele não é a entrada
local padrão.

Os overlays de produção não ficam nesta árvore pública.

## Classes de scripts

| Caminho | Classe |
|---|---|
| `infra/*.sh` | Entradas locais públicas e wrappers de compatibilidade |
| `infra/scripts/quorum.sh` | Dispatcher interno das entradas públicas |
| `infra/scripts/common.sh`, `polyrepo-env.sh` | Bibliotecas; devem ser carregadas por outros scripts |
| `infra/scripts/local/` | Operações Compose legadas mantidas por compatibilidade |
| `infra/scripts/beta/` | Smokes experimentais; não são gates de produção |
| `infra/kubernetes/scripts/deploy-local-ha.sh` | Executor especializado de `local-ha` |
| `infra/kubernetes/scripts/validate-*.sh` | Validações sem aplicação |
| `infra/kubernetes/scripts/smoke-*.sh` | Verificações posteriores ao deploy |
| `infra/production/` | Gates fail-closed de evidência e validação |
| `infra/runtime/` | Entrypoints e configuração interna dos containers |

## Fallback legado conhecido

O helper local aceita `KEROSENE_VAULT_MESH_PROFILE=staging`, tenta gerar
certificados e, se essa geração falhar, usa a mesh de laboratório com token
estático. Esse comportamento é legado, exclusivamente local e não comprova que
staging ou produção estão prontos. Ele foi mantido nesta onda para evitar uma
mudança de runtime sem evidência e janela de migração próprias.
