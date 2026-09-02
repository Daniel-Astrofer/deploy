# Deploy de staging

O overlay público suporta `local` e `staging`. Produção permanece fora deste
repositório e está bloqueada pelos gates descritos em
`SERVICE_INTEGRATION_SECURITY_PLAN.md`.

## Rede

- Bitcoin: testnet3.
- Lightning: `testnet`/`lntb`, nome usado pelo LND para Bitcoin testnet3.
- Regtest não é usado pelos overlays ativos.

## Pré-requisitos

- `kubectl` com contexto correto.
- `kustomize` quando imagens forem substituídas por variável.
- Imagens publicadas e preferencialmente referenciadas por digest.
- StorageClass padrão capaz de provisionar PVCs `ReadWriteOnce`.
- Secrets centrais já provisionados:
  - `server-secrets`;
  - `kfe-service-secrets`, obrigatório no perfil `staging-spiffe`, com
    `fee-quote-signing-secret` independente;
  - `kerosene-db-secrets`;
  - `kerosene-redis-secrets`;
  - `kerosene-bitcoin-secrets`;
  - `kerosene-lnd-secrets`.
  - `staging-smoke-credentials`, com um usuário sintético já cadastrado.
  - `kerosene-node-genesis`, com `genesis-trust-bundle.json`;
  - `kerosene-node-identity`, com `identity.key` e `member-id`;
  - `kerosene-node-mtls`, com a CA, folha servidor e identidade cliente.

O script de deploy não cria nem imprime esses valores.

No perfil comum `staging`, `server-secrets` ainda precisa conter
`kfe-internal-shared-secret` para permitir rollback. No perfil
`staging-spiffe`, esse valor não é montado em Auth nem KFE e é substituído por
identidades SPIFFE rotativas e autorização exata do par.

O namespace é proprietário dos StatefulSets `staging-postgres`,
`staging-redis`, `staging-bitcoin`, `staging-lnd` e `staging-tor`. Nenhum
Service de staging seleciona pods de `kerosene-local`.

`local` e `local-full` permanecem somente como ferramentas de workstation. Eles
não são fallback, origem de dados nem destino de promoção para staging.

## Inicialização do LND

O Deploy cria o StatefulSet e o PVC, mas não gera nem importa seed. Antes da
primeira promoção, o operador inicializa a wallet do LND por canal seguro,
registra o backup de seed e o static channel backup no secret manager e
provisiona `LIGHTNING_LND_MACAROON` em `kerosene-lnd-secrets`.

Essa cerimônia é deliberadamente externa ao pipeline. Reinicializar o PVC sem
restaurar o material de recuperação cria outra identidade Lightning.

## Certificados e secrets dos vaults

Os scripts de cerimônia pertencem ao repositório `kerosene-vault`. Antes dos
comandos abaixo, aponte explicitamente para o checkout correto:

```bash
export KEROSENE_VAULT_DIR=/caminho/para/kerosene-vault
```

Gere uma CA de cerimônia e folhas independentes:

```bash
VAULT_CEREMONY_MTLS_OUT=/caminho/seguro/ceremony-certs \
  bash "$KEROSENE_VAULT_DIR/scripts/gen_ceremony_mtls_certs.sh"
```

Provisione os seis Secrets dos três vaults e o Secret cliente mTLS do KFE:

```bash
VAULT_CEREMONY_MTLS_OUT=/caminho/seguro/ceremony-certs \
  bash "$KEROSENE_VAULT_DIR/scripts/provision_staging_k8s_secrets.sh"
```

Cada vault recebe certificado servidor, certificado cliente e passphrase
próprios. O KFE recebe somente sua folha cliente, chave PKCS#8 e CA. O diretório
de cerimônia deve ser protegido e ter backup seguro.

## Validação

```bash
infra/kubernetes/scripts/deploy.sh local --dry-run
SERVER_IMAGE=registry/kerosene-server@sha256:... \
KFE_SERVICE_IMAGE=registry/kerosene-kfe@sha256:... \
WEB_PAGE_IMAGE=registry/kerosene-web@sha256:... \
VAULT_IMAGE=registry/kerosene-vault@sha256:... \
NODE_IMAGE=registry/kerosene-node@sha256:... \
TOR_IMAGE=registry/kerosene-tor@sha256:... \
  infra/kubernetes/scripts/deploy.sh staging --dry-run
```

Se o namespace staging ainda não existir, o segundo comando realiza validação
cliente e informa como habilitar o dry-run no servidor sem executar o deploy.

## Deploy

Use imagens imutáveis:

