# Inicialização nativa com Docker e Kubernetes

Este procedimento usa diretamente `docker`, `kind`, `kubectl`, `openssl` e
`curl`. Não depende dos scripts de inicialização do workspace. Os comandos de
cerimônia são instruções para operadores; não foram executados nesta recuperação.

O overlay `infra/kubernetes/overlays/native-local` descreve a instalação desta
máquina. É uma implantação local com controles de produção, em Bitcoin testnet3.
Um único host/kind não fornece isolamento físico dos participantes nem alta
disponibilidade de produção.

## 1. Componentes e materiais necessários

| Plano | Componentes | Persistência |
| --- | --- | --- |
| Identidade | SPIRE Server, Agent e CSI | Estado do servidor, trust bundle e registros |
| Aplicação | Auth, KFE e Flutter web | Dois bancos independentes, Redis e manifesto de release |
| Rede financeira | Bitcoin Core, dois LNDs e três células de rails | Blocos, carteiras, macaroons e SQLite de idempotência |
| Custódia | Três Nodes, três Vaults e três Tor | Membership, shares cifradas, certificados e identidades onion |
| Entrada | Nginx local e Tor web | Identidade onion web existente |

Os materiais operacionais desta recuperação estão fora do repositório:

```bash
export WORKSPACE=/home/astrofer/workspaces/kerosene
export DEPLOY="$WORKSPACE/deploy"
export OPS_ROOT=/home/astrofer/.config/kerosene/native-recovery
export STATE_ROOT=/home/astrofer/.local/state/kerosene
export CELLS_ROOT="$STATE_ROOT/production-cells"
export RELEASE=native-20261025
export BITCOIN_DATA_ROOT=/media/astrofer/2f8abc85-3c19-47e5-9968-21b8b67f7041/kerosene/bitcoin-data
export BITCOIN_WALLETS_ROOT=/media/astrofer/2f8abc85-3c19-47e5-9968-21b8b67f7041/kerosene/bitcoin-native-wallets
export BITCOIN_HOME_VOLUME=cd517e43e44a07fcb9921945a1e6d6b016cc4eb7c9ef2daa02077ff6e9ec013f
```

`OPS_ROOT` contém segredos recuperados, credenciais novas dos bancos, dumps,
manifestos privados e chaves operacionais. Não versionar, publicar nem imprimir
seu conteúdo. Em infraestrutura nova, esses materiais devem vir do processo
seguro da organização, com identidades e participantes próprios.

Antes de partir, conferir espaço e os volumes reais, inclusive os caminhos cujo
nome sugere um disco externo:

```bash
df -h "$STATE_ROOT" "$WORKSPACE" "$BITCOIN_DATA_ROOT" "$BITCOIN_WALLETS_ROOT"
findmnt -T "$STATE_ROOT"
findmnt -T "$BITCOIN_DATA_ROOT"
docker ps -a
docker volume ls
kubectl config current-context
```

Não apagar volumes, carteiras, shares ou identidades para corrigir uma falha de
inicialização. Não recriar carteiras existentes nem repetir DKG como recuperação.

## 2. Construir as imagens

Os contextos `contracts`, `shared` e `deploy` são necessários nos builds Java.

```bash
docker build --build-context contracts="$WORKSPACE/contracts" --build-context shared="$WORKSPACE/shared" --build-context deploy="$DEPLOY" -f "$DEPLOY/infra/docker/images/server/Dockerfile" -t "kerosene/server:$RELEASE" "$WORKSPACE/users-authentication"
docker build --build-context contracts="$WORKSPACE/contracts" --build-context shared="$WORKSPACE/shared" --build-context deploy="$DEPLOY" -f "$DEPLOY/infra/docker/images/kfe-service/Dockerfile" -t "kerosene/kfe-service:$RELEASE" "$WORKSPACE/krinse-engine"
docker build -f "$DEPLOY/infra/docker/images/node/Dockerfile" -t "kerosene/node:$RELEASE" "$WORKSPACE/discoveryng-node"
docker build -f "$DEPLOY/infra/docker/images/vault/Dockerfile" -t "kerosene/vault:$RELEASE" "$WORKSPACE/vault"
docker build -f "$DEPLOY/infra/docker/images/tor/Dockerfile" -t "kerosene/tor:$RELEASE" "$DEPLOY/infra/runtime/tor"
docker build -f "$DEPLOY/infra/docker/images/bitcoin-rail/Dockerfile" -t "kerosene/bitcoin-rail:$RELEASE" "$WORKSPACE/financial-rails/bitcoin_core_backend"
docker build -f "$DEPLOY/infra/docker/images/lightning-rail/Dockerfile" -t "kerosene/lightning-rail:$RELEASE" "$WORKSPACE/financial-rails/lightning_flask"

cd "$WORKSPACE/clients"
flutter pub get
flutter build web --release --no-pub --csp --no-web-resources-cdn --no-tree-shake-icons --target lib/web_main.dart
docker build --build-context deploy="$DEPLOY" -f "$DEPLOY/infra/docker/images/web-page/Dockerfile" -t "kerosene/web-page:$RELEASE" "$WORKSPACE/clients/build/web"
```

