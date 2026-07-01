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
local-full, constrói/importa imagens locais quando possível, aplica o overlay,
aguarda readiness e mostra as URLs finais.

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

O quorum local oficial usa Kubernetes `local-full`. O gateway principal é:

```text
http://127.0.0.1:30082
```

KFE e web passam pelo `web-page` em `30082`; `3000` é legado/Compose quando
estiver rodando em paralelo.

Portas NodePort fixas:

```text
server   30080
mpc      30081
web/KFE  30082
```

O onion local-full usa chaves persistentes em:

```text
/home/omega/.local/state/kerosene/tor/keys/local-full
```

Enquanto esse diretório for preservado, o endereço `.onion` permanece o mesmo.
`infra/stop.sh` e `infra/recreate.sh` não removem essas chaves.

## Logs

Para acompanhar todos os workloads do quorum completo em tempo real, separados
por arquivo:

```bash
bash infra/logs.sh --follow
```

Esse comando cria um diretório em `infra/runtime/logs/kubernetes/<timestamp>/`
com `server.log`, `kfe-service.log`, `web-page.log`, `mpc-sidecar.log`,
`tor-onion.log`, `local-postgres.log`, `local-redis.log`, `local-vault.log`,
`local-bitcoin.log`, `local-lnd-placeholder.log` e `index.txt`.

Para logs de um serviço só:

```bash
bash infra/logs.sh server --follow
bash infra/logs.sh tor-onion --follow
```

## Compose legado

Compose continua em `infra/docker/compose/` e `infra/scripts/local/` como apoio
legado/local específico. Ele não deve competir como caminho principal do quorum.

## Regras

- Código de produto fica em `backend/` e `frontend/`.
- Dockerfiles e contratos de imagem ficam sob `infra/docker`.
- Kubernetes em `infra/` representa o quorum local, não a plataforma final.
- Scripts públicos novos devem ser apenas os entrypoints `infra/*.sh` acima.
- Scripts antigos devem virar internos ou wrappers de compatibilidade sem lógica.
- Arquivos sensíveis, certificados, chaves e segredos não devem ser movidos sem plano explícito de rotação.
