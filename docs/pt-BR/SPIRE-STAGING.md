# Identidade SPIFFE/SPIRE em staging

## O que esta etapa entrega

Esta etapa cria a base de identidade dos processos. Cada workload recebe um
certificado X.509 de curta duração, renovado pelo SPIRE, por meio de um socket
Unix montado pelo CSI. O socket não contém um secret estático no manifest e não
é exposto pela rede.

Ela ainda não muda o protocolo entre os serviços. Auth, KFE, Vault e Kerosene
Node continuam usando os certificados estáticos atuais até que seus clientes e
servidores passem a consumir a Workload API e validar a SPIFFE ID do par. Isso
evita uma migração implícita e permite rollback.

## Separação de responsabilidades

| Camada | Responsabilidade |
|---|---|
| `bootstrap` | Cria namespaces, CRD fixada e RBAC mínimo |
| `server` | Emite identidades no trust domain de staging |
| `agent` | Atesta processos no nó e oferece a Workload API |
| `registration` | Define qual container pode receber qual identidade |
| overlays `*-spiffe` | Montam o socket apenas nos containers Kerosene autorizados |

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

Uma label isolada não concede identidade. O registro também exige namespace
autorizado, ServiceAccount exata e nome exato do container. A autorização de
negócio continuará dependendo do roster assinado: possuir um SVID válido prova
identidade de workload, mas não autoriza assinatura, custódia ou membership.

## Validação

```bash
bash infra/kubernetes/scripts/validate-staging-spire.sh
```

O gate verifica checksum da CRD, imagens por digest, isolamento dos namespaces,
registros específicos e mounts CSI. Ele recusa explicitamente
`KEROSENE_SPIRE_TARGET=production`.

## Aplicação por fases

Use comandos Kubernetes nativos para a infraestrutura de identidade:

```bash
kubectl apply -k infra/kubernetes/components/spire/bootstrap
kubectl apply -k infra/kubernetes/components/spire/server
kubectl -n spire-server rollout status statefulset/spire-server --timeout=5m
test -n "$(kubectl -n spire-system get configmap/spire-bundle -o jsonpath='{.data.bundle\.crt}')"
kubectl apply -k infra/kubernetes/components/spire/agent
kubectl -n spire-system rollout status daemonset/spire-agent --timeout=10m
kubectl apply -f infra/kubernetes/overlays/staging/namespace.yaml
kubectl apply -f infra/kubernetes/overlays/staging-vault/namespace.yaml
kubectl apply -k infra/kubernetes/components/spire/registration
bash infra/kubernetes/scripts/preflight-staging-spire.sh core
bash infra/kubernetes/scripts/preflight-staging-spire.sh vault
```

Depois aplique os workloads com o executor existente, sempre informando imagens
imutáveis. Para o Core use `staging-spiffe`; para o Vault independente use
`staging-vault-spiffe`. As mesmas variáveis `..._IMAGE` exigidas pelos perfis de
staging comuns continuam obrigatórias.

Não aplique o overlay de workload antes de o DaemonSet estar pronto: o kubelet
não conseguirá montar o volume CSI e os pods ficarão pendentes.

O controller reconcilia somente a classe `kerosene-staging` e prefixa as
entradas que possui. O webhook de admissão do controller está desligado nesta
base mínima; a CRD oferece validação estrutural e o gate do repositório valida
os objetos conhecidos, mas produção precisa habilitar validação semântica.

## Rollback seguro

1. Reimplante `staging` e `staging-vault`, com os mesmos digests, para remover os
   mounts CSI sem trocar as imagens.
2. Confirme a saúde dos serviços e dos certificados estáticos.
3. Remova `components/spire/registration`.
4. Remova `components/spire/agent` e depois `components/spire/server`.
5. Preserve o PVC do servidor para diagnóstico e backup.
6. Remova RBAC e CRD somente após confirmar que não existe nenhum
   `ClusterSPIFFEID`.

## Por que ainda não serve para produção

- há um servidor SPIRE com SQLite, sem alta disponibilidade;
- as regras de saída para API Kubernetes e kubelet usam `0.0.0.0/0` limitado às
  portas 443 e 10250 para portabilidade de staging;
- os serviços ainda não carregam e renovam SVIDs no TLS;
- ainda não existe política de autorização de peer por SPIFFE ID no código;
- ainda falta uma política de admissão que impeça editores do namespace de
  reutilizar ServiceAccounts e labels reservadas fora do fluxo GitOps;
- o webhook semântico dos recursos SPIRE não faz parte deste staging mínimo;
- máquinas fora do cluster ainda não possuem fluxo de node attestation;
- backup, restauração, rotação de CA e federação entre clusters precisam de
  ensaio operacional.

Produção deve usar datastore HA, CIDRs reais do cluster, imagens fixadas,
monitoramento de expiração/rotação e política fail-closed. O SPIRE não descobre
serviços: endpoints vêm do deploy e membership vem do roster assinado; o SPIRE
somente prova a identidade do processo que está se conectando.

Bloqueios rastreados: [controle contra impersonação de SPIFFE ID #36](https://github.com/Daniel-Astrofer/kerosene-deploy/issues/36)
e [evidência end-to-end/HA do SPIRE #37](https://github.com/Daniel-Astrofer/kerosene-deploy/issues/37).