Para releases reais, publicar as imagens no registry aprovado e usar o digest
do manifesto OCI nos manifests. O ID de configuração exibido por
`docker image inspect .Id` não é o digest do manifesto OCI.

## 3. Cluster e identidade

Nesta máquina o cluster existente se chama `kerosene-local`. Não executar
novamente sua criação se ele já estiver ativo.

```bash
kind create cluster --name kerosene-local --image kindest/node:v1.32.2 --wait 90s
kubectl config use-context kind-kerosene-local
kubectl get nodes -o wide
kubectl get --raw=/readyz
kubectl apply --server-side --field-manager=kerosene-native-bootstrap -k "$DEPLOY/infra/kubernetes/components/spire/bootstrap"
kubectl apply --server-side --field-manager=kerosene-native-bootstrap -k "$DEPLOY/infra/kubernetes/components/spire/server"
kubectl apply --server-side --field-manager=kerosene-native-bootstrap -k "$DEPLOY/infra/kubernetes/components/spire/agent"
```

O kind desta instalação usa uma CA de serving própria no kubelet. O Agent deve
confiar nessa CA, obtida diretamente do nó administrado e conferida com
`openssl verify`; não usar `skip_verify`. O ConfigMap
`spire-system/native-kubelet-serving-ca` contém somente a CA pública conferida.

```bash
kubectl -n spire-system create configmap native-kubelet-serving-ca --from-file=ca.crt="$OPS_ROOT/kubelet-serving-ca.crt" --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --server-side --field-manager=kerosene-native-bootstrap -k "$DEPLOY/infra/kubernetes/overlays/native-local/spire-agent"
kubectl -n spire-server rollout status statefulset/spire-server --timeout=60s
kubectl -n spire-system rollout status daemonset/spire-agent --timeout=60s
kubectl get csinode kerosene-local-control-plane -o yaml
```

