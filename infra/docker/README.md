# Kerosene Docker

`infra/docker/images.yaml` é o inventário canônico de empacotamento local das
imagens da Kerosene. Releases e deploys de staging/produção consomem referências
de imagens imutáveis; não usam este repositório como fonte dos serviços.

Ele define nome da imagem, tag local, Dockerfile e contexto de build para os workloads principais: `server`, `kfe-service`, `kerosene-vault`, `tor` e `web-page`.

## Estado atual

Os Dockerfiles e Compose files suportados vivem em `infra/docker`. Use os helpers
de Compose em `infra/docker/scripts`:

```bash
bash infra/docker/scripts/compose-local.sh ps
bash infra/docker/scripts/compose-local-kfe.sh ps
```

`compose-local-kfe.sh` é um alias de compatibilidade para a composição Vault
Mesh; ele não inicia o KFE e não deve ser adotado por automações novas.

Arquivos Compose canônicos:

```text
infra/docker/compose/vault-mesh-lab.compose.yaml
infra/docker/compose/vault-mesh-staging.compose.yaml
infra/docker/compose/vault-mesh-tor.compose.yaml
infra/docker/compose/vault-mesh-ceremony.compose.yaml
infra/docker/compose/local.limits.compose.yaml  # legacy override; not standalone
```

`hardened` e overlays de produção ficam fora do repositório público.

## Layout

```text
infra/docker/
  healthcheck/Healthcheck.java
  images/
    server/Dockerfile
    kfe-service/Dockerfile
    kerosene-vault/Dockerfile
    tor/Dockerfile
    web-page/Dockerfile
  compose/
    vault-mesh-*.compose.yaml
    local.limits.compose.yaml
  images.yaml
```

## Regra operacional

Scripts novos não devem hardcodar caminhos de Dockerfile. Eles devem ler ou seguir o contrato de `images.yaml`.

Os contextos `core`, `kfe`, `shared`, `contracts`, `admin`, `clients`, `vault` e `node` apontam
para checkouts externos. Builds de Core recebem os contextos nomeados
`contracts` e `deploy` via BuildKit; por isso não devem restaurar cópias de
Contracts ou do healthcheck dentro do Core.
Somente imagens auxiliares de infraestrutura, como Tor, têm runtime pertencente
ao próprio Deploy.
