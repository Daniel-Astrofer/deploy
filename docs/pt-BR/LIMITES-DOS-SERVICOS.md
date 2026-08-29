# Limites dos serviços

O `kerosene-deploy` conecta workloads publicados. Ele não incorpora o código
nem assume a responsabilidade de domínio dos demais repositórios.

| Repositório | Responsabilidade própria | O que o Deploy consome |
|---|---|---|
| `kerosene-core` | Comportamento do Auth e do KFE | Imagens, health checks e contrato de configuração |
| `kerosene-clients` | Aplicação Flutter/web | Imagem web publicada |
| `kerosene-vault` | Custódia, DKG, FROST e assinatura | Imagem do Vault e contrato de execução documentado |
| `kerosene-node` | Identidade, descoberta e membership | Imagem do Node e contrato de execução documentado |
| `kerosene-contracts` | Schemas e versões compatíveis | Identificadores de versões publicadas |
| `kerosene-deploy` | Compose, Kubernetes, helpers de runtime, observabilidade e validação de rollout | Imagens imutáveis e secrets fornecidos pelo ambiente |

## Empacotamento não é propriedade do código

Os Dockerfiles em `infra/docker/images/` são receitas de empacotamento. No
desenvolvimento local, eles podem receber outro repositório como contexto de
build. Isso não copia o serviço para o Deploy nem transfere sua responsabilidade.

Staging e produção devem receber imagens fixadas por digest. Esses ambientes não
devem compilar os serviços a partir do checkout do Deploy.

## Onde cada mudança deve ser feita

- API, regra de domínio ou protocolo: repositório proprietário do serviço.
- Schema e compatibilidade: `kerosene-contracts`.
- Manifests, conexão de containers, observabilidade e gates de rollout: este repositório.
- Secrets: ambiente operacional; aqui ficam somente nomes e contratos de montagem.

A implementação de mTLS e CometBFT está deliberadamente fora desta onda. No
futuro, o Deploy deverá apenas configurar contratos publicados pelos serviços.
