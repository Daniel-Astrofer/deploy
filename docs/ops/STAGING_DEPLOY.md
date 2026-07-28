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
- Secrets centrais já provisionados:
  - `server-secrets`;
  - `kerosene-db-secrets`;
  - `kerosene-redis-secrets`;
  - `kerosene-bitcoin-secrets`;
  - `kerosene-lnd-secrets`.

O script de deploy não cria nem imprime esses valores.

## Certificados e secrets dos vaults

Gere uma CA de cerimônia e folhas independentes:

```bash
VAULT_CEREMONY_MTLS_OUT=/caminho/seguro/ceremony-certs \
  scripts/vault/gen_ceremony_mtls_certs.sh
```

Provisione os seis Secrets dos três vaults e o Secret cliente mTLS do KFE:

```bash
VAULT_CEREMONY_MTLS_OUT=/caminho/seguro/ceremony-certs \
  scripts/vault/provision_staging_k8s_secrets.sh
```

Cada vault recebe certificado servidor, certificado cliente e passphrase
próprios. O KFE recebe somente sua folha cliente, chave PKCS#8 e CA. O diretório
de cerimônia deve ser protegido e ter backup seguro.

## Validação

```bash
infra/kubernetes/scripts/deploy.sh local --dry-run
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
  infra/kubernetes/scripts/deploy.sh staging
```

O deploy falha se:

- algum Secret obrigatório estiver ausente;
- houver conflito de ownership no server-side apply;
- server, KFE, web ou qualquer um dos três vaults não ficar pronto.

`KEROSENE_FORCE_CONFLICTS=1` existe somente para recuperação operacional
deliberada e emite aviso.

## Limites do staging

Os vaults Kubernetes são um ambiente de integração, não fornecem independência
administrativa. Produção exige vaults externos em máquinas/domínios distintos,
Tor+mTLS, hardware real e conclusão dos gates PQ.