```bash
SERVER_IMAGE=registry/kerosene-server@sha256:... \
KFE_SERVICE_IMAGE=registry/kerosene-kfe@sha256:... \
WEB_PAGE_IMAGE=registry/kerosene-web@sha256:... \
VAULT_IMAGE=registry/kerosene-vault@sha256:... \
NODE_IMAGE=registry/kerosene-node@sha256:... \
TOR_IMAGE=registry/kerosene-tor@sha256:... \
  infra/kubernetes/scripts/deploy.sh staging
```

Depois de instalar e validar o SPIRE conforme `docs/pt-BR/SPIRE-STAGING.md`, use
o perfil que ativa mTLS entre Auth e KFE:

```bash
SERVER_IMAGE=registry/kerosene-server@sha256:... \
KFE_SERVICE_IMAGE=registry/kerosene-kfe@sha256:... \
WEB_PAGE_IMAGE=registry/kerosene-web@sha256:... \
NODE_IMAGE=registry/kerosene-node@sha256:... \
TOR_IMAGE=registry/kerosene-tor@sha256:... \
  infra/kubernetes/scripts/deploy.sh staging-spiffe
```

O deploy falha se:

- qualquer imagem de aplicação/Tor não estiver fixada por digest;
- algum Secret obrigatório estiver ausente;
- o JDBC não apontar para `kerosene-db-headless`;
- o manifesto mencionar `kerosene-local` ou outro namespace Kerosene;
- PostgreSQL, Redis, Bitcoin, LND ou Tor não ficar pronto;
- houver conflito de ownership no server-side apply;
- server, KFE, web ou qualquer um dos três vaults não ficar pronto.
- o login sintético ou a prontidão de três nós do quorum falhar.

`KEROSENE_FORCE_CONFLICTS=1` existe somente para recuperação operacional
deliberada e emite aviso.

`KEROSENE_SKIP_STAGING_SMOKES=1` existe somente para recuperação deliberada;
um deploy normal nunca deve usá-lo.

## Onion

`staging-tor` publica `web-page:8080` e o Bank-plane Kerosene Node em `8800`
como hidden services no mesmo onion. O Node não contém chaves FROST nem
credenciais Bitcoin/LND. A identidade onion permanece no PVC
`data-staging-tor-0`. Consulte o hostname sem exportar a chave:

```bash
kubectl -n kerosene-staging exec staging-tor-0 -- \
  cat /var/lib/tor/kerosene_service/hostname
```

O snapshot do PVC é parte obrigatória do plano de recuperação.

## Cerimônia do Kerosene Node e primeiro Vault

Antes do deploy, gere a identidade persistente de cada Node com o utilitário
`kerosene-node-keygen`. Use o `member_id` e a chave pública emitidos para montar
o `GenesisTrustBundleV1`; então provisione exatamente a mesma `identity.key`.
Uma identidade aleatória criada no rollout não seria aceita pelo bundle.

O primeiro `staging-vault` inicia isolado, com seu Vault-plane Node e sem
manifesto. O Vault fica localmente utilizável, mas sem quorum financeiro.
Depois que Tor materializar o hostname:

1. emita/rotacione a folha TLS do Node com SAN para o onion e, no namespace do
   Vault, para `tor.kerosene-staging-vault.svc`;
2. publique no Node um `MembershipManifestV1` assinado com endpoint
   `https://<onion>:8800`;
3. reinicie o Vault para ele carregar o snapshot verificado de peers;
4. configure `vault-discovery` com URLs `https://<mesmo-onion>:7801`;
5. configure `vault-node-directory.node-url` com
   `https://<mesmo-onion>:8800`;
6. habilite KFE/Vault mesh somente depois do roster assinado e do quorum.

Os endpoints de bootstrap são dicas de roteamento, não autorização. Novos
Vaults entram pelo fluxo OLD → JOINT → NEW do manifesto. O deploy nunca cria
shares, nunca promove um Vault sozinho a quorum e nunca ativa um signer.

## Rollback e recuperação

- Reaplique os digests anteriores; não remova namespace nem PVCs.
- Restaure PostgreSQL/Bitcoin em PVCs substitutos a partir do último snapshot
  verificado.
- Redis pode ser recriado somente com os serviços de aplicação parados.
- Recupere o LND usando seed e static channel backup sob controle do operador.
- Recupere o onion pelo snapshot do PVC `data-staging-tor-0`.
- Vault shares seguem a cerimônia própria do `kerosene-vault`; este deploy não
  cria nem ativa signers.

Depois de qualquer rollback:

```bash
bash infra/kubernetes/scripts/smoke-staging.sh
```

## Limites do staging

Os vaults Kubernetes são um ambiente de integração, não fornecem independência
administrativa. Produção exige vaults externos em máquinas/domínios distintos,
Tor+mTLS, hardware real e conclusão dos gates PQ.
