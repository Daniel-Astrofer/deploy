# Kerosene Docker

`infra/docker/images.yaml` é o contrato canônico de imagens da Kerosene.

Ele define nome da imagem, tag local, Dockerfile e contexto de build para os workloads principais: `server`, `kfe-service`, `kerosene-vault`, `tor` e `web-page`.

## Estado atual

Os Dockerfiles e Compose files canônicos já vivem em `infra/docker`. Use os wrappers canônicos em `infra/docker/scripts`:

```bash
bash infra/docker/scripts/compose-local.sh ps
bash infra/docker/scripts/compose-local-kfe.sh ps
```

Arquivos Compose canônicos:

```text
infra/docker/compose/vault-mesh-lab.compose.yaml
infra/docker/compose/vault-mesh-staging.compose.yaml
infra/docker/compose/vault-mesh-tor.compose.yaml
infra/docker/compose/vault-mesh-ceremony.compose.yaml
infra/docker/compose/local.limits.compose.yaml
```

`hardened` e overlays de produção ficam fora do repositório público.

## Layout

```text
infra/docker/
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
