# Limites dos serviços

O `kerosene-deploy` conecta workloads publicados. Ele não incorpora o código
nem assume a responsabilidade de domínio dos demais repositórios.

| Repositório | Responsabilidade própria | O que o Deploy consome |
|---|---|---|
| `kerosene-core` | Auth, políticas da borda pública e gateway público do KFE | Imagem do Server, health checks e contrato de configuração |
| `kerosene-kfe` | Motor financeiro, carteiras, rails e reconciliação | Imagem do KFE, health checks e contrato de configuração |
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

O mTLS SPIFFE entre Auth e KFE está implementado nas branches dos serviços e é
ativado somente pelo overlay explícito `staging-spiffe`. Evidência em cluster
ainda é gate de promoção. A migração de identidade de Vault/Node e o CometBFT
continuam pertencendo aos serviços; o Deploy apenas os conectará após a
publicação dos contratos correspondentes.