O registrar CSI precisa acessar o socket e o registro root-owned do kubelet.
Sua execução como UID 0 mantém filesystem somente leitura, capabilities
removidas e proibição de escalada. Ver
[permissões do node-driver-registrar](https://github.com/kubernetes-csi/node-driver-registrar#required-permissions).

Os manifests de identidade existentes usam `staging.kerosene.internal` e os
namespaces revisados abaixo. Uma mudança de trust domain de produção exige
revisar conjuntamente registros, admission, namespaces e IDs esperados.

```bash
kubectl apply -f "$DEPLOY/infra/kubernetes/overlays/staging/namespace.yaml" -f "$DEPLOY/infra/kubernetes/overlays/staging-vault/namespace.yaml"
kubectl annotate namespace kerosene-staging --overwrite kerosene.io/approved-auth-image=docker.io/kerosene/server kerosene.io/approved-kfe-image=docker.io/kerosene/kfe-service kerosene.io/approved-node-image=docker.io/kerosene/node kerosene.io/approved-tor-image=docker.io/kerosene/tor
kubectl annotate namespace kerosene-staging-vault --overwrite kerosene.io/approved-vault-image=docker.io/kerosene/vault kerosene.io/approved-node-image=docker.io/kerosene/node kerosene.io/approved-tor-image=docker.io/kerosene/tor
kubectl apply --server-side --field-manager=kerosene-security-bootstrap -k "$DEPLOY/infra/kubernetes/components/spire/admission"
kubectl --as=system:serviceaccount:kerosene-gitops:kerosene-deployer apply --server-side --field-manager=kerosene-gitops -k "$DEPLOY/infra/kubernetes/components/spire/registration"
```

## 4. Dependências Docker sem entrypoint de inicialização

Executar diretamente os binários, com dados persistidos e arquivos privados.
Os exemplos usam as portas e a bridge desta máquina; conferir os endereços antes
de reutilizá-los em outra instalação.

```bash
docker run -d --name kerosene-native-postgres --restart unless-stopped --network kind --ip 172.18.0.20 --network-alias kerosene-native-postgres --user 70:70 --read-only --cap-drop ALL --security-opt no-new-privileges --memory 768m --cpus 1 --publish 127.0.0.1:55432:5432 --tmpfs /var/run/postgresql:rw,uid=70,gid=70 --tmpfs /tmp:rw,nosuid,nodev,size=32m --mount type=bind,src="$STATE_ROOT/postgres-data",dst=/var/lib/postgresql/data --entrypoint postgres postgres:16.4-alpine3.20 -D /var/lib/postgresql/data/pgdata -c listen_addresses=* -c shared_buffers=128MB -c max_connections=80
docker run -d --name kerosene-native-redis --restart unless-stopped --network host --user 1000:1000 --read-only --cap-drop ALL --security-opt no-new-privileges --memory 256m --mount type=bind,src="$STATE_ROOT/native-redis-data",dst=/data --mount type=bind,src="$OPS_ROOT/redis.conf",dst=/run/redis.conf,readonly --entrypoint redis-server redis:7.4.0-alpine3.20 /run/redis.conf
docker run -d --name kerosene-native-bitcoin --restart unless-stopped --network host --user 1000:1000 --read-only --cap-drop ALL --security-opt no-new-privileges --memory 5g --memory-swap 7g --cpus 1 \
  --mount type=bind,src="$BITCOIN_DATA_ROOT",dst=/bitcoin \
  --mount type=bind,src="$BITCOIN_WALLETS_ROOT",dst=/bitcoin-wallets \
  --mount type=volume,src="$BITCOIN_HOME_VOLUME",dst=/home/bitcoin/.bitcoin \
  --mount type=bind,src="$OPS_ROOT/bitcoin.conf",dst=/run/bitcoin.conf,readonly \
  --entrypoint bitcoind bitcoin/bitcoin:28.0 -datadir=/bitcoin -conf=/run/bitcoin.conf -walletdir=/bitcoin-wallets -wallet=kerosene -wallet=kerosene-funds -wallet=kerosene-profit -debug=http
```

`redis.conf` exige autenticação, AOF, limite de memória e `noeviction`.
`bitcoin.conf` fixa testnet3, pruning, RPC autenticado com allow-list restrita,
ZMQ e proxy Tor. Não publicar RPC na interface pública. Em tráfego entre hosts,
proteger também o transporte RPC; a bridge local não substitui TLS de produção.

Para cada LND existente, montar seu próprio diretório e o arquivo de senha
protegido. Usar as portas distintas de `lnd-1.conf` e `lnd-2.conf`:

```bash
docker run -d --name kerosene-native-lnd --restart unless-stopped --network host --user 1000:1000 --read-only --cap-drop ALL --security-opt no-new-privileges --memory 512m --tmpfs /tmp:rw,nosuid,nodev,size=32m --mount type=bind,src="$STATE_ROOT/lnd-data",dst=/lnd --mount type=bind,src="$OPS_ROOT/lnd-1.conf",dst=/run/lnd.conf,readonly --mount type=bind,src="$OPS_ROOT/lnd-wallet-password",dst=/run/wallet-password,readonly --entrypoint lnd lightninglabs/lnd:v0.20.1-beta --configfile=/run/lnd.conf --lnddir=/lnd
docker run -d --name kerosene-native-lnd-peer --restart unless-stopped --network host --user 1000:1000 --read-only --cap-drop ALL --security-opt no-new-privileges --memory 512m --tmpfs /tmp:rw,nosuid,nodev,size=32m --mount type=bind,src="$STATE_ROOT/lnd-peer-data",dst=/lnd --mount type=bind,src="$OPS_ROOT/lnd-2.conf",dst=/run/lnd.conf,readonly --mount type=bind,src="$OPS_ROOT/lnd-wallet-password",dst=/run/wallet-password,readonly --entrypoint lnd lightninglabs/lnd:v0.20.1-beta --configfile=/run/lnd.conf --lnddir=/lnd
```

Usar `wallet-unlock-password-file`, sem `wallet-unlock-allow-create`. As carteiras
persistidas devem pertencer ao UID de execução e ter diretórios 0700 e arquivos
0600. LND operacional não habilita pagamentos Lightning no produto: o perfil
atual mantém esse rail desativado enquanto sua custódia não for distribuída.

## 5. Nodes, Vaults e Tor

Cada membro usa somente seu diretório de estado, seus certificados e seu Tor.
Não iniciar dois processos sobre o mesmo diretório. Os arquivos `node-N.env` e
`vault-N.env` contêm a configuração revisada de cada membro, incluindo rede,
membership e caminhos mTLS. Usar a imagem efetivamente construída para cada
membro; nesta recuperação Node/Tor mantêm a tag `native-20261025`, e Vault usa
`native-20261027-probe`, que corrige bloqueio e timeout dos probes de saúde.

```bash
export MEMBER=1
export CELL_IMAGE_TAG=native-20261025
export VAULT_IMAGE_TAG=native-20261027-probe
docker run -d --name "kerosene-native-node-$MEMBER" --restart unless-stopped --network host --user 1000:1000 --read-only --cap-drop ALL --security-opt no-new-privileges --memory 384m --env-file "$OPS_ROOT/node-$MEMBER.env" --mount type=bind,src="$CELLS_ROOT/node/member-$MEMBER",dst="$CELLS_ROOT/node/member-$MEMBER" --mount type=bind,src="$CELLS_ROOT/node/genesis-trust.json",dst="$CELLS_ROOT/node/genesis-trust.json",readonly --mount type=bind,src="$CELLS_ROOT/ceremony-certs/nodes/vault-$MEMBER",dst="$CELLS_ROOT/ceremony-certs/nodes/vault-$MEMBER",readonly --mount type=bind,src="$CELLS_ROOT/ceremony-certs/ca.crt",dst="$CELLS_ROOT/ceremony-certs/ca.crt",readonly "kerosene/node:$CELL_IMAGE_TAG"
docker run -d --name "kerosene-native-vault-$MEMBER" --restart unless-stopped --network host --user 1000:1000 --read-only --cap-drop ALL --security-opt no-new-privileges --memory 512m --tmpfs /tmp:rw,nosuid,nodev,size=16m --env-file "$OPS_ROOT/vault-$MEMBER.env" --mount type=bind,src="$CELLS_ROOT/vault-$MEMBER-data-v2",dst="$CELLS_ROOT/vault-$MEMBER-data-v2" --mount type=bind,src="$CELLS_ROOT/ceremony-certs/nodes/vault-$MEMBER",dst="$CELLS_ROOT/ceremony-certs/nodes/vault-$MEMBER",readonly --mount type=bind,src="$CELLS_ROOT/ceremony-certs/ca.crt",dst="$CELLS_ROOT/ceremony-certs/ca.crt",readonly --mount type=bind,src="$CELLS_ROOT/ceremony-certs/audit/allowlist.txt",dst="$CELLS_ROOT/ceremony-certs/audit/allowlist.txt",readonly "kerosene/vault:$VAULT_IMAGE_TAG"
docker run -d --name "kerosene-native-tor-$MEMBER" --restart unless-stopped --network host --user 1000:1000 --read-only --cap-drop ALL --security-opt no-new-privileges --memory 192m --mount type=bind,src="$OPS_ROOT/tor-empty.conf",dst=/run/torrc,readonly --mount type=bind,src="$CELLS_ROOT/tor-$MEMBER",dst="$CELLS_ROOT/tor-$MEMBER" --mount type=bind,src="$CELLS_ROOT/onion-$MEMBER",dst="$CELLS_ROOT/onion-$MEMBER" --entrypoint /usr/bin/tor "kerosene/tor:$CELL_IMAGE_TAG" -f /run/torrc --DataDirectory "$CELLS_ROOT/tor-$MEMBER" --SocksPort "127.0.0.1:1905$MEMBER" --ControlPort 0 --HiddenServiceDir "$CELLS_ROOT/onion-$MEMBER" --HiddenServiceVersion 3 --HiddenServicePort "7701 127.0.0.1:770$MEMBER" --HiddenServicePort "8800 127.0.0.1:880$MEMBER" --Log 'notice stdout'
```

Repetir manualmente com `MEMBER=2` e `MEMBER=3`. No Tor 1 desta instalação,
adicionar também `--SocksPort 172.18.0.1:9050`, com `SocksPolicy` permitindo
somente a bridge kind e loopback. As aplicações Kubernetes usam esse endpoint.

O KFE publica checkpoints com uma chave Ed25519 exclusiva. Montar a chave privada
somente no KFE e fixar a chave pública raw de 32 bytes em
`KEROSENE_KFE_LEDGER_PUBLIC_KEY` dos Nodes. Não reutilizar chaves de custódia.

Certificados devem manter SANs onion, finalidade de uso e identidade SPIFFE
autorizada. Na recuperação local, os certificados expirados foram renovados
com as mesmas chaves e CA, por 24 horas. Conferir expiração e implementar emissão
e renovação operacional contínua antes de qualificar produção durável.

## 6. Bancos, Secrets e release

Auth usa `kerosene_auth` com `kerosene_auth_app`; KFE usa `kerosene_kfe` com
`kerosene_kfe_app`. Nenhuma dessas roles é superuser. O banco original foi
preservado. A cópia foi conferida por contagem e digest de cada tabela.

O Auth executa Flyway nos schemas `auth,public`, com default `auth`. O KFE mantém
seu histórico em `public`. Ambos usam `validate`, sem Hibernate criar tabelas,
sem baseline automático e sem ignorar checksums. Na recuperação foi aplicado
um baseline explícito V15 somente na cópia do banco Auth, após conferência das
migrações existentes; não repetir esse baseline em banco novo ou desconhecido.

```bash
docker run --rm --network kind --env-file "$OPS_ROOT/auth-flyway.env" --mount type=bind,src="$WORKSPACE/users-authentication/auth-service/src/main/resources/db/migration/auth",dst=/flyway/sql,readonly flyway/flyway:11.7.2 validate
docker run --rm --network kind --env-file "$OPS_ROOT/kfe-flyway.env" --mount type=bind,src="$WORKSPACE/krinse-engine/src/main/resources/db/migration",dst=/flyway/sql,readonly flyway/flyway:11.7.2 validate
```

Revisar migrações pendentes antes de autorizar `migrate`. O callback do KFE
recusa executar os resets históricos V14, V23 e V27 se aparecerem como pendentes.
O Flyway CLI não carrega esse callback Java; portanto não usar `migrate` no CLI
sobre banco novo sem revisar o histórico completo. Rollback de imagem não desfaz
migrações de banco.

Preservar JWT, AES, pepper e chave de criptografia das colunas ao recuperar dados.
Não gerar substitutos para fazê-los aceitar a inicialização. A chave de cotação
e a chave de checkpoint têm finalidades independentes.

`native-application-secrets.yaml`, `native-rail-secrets.yaml` e
`native-release-manifest-secret.yaml` são manifestos privados fora do checkout.
O último contém manifesto público, assinatura Ed25519 e chave pública; a chave
de assinatura permanece exclusivamente no plano de release. O manifesto associa
versão, digest de imagem, origem e hashes à configuração revisada.

```bash
kubectl apply -f "$OPS_ROOT/native-application-secrets.yaml"
kubectl apply -f "$OPS_ROOT/native-release-manifest-secret.yaml"
kubectl -n kerosene-staging create secret generic kfe-ledger-checkpoint --from-file=signing-key.pem="$OPS_ROOT/kfe-ledger-checkpoint.key"
```

## 7. Aplicações e rails Kubernetes

Antes de aplicar, revisar IPs em `dependencies/services.yaml` e
`native-network-policy.yaml`; verificar o IP atual do PostgreSQL na rede kind.
As políticas liberam somente DNS, comunicação mTLS e portas específicas das
dependências. A instalação atual aplica NetworkPolicies efetivamente.

```bash
kind load docker-image "kerosene/server:$RELEASE" "kerosene/kfe-service:$RELEASE" "kerosene/web-page:$RELEASE" --name kerosene-local
docker exec kerosene-local-control-plane ctr -n k8s.io images ls
```

No kind, registrar também o nome `docker.io/kerosene/COMPONENTE@sha256:DIGEST`
no containerd a partir da tag carregada, com `ctr -n k8s.io images tag`. O digest
é o exibido por `ctr images ls`. Atualizar `images` no overlay e o manifesto
assinado juntos. Não retirar a exigência de digest do admission.

```bash
kubectl apply --dry-run=server -k "$DEPLOY/infra/kubernetes/overlays/native-local/dependencies"
kubectl apply -k "$DEPLOY/infra/kubernetes/overlays/native-local/dependencies"
kubectl --as=system:serviceaccount:kerosene-gitops:kerosene-deployer apply --dry-run=server -k "$DEPLOY/infra/kubernetes/overlays/native-local"
kubectl --as=system:serviceaccount:kerosene-gitops:kerosene-deployer apply --server-side --field-manager=kerosene-gitops -k "$DEPLOY/infra/kubernetes/overlays/native-local"
```

`kfe-health` é um serviço interno de diagnóstico que publica os Pods antes da
prontidão financeira. Seu único porto é mTLS 8443; as rotas internas continuam
autenticadas por SPIFFE. O serviço normal da aplicação permanece condicionado
à prontidão. Isso permite diagnosticar configuração e quorum sem liberar o
tráfego de produto para um KFE ainda não pronto.

As três células de rails usam PVs locais com `Retain`. Nesta recuperação, seus
seis SQLite foram copiados do volume antigo, preservado, para
`/var/lib/kerosene-native/rail-cell-N` no nó. Esses diretórios devem existir com
UID/GID 10001 antes de criar o StatefulSet. Não substituir por diretórios vazios:
isso descartaria histórico de idempotência.

```bash
kubectl create namespace kerosene-local --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$OPS_ROOT/native-rail-secrets.yaml"
kubectl apply --dry-run=server -k "$DEPLOY/infra/kubernetes/overlays/native-local/rails"
kubectl apply -k "$DEPLOY/infra/kubernetes/overlays/native-local/rails"
kubectl -n kerosene-local get pvc,pods
```

## 8. Cerimônia FROST supervisionada

Executar somente em instalação nova, com participantes aprovados, backups e
certificados de operador autorizados. O arranque do servidor não autoriza gerar
novos shares sobre uma custódia existente. Inspecionar primeiro os status de
DKG, keysets USERS/CHANNELS, genesis, threshold 2-de-3 e hashes da constituição.

O protocolo atual oferece `/v1/dkg/*`, `/v1/dkg/tr/*` para Taproot USERS e
`/v1/dkg/tr/channels/*` para CHANNELS. Não reutilizar uma sessão entre domínios.
Os operadores executam cada rodada nos três membros. Exemplo para USERS:

```bash
export VAULT_URL=https://SEU_VAULT_AUTORIZADO.onion:7701
export SESSION_ID=IDENTIFICADOR_UNICO_APROVADO_USERS
curl --fail-with-body --socks5-hostname 127.0.0.1:19051 --cert "$OPS_ROOT/operator.crt" --key "$OPS_ROOT/operator.key" --cacert "$OPS_ROOT/ca.crt" "$VAULT_URL/v1/dkg/tr/status?session_id=$SESSION_ID"
curl --fail-with-body --socks5-hostname 127.0.0.1:19051 --cert "$OPS_ROOT/operator.crt" --key "$OPS_ROOT/operator.key" --cacert "$OPS_ROOT/ca.crt" -H 'Content-Type: application/json' --data-binary @"$OPS_ROOT/users-round1.json" "$VAULT_URL/v1/dkg/tr/round1"
```

O arquivo da rodada 1 contém `session_id`, roster aprovado, `max_signers: 3`,
`min_signers: 2` e `fanout: true`. Conferir os pacotes e transcripts recebidos em
todos os participantes antes da rodada seguinte.

```bash
curl --fail-with-body --socks5-hostname 127.0.0.1:19051 --cert "$OPS_ROOT/operator.crt" --key "$OPS_ROOT/operator.key" --cacert "$OPS_ROOT/ca.crt" -H 'Content-Type: application/json' --data-binary @"$OPS_ROOT/users-round2.json" "$VAULT_URL/v1/dkg/tr/round2"
curl --fail-with-body --socks5-hostname 127.0.0.1:19051 --cert "$OPS_ROOT/operator.crt" --key "$OPS_ROOT/operator.key" --cacert "$OPS_ROOT/ca.crt" -H 'Content-Type: application/json' --data-binary @"$OPS_ROOT/users-round3.json" "$VAULT_URL/v1/dkg/tr/round3"
```

Na rodada 2 usar a mesma sessão e `deliver: true`, `fanout: true`. Na rodada 3,
`finalize: true`. Conferir o status entre rodadas e a conclusão nos três membros.
Repetir CHANNELS com sessão independente. Conferir separação efetiva dos keysets,
day quorum, endereço Taproot e capacidade de recuperação antes da ativação
operacional supervisionada. Nenhum sucesso HTTP isolado comprova essa aceitação.

## 9. Entrada web e comprovação

O NodePort do web é 30080. Nesta máquina o proxy Docker escuta apenas em
`127.0.0.1:30080`, encaminhando para `172.18.0.2:30080`. Nginx é iniciado
diretamente com `--entrypoint nginx`, configuração privada e temporários em
tmpfs, sem entrypoint shell. O Tor web é iniciado com `--entrypoint /usr/bin/tor`,
montando a identidade existente e encaminhando porta onion 80 ao mesmo NodePort.

```bash
docker run -d --name kerosene-native-web-access --restart unless-stopped --network host --user 101:101 --read-only --cap-drop ALL --security-opt no-new-privileges --memory 64m --tmpfs /tmp:rw,nosuid,nodev,size=16m,uid=101,gid=101 --mount type=bind,src="$OPS_ROOT/local-web-proxy.conf",dst=/etc/nginx/nginx.conf,readonly --entrypoint nginx nginx:1.27-alpine -g 'daemon off;'
docker run -d --name kerosene-native-web-tor --restart unless-stopped --network host --user 1000:1000 --read-only --cap-drop ALL --security-opt no-new-privileges --memory 192m --mount type=bind,src="$OPS_ROOT/web-tor.conf",dst=/run/torrc,readonly --mount type=bind,src="$STATE_ROOT/native-web-tor",dst=/data --mount type=bind,src="$STATE_ROOT/tor/keys/local-full",dst=/service --entrypoint /usr/bin/tor "kerosene/tor:$CELL_IMAGE_TAG" -f /run/torrc
```

O Nginx usa `pid` e todos os diretórios temporários em `/tmp`; o upstream é
`172.18.0.2:30080`. O `web-tor.conf` usa `DataDirectory /data`, `SocksPort 0`,
`HiddenServiceDir /service` e `HiddenServicePort 80 172.18.0.2:30080`.
O web Kubernetes também inicia `nginx` diretamente, sem entrypoint shell.

```bash
curl --fail --noproxy '*' http://localhost:30080/ -o /dev/null
kubectl -n kerosene-staging get pods,services
kubectl -n kerosene-local get pods,pvc
kubectl -n kerosene-staging rollout status deployment/server --timeout=60s
kubectl -n kerosene-staging rollout status deployment/kfe-service --timeout=60s
docker ps --format '{{.Names}} {{.Status}}'
```

Aceitar a stack completa somente com:

- Auth e KFE inicializados, migrações válidas e comunicação mTLS verificada;
- Nodes com quorum e checkpoint KFE autenticado, fresco e sem blockers;
- Vaults com `local_ready` e `financial_ready`, keysets separados e day quorum;
- Bitcoin em testnet3 com `initialblockdownload=false`; LND desbloqueado e
  sincronizado, sem confundir isso com habilitação do rail Lightning no produto;
- políticas de rede verificadas com conexões permitidas e recusadas;
- frontend acessível e fluxos autenticados reais conferidos;
- backups restauráveis, renovação de certificados e participantes distribuídos
  entre domínios de falha para uma qualificação de produção.

## 10. Recuperação e limites desta execução

Foram recuperados somente arquivos Java/build e migrações ausentes do KFE a
partir da revisão `47d1dcd0eb82e57d8a177c165c5cd3f49f5b960b`, preservando as
alterações locais. Os checksums dos históricos SQL foram conferidos antes de
migrar. O banco original, shares, carteiras e identidades onion foram mantidos.
Os dados antigos de Kubernetes foram lidos de uma cópia offline do etcd; esse
servidor de recuperação não permanece exposto ou ativo.

O Bitcoin original tinha undo data truncado e falhava na verificação do banco de
blocos. Foi criada uma cópia em `/home`, com espaço suficiente, para reindexação
com verificação normal. O original permaneceu como referência. A reindexação
local concluiu, mas a sincronização de rede ainda é uma condição independente.
Em pruning, `-reindex-chainstate` não substitui a reconstrução completa; ver o
[código do Bitcoin Core 28](https://github.com/bitcoin/bitcoin/blob/v28.0/src/init.cpp).
Não reduzir `checklevel` ou aceitar uma cadeia não sincronizada como recuperação.

Durante a execução, os três Nodes receberam checkpoints assinados e o Vault
consultado reportou `financial_ready=true`; os três Pods de rails ficaram 2/2.
O frontend respondeu HTTP 200. A qualificação final de Auth/KFE e da cadeia deve
ser conferida novamente pelos comandos acima; esses resultados não certificam
produção financeira completa.

As chamadas internas KFE→Auth incluem prova HMAC do manifesto de release, método,
caminho, timestamp e corpo, além do mTLS SPIFFE. O teste de integração confirma
a aceitação da prova e a rejeição quando o corpo é alterado.

## 11. Atualizar sem duplicar

Reutilizar `kerosene-local`, os mesmos Deployments/StatefulSets, os nomes
`kerosene-native-*` e a mesma tag local `native-20261025`. Fazer o novo build
sobre essa tag, carregar no cluster existente, atualizar o digest OCI no overlay
e no manifesto assinado e aplicar os mesmos recursos. Os Deployments locais
usam `Recreate` e não retêm ReplicaSets antigos.

Para trocar imagem ou parâmetros de um contêiner Docker, parar com timeout
adequado, remover somente esse contêiner sem `-v` e recriar com o mesmo nome,
montagens e dados persistidos. `docker restart` não troca a imagem. Não criar
sufixos `fix`, `new`, `backup` ou outro cluster para cada tentativa. Depois da
validação, retirar somente as imagens anteriores desses serviços que não estejam
em uso; nunca executar limpeza global no Docker ou no containerd do kind.

A anotação `kerosene.io/release-digest` nos templates de Auth/KFE deve acompanhar
o SHA-256 do manifesto assinado. Ela recria os clientes internos quando o
manifesto muda, mantendo coerentes as provas HMAC, mesmo quando apenas uma
imagem é atualizada.

### Conferência final e pendências observadas

As rotas `/health/ready` e `/kfe/health/ready`, pelo proxy web, responderam HTTP
200 com estado UP. O onion web também respondeu HTTP 200. Os três Nodes
reportaram quorum e checkpoint financeiro frescos. Houve leituras do Vault com
`financial_ready=true` e outras com `degraded`/`financial_ready=false`; as
sondagens de peers usam Tor e limitam cada tentativa a cinco segundos no código
atual. Essa oscilação exige acompanhamento e não deve ser mascarada com contagem
estática de membros. Revalidar todos os Vaults antes de operações financeiras.

O Bitcoin permanece em initial block download, apesar de estar conectado e
recebendo headers. Os LNDs aguardam o backend sincronizar. Esse estado impede
qualificar a plataforma inteira como pronta para operação financeira.

O cliente Python 3.13, no modo X.509 estrito padrão, recusou a CA local por falta
da extensão KeyUsage. As verificações nativas com curl, Java e Rust aceitam a
cadeia existente sem desabilitar TLS. Revisar o perfil da CA para clientes
estritos na implantação de produção; não usar `--insecure` nem desligar a
validação para ocultar esse resultado.

Na última aplicação, Auth, KFE e web ficaram 1/1 Ready; as três células de rails
ficaram 2/2. Auth registrou `STARTUP_READY status=OK`, e as duas rotas de
prontidão pelo web responderam 200. Foi mantido somente o cluster
`kerosene-local`, com os mesmos nomes de recursos e uma tag local por serviço;
os oito contêineres intermediários e os ReplicaSets antigos desta execução
foram removidos sem apagar dados persistidos.
