# Documentação do Kerosene Deploy

Este repositório é responsável por executar e operar os serviços da Kerosene.
Ele não contém regras financeiras, código dos serviços nem implementação de
custódia.

Comece por estes documentos:

- [Índice do deploy](INDICE-DEPLOY.md): entradas suportadas e responsabilidade das pastas.
- [Limites dos serviços](LIMITES-DOS-SERVICOS.md): responsabilidades e artefatos consumidos.
- [Fronteira do repositório](../REPOSITORY_BOUNDARY.md): separação entre código e deploy.
- [Procedimento de staging](../ops/STAGING_DEPLOY.md): processo atual e bloqueios.

## Uso local

```bash
bash infra/start.sh
bash infra/status.sh
bash infra/logs.sh --follow
bash infra/stop.sh
```

## Validação

```bash
bash infra/test.sh
bash infra/start.sh --dry-run
bash infra/kubernetes/scripts/validate-staging-runtime.sh
```

Produção usa overlays privados e deve falhar se imagens imutáveis, secrets ou
evidências estiverem ausentes. Os manifests públicos não criam secrets reais e
não ativam signers.

Documentação técnica curta em inglês: [`docs/en/README.md`](../en/README.md).
