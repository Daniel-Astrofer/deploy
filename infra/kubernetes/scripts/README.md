# Kubernetes Scripts

O conteúdo normativo deste documento foi consolidado no repositório externo [kerosene-global-docs](../../../../../kerosene-global-docs/operations/deploy/infra/kubernetes/scripts/README.md).

Use a interface pública:

```bash
bash infra/start.sh
```

Helpers principais:

```text
validate-staging-spire.sh Valida a base SPIRE e recusa promoção para produção.
install-staging-spire.sh Instala bootstrap, allow-list, admissão, registros e SPIRE na ordem segura.
preflight-staging-spire.sh Confirma admissão/RBAC, SPIRE/CSI e reconciliação antes do rollout.
../tests/staging-spire-admission-kind-test.sh Prova bloqueios de impersonação em Kind efêmero.
```

O profile local `KEROSENE_VAULT_MESH_PROFILE=staging` ainda possui fallback
legado para a mesh lab quando a geração de certificados falha. Esse fallback é
exclusivamente local e não serve como evidência de staging ou produção.
