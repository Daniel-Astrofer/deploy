# Identidade SPIFFE/SPIRE em staging

## O que esta etapa entrega

Cada workload recebe um certificado X.509 de curta duração, renovado pelo
SPIRE, por meio de um socket Unix montado pelo CSI. O socket não contém um
secret estático no manifesto e não é exposto pela rede.

O perfil explícito `staging-spiffe` agora muda o transporte entre Auth e KFE.
Os dois processos consomem o SVID rotativo em memória, exigem a SPIFFE ID exata
do par e usam TLS 1.3 na porta interna `8443`. O segredo estático de transporte
não é montado nesse perfil. As NetworkPolicies também removem o acesso cruzado
pela porta pública `8080`.

Todas as rotas do KFE visíveis ao cliente (`/kfe`, `/api/public/kfe` e
`/api/admin/kfe`) entram pelo gateway do Auth. O workload web não possui saída
direta para o KFE; o Auth preserva a rota e encaminha somente headers permitidos
pelo conector interno autenticado.

Essa ativação depende de imagens construídas com as mudanças correspondentes
em Shared, Auth e KFE. A barreira de admissão agora impede que um editor comum
copie labels, ServiceAccount, nome de container e volume CSI para obter a mesma
identidade. Vault e Kerosene Node recebem SVIDs, porém ainda não os usam como
identidade ativa de transporte. Ainda falta evidência end-to-end de handshake,
rotação e alta disponibilidade.

## Separação de responsabilidades

| Camada | Responsabilidade |
|---|---|
| `bootstrap` | Cria namespaces, CRD fixada e RBAC mínimo |
| `admission` | Reserva identidades e permite escrita de workloads somente pelo principal GitOps |
| `server` | Emite identidades no trust domain de staging |
| `agent` | Atesta processos no nó e oferece a Workload API |
| `registration` | Define qual container pode receber qual identidade |
| `staging-spiffe` | Ativa socket, identidades estritas, endpoints mTLS Auth/KFE e isolamento de rede |
| `staging-vault-spiffe` | Monta socket/labels de Vault e Node sem migrar seu transporte ainda |

O servidor fica no namespace `spire-server`, com Pod Security `restricted`. O
agente e o CSI ficam sozinhos em `spire-system`, porque `hostPID`, `hostPath` e
propagação de mount exigem privilégio. O container Tor que compartilha pod com
Kerosene Node não recebe o socket e não corresponde ao seletor do Node.

## Identidades emitidas

| Workload | SPIFFE ID |
|---|---|
| Auth | `spiffe://staging.kerosene.internal/service/auth` |
| KFE | `spiffe://staging.kerosene.internal/service/kfe` |
| Vault | `spiffe://staging.kerosene.internal/service/vault/node/<node-id>` |
| Node | `spiffe://staging.kerosene.internal/service/node/plane/<plano>/instance/<pod>` |

Uma label isolada não concede identidade. O API server exige a combinação exata
de namespace, controller/owner, ServiceAccount, role e container. Somente o
container registrado monta o socket; Tor, init containers, sidecars e
ephemeral containers são recusados. As imagens precisam usar digest e o
repositório previamente aprovado no namespace. A autorização de negócio
continua dependendo do roster assinado: possuir um SVID válido prova identidade
de workload, mas não autoriza assinatura, custódia ou membership.

## Validação

```bash
bash infra/kubernetes/scripts/validate-staging-spire.sh
```

O gate verifica checksum da CRD, políticas CEL fail-closed, bindings `Deny` e
`Audit`, RBAC negativo, imagens por digest, isolamento dos namespaces,
registros específicos e mounts CSI. O teste
`staging-spire-admission-kind-test.sh` também executa ataques reais contra um
Kind efêmero e apaga apenas esse cluster. O gate recusa explicitamente
`KEROSENE_SPIRE_TARGET=production`.

## Aplicação por fases

