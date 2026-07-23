# Kerosene Local Quorum Infrastructure

`infra/` é o laboratório local/integrado para subir o quorum completo da
Kerosene e verificar se os serviços principais conseguem operar juntos.

Ele não é a infraestrutura final de produção de cada serviço. Cada aplicação
deve caminhar para ter sua própria imagem e seu próprio Kubernetes; `infra/`
apenas agrega tudo para teste local do conjunto.

## Interface pública

Use somente estes comandos no fluxo normal:

```bash
bash infra/start.sh
bash infra/stop.sh
bash infra/recreate.sh
bash infra/status.sh
bash infra/logs.sh
bash infra/test.sh
```

`infra/start.sh` é o caminho principal do quorum local. Ele usa Kubernetes
local-full, sobe o **vault mesh** (`vault-mesh-lab.compose.yaml`, testnet3),
constrói/importa imagens locais quando possível, aplica o overlay, aguarda
readiness e mostra as URLs finais. Signing legado (`mpc-sidecar` / HashiCorp
wallet-arming) **não** faz parte deste caminho.

Antes de falar com o Kubernetes, `infra/start.sh` tenta iniciar os serviços host
`containerd.service`, `docker.service` e `kubelet.service` quando o host usa
systemd e eles existem. Para desativar isso:

```bash
KEROSENE_AUTO_START_HOST_SERVICES=0 bash infra/start.sh
```

Atalhos antigos como `infra/deploy.sh` e scripts dentro de
`infra/kubernetes/scripts/` ficam apenas como compatibilidade ou helpers
internos. Eles não são a API normal para humanos ou agentes.

## Layout

```text
infra/
  start.sh      Sobe o quorum local completo.
  stop.sh       Para workloads do quorum preservando dados locais.
  recreate.sh   Para e sobe novamente o quorum local.
  status.sh     Mostra estado, serviços e URLs do quorum.
  logs.sh       Mostra logs gerais ou por serviço.
  test.sh       Valida scripts e manifests do quorum local.

  docker/       Contrato de imagens, Dockerfiles e Compose legado/auxiliar.
  kubernetes/   Manifests e helpers internos do quorum Kubernetes local.
  runtime/      Configurações locais de Bitcoin, Vault, Tor, Web/Nginx, Postgres etc.
  scripts/      Helpers internos chamados pelos entrypoints públicos.
  mcp/          Ferramentas MCP/agentes, fora da árvore Kubernetes.
```

## Kubernetes local

O quorum local oficial usa Kubernetes `local-full`. Os Services ficam
`ClusterIP` e o deploy rejeita `NodePort`/`LoadBalancer`, evitando competir por
portas já usadas no host. A entrada pública do `local-full` é somente o onion
publicado pelo `tor-onion`, que encaminha a porta onion `80` para o gateway
interno `web-page:8080`.

Use o status para ver o endereço atual:

```bash
bash infra/status.sh
```

`3000` permanece legado/Compose quando esse runtime separado estiver rodando em
paralelo; ele não faz parte do contrato Kubernetes `local-full`.

O onion local-full usa chaves persistentes em:

```text
$KEROSENE_HOST_HOME/.local/state/kerosene/tor/keys/local-full
```

Por padrão `KEROSENE_HOST_HOME` é `$HOME` do usuário atual. Enquanto esse
diretório for preservado, o endereço `.onion` permanece o mesmo.
`infra/stop.sh` e `infra/recreate.sh` não removem essas chaves.

Se não houver cluster Kubernetes acessível, `infra/deploy.sh` / `infra/start.sh`
tentam criar um cluster `kind` chamado `kerosene-local` (requer Docker).
Desative com `KEROSENE_AUTO_CREATE_CLUSTER=0`.

## Logs

Para acompanhar todos os workloads do quorum completo em tempo real, separados
por arquivo:

```bash
bash infra/logs.sh --follow
```

Esse comando cria um diretório em `infra/runtime/logs/kubernetes/<timestamp>/`
com `server.log`, `kfe-service.log`, `web-page.log`,
`tor-onion.log`, `local-postgres.log`, `local-redis.log`,
`local-bitcoin.log`, `local-lnd.log`, `local-lnd-peer.log` e `index.txt`.
Vault mesh logs: `docker compose -f infra/docker/compose/vault-mesh-lab.compose.yaml logs`.

Para logs de um serviço só:

```bash
bash infra/logs.sh server --follow
bash infra/logs.sh tor-onion --follow
```

## Compose legado

Compose continua em `infra/docker/compose/` e `infra/scripts/local/` como apoio
legado/local específico. O caminho de settlement do deploy é
`vault-mesh-lab.compose.yaml` (não `local.compose.yaml` / mpc-sidecar).

## Regras

- Código de produto fica em `backend/` e `frontend/`.
- Dockerfiles e contratos de imagem ficam sob `infra/docker`.
- Kubernetes em `infra/` representa o quorum local, não a plataforma final.
- Scripts públicos novos devem ser apenas os entrypoints `infra/*.sh` acima.
- Scripts antigos devem virar internos ou wrappers de compatibilidade sem lógica.
- Arquivos sensíveis, certificados, chaves e segredos não devem ser movidos sem plano explícito de rotação.
