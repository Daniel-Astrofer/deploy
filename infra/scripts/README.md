# Kerosene infrastructure scripts

A camada canônica de operação local fica em `infra/scripts`.

## Entrada única local

Use:

```bash
bash infra/scripts/local/control.sh <command> [options] [compose-service...]
```

Comandos suportados:

```text
init       valida pré-requisitos locais
start      sobe a infraestrutura local
stop       desliga a infraestrutura local
restart    desliga e sobe novamente
recreate   força recriação de serviços/infra
status     mostra estado do Compose ou dashboard
logs       mostra logs do Compose
capture    reinicia a infra local e captura logs de startup
backup-db  cria backup local de Postgres/Redis
migrate-db aplica migrações locais de banco
repair-bitcoin repara runtime local Bitcoin/LND
recreate-mpc recria todos os sidecars MPC locais
```

Exemplos:

```bash
bash infra/scripts/local/control.sh init
bash infra/scripts/local/control.sh start
bash infra/scripts/local/control.sh stop
bash infra/scripts/local/control.sh stop --volumes
bash infra/scripts/local/control.sh recreate server-wvo
bash infra/scripts/local/control.sh recreate --kfe kfe-service-wvo
bash infra/scripts/local/control.sh logs --tail 200 server-wvo
bash infra/scripts/local/control.sh status --dashboard
bash infra/scripts/local/control.sh capture --minutes 10 -- --no-build
bash infra/scripts/local/control.sh backup-db
bash infra/scripts/local/control.sh migrate-db
bash infra/scripts/local/control.sh repair-bitcoin --reindex
bash infra/scripts/local/control.sh recreate-mpc
bash infra/scripts/local/logs-app-is.sh
bash infra/scripts/local/logs-region-ch.sh
bash infra/scripts/local/logs-app-sg.sh
```

## Wrappers canônicos

```text
infra/scripts/local/init.sh
infra/scripts/local/start.sh
infra/scripts/local/stop.sh
infra/scripts/local/restart.sh
infra/scripts/local/recreate.sh
infra/scripts/local/state.sh
infra/scripts/local/logs.sh
```

## Compatibilidade

Os scripts antigos da raiz, como `scripts/start-local.sh`, `scripts/stop-local.sh`, `scripts/status-local.sh`, `scripts/logs-local.sh` e `scripts/recreate-local-mpc-sidecars.sh`, devem ser wrappers curtos para `infra/scripts/local/control.sh`.

A regra arquitetural é: lógica operacional em `infra/scripts`; raiz apenas compatibilidade.