Use o instalador transacional, que apenas organiza comandos Kubernetes nativos
na ordem segura:

```bash
SERVER_IMAGE=registry.example/kerosene-core@sha256:... \
KFE_SERVICE_IMAGE=registry.example/kerosene-kfe@sha256:... \
VAULT_IMAGE=registry.example/kerosene-vault@sha256:... \
NODE_IMAGE=registry.example/kerosene-node@sha256:... \
TOR_IMAGE=registry.example/kerosene-tor@sha256:... \
  bash infra/kubernetes/scripts/install-staging-spire.sh
```

A ordem aplicada é: bootstrap → dois namespaces revisados e allow-list de
repositórios → admissão/RBAC → registros via GitOps → servidor → agente. A
identidade que executa a instalação precisa poder impersonar exclusivamente
`system:serviceaccount:kerosene-gitops:kerosene-deployer`. Essa permissão de
cluster não é concedida pelo repositório público e deve pertencer ao pipeline
ou operador de bootstrap.

Depois aplique os workloads com o executor existente, sempre informando imagens
imutáveis. Para o Core use `staging-spiffe`; para o Vault independente use
`staging-vault-spiffe`. As mesmas variáveis `..._IMAGE` exigidas pelos perfis de
staging comuns continuam obrigatórias.

Antes de usar `staging-spiffe`, provisione o Secret
`kfe-service-secrets` com a chave `fee-quote-signing-secret`. Esse material
assina cotações da aplicação e não pode reutilizar o segredo de transporte
removido.

Não aplique o overlay de workload antes de o DaemonSet estar pronto: o kubelet
não conseguirá montar o volume CSI e os pods ficarão pendentes.

O controller reconcilia somente a classe `kerosene-staging` e prefixa as
entradas que possui. O webhook do controller continua desligado. A validação
semântica é feita pelas `ValidatingAdmissionPolicy` nativas: somente quatro
registros exatos são aceitos e `admin`, downstream, federação, DNS adicional e
TTL divergente são recusados.

## Rollback seguro

1. Reimplante os digests anteriores, já aprovados, dos perfis
   `staging-spiffe` e `staging-vault-spiffe`.
2. Confirme mTLS, saúde e identidade exata dos pares.
3. Preserve o PVC do SPIRE Server e as evidências para diagnóstico.

Depois que a admissão é ativada, os perfis antigos sem SPIFFE são recusados
porque reutilizam ServiceAccounts reservadas sem o contrato de identidade.
Remover a barreira ou restaurar o segredo compartilhado é uma migração
break-glass separada, com aprovação de segurança; não é rollback automático.

## Por que ainda não serve para produção

- há um servidor SPIRE com SQLite, sem alta disponibilidade;
- as regras de saída para API Kubernetes e kubelet usam `0.0.0.0/0` limitado às
  portas 443 e 10250 para portabilidade de staging;
- ainda falta comprovar em cluster os handshakes Auth/KFE, a rotação de SVID e
  os testes negativos de SPIFFE ID incorreta;
- Vault e Node ainda precisam migrar dos arquivos de certificado estático para
  identidades de workload;
- máquinas fora do cluster ainda não possuem fluxo de node attestation;
- backup, restauração, rotação de CA e federação entre clusters precisam de
  ensaio operacional.

Produção deve usar datastore HA, CIDRs reais do cluster, imagens fixadas,
monitoramento de expiração/rotação e política fail-closed. O SPIRE não descobre
serviços: endpoints vêm do deploy e membership vem do roster assinado; o SPIRE
somente prova a identidade do processo que está se conectando.

A implementação e a evidência da admissão são rastreadas em
[controle contra impersonação de SPIFFE ID #36](https://github.com/Daniel-Astrofer/kerosene-deploy/issues/36).
A evidência de produção ainda pendente está em
[SPIRE end-to-end/HA #37](https://github.com/Daniel-Astrofer/kerosene-deploy/issues/37).
