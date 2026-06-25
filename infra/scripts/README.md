# Kerosene Infra Scripts

Este diretório documenta a separação futura dos scripts operacionais por domínio.

## Layout alvo

```text
infra/scripts/
  local/       start, stop, status e bootstrap local.
  docker/      build, tag, import e inspeção de imagens.
  kubernetes/  deploy, validate, wait, diagnostics e rollback.
  db/          backup, restore, migration helpers.
  logs/        coleta e streaming de logs.
  release/     snapshot, assinatura, SBOM e checks de release.
```

## Estado atual

Os scripts executáveis ainda ficam majoritariamente em `scripts/` na raiz e em `backend/kerosene-infrastructure/k8s/scripts`.

Já existem wrappers locais em:

```bash
bash infra/scripts/local/start.sh
bash infra/scripts/local/stop.sh
bash infra/scripts/local/state.sh
bash infra/scripts/local/logs.sh
```

Os comandos legados continuam válidos:

```bash
scripts/start-local.sh
scripts/stop-local.sh
scripts/status-local.sh
scripts/logs-local.sh
```

## Regra operacional

Scripts novos devem declarar claramente seu domínio e evitar misturar Docker, Kubernetes, banco, logs e release no mesmo arquivo, salvo scripts orquestradores de alto nível.
