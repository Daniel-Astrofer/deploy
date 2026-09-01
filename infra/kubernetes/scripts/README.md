# Kubernetes Quorum Helpers

Esta pasta contém helpers internos do quorum Kubernetes local.

Use a interface pública:

```bash
bash infra/start.sh
bash infra/stop.sh
bash infra/recreate.sh
bash infra/status.sh
bash infra/logs.sh
bash infra/test.sh
```

Helpers principais:

```text
apply.sh              Aplica o local-full e opcionalmente aguarda readiness.
wait.sh               Aguarda workloads do quorum.
status.sh             Mostra recursos, serviços e URLs.
logs.sh               Mostra logs por serviço.
validate-local-full.sh Renderiza e valida o overlay local-full.
validate-staging-spire.sh Valida a base SPIRE e recusa promoção para produção.
preflight-staging-spire.sh Confirma SPIRE/CSI/reconciliação antes do rollout.
```

`bash infra/logs.sh --follow` acompanha o quorum completo em tempo real,
gravando arquivos separados em `infra/runtime/logs/kubernetes/<timestamp>/`.

Scripts antigos nesta pasta são compatibilidade temporária ou diagnósticos
especializados. Novos scripts públicos não devem ser adicionados aqui.

O inventário e a classificação canônica estão em
`docs/pt-BR/INDICE-DEPLOY.md` e `docs/en/DEPLOYMENT_INDEX.md`.

O profile local `KEROSENE_VAULT_MESH_PROFILE=staging` ainda possui fallback
legado para a mesh lab quando a geração de certificados falha. Esse fallback é
exclusivamente local e não serve como evidência de staging ou produção.
