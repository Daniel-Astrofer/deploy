# Kerosene Docker

`infra/docker/images.yaml` é o contrato canônico de imagens da Kerosene.

Ele define nome da imagem, tag local, Dockerfile e contexto de build para os workloads principais: `server`, `kfe-service`, `mpc-sidecar`, `vault`, `tor` e `web-page`.

## Estado atual

Os Dockerfiles e Compose files canônicos já vivem em `infra/docker`. Os caminhos
legados continuam presentes para compatibilidade enquanto scripts antigos são
migrados gradualmente. Use os wrappers canônicos em `infra/docker/scripts`:

```bash
bash infra/docker/scripts/compose-local.sh ps
bash infra/docker/scripts/compose-local-kfe.sh ps
bash infra/docker/scripts/compose-hardened.sh config
```

Arquivos Compose canônicos:

```text
infra/docker/compose/local.compose.yaml
infra/docker/compose/local.limits.compose.yaml
infra/docker/compose/local.kfe.compose.yaml
infra/docker/compose/hardened.compose.yaml
```

`infra/docker/images.yaml` aponta para os Dockerfiles canônicos e mantém
`legacy_dockerfile` para rastrear a origem antiga.

## Layout

```text
infra/docker/
  images/
    server/Dockerfile
    kfe-service/Dockerfile
    mpc-sidecar/Dockerfile
    vault/Dockerfile
    tor/Dockerfile
    web-page/Dockerfile
  compose/
    local.compose.yaml
    local.limits.compose.yaml
    local.kfe.compose.yaml
    hardened.compose.yaml
  images.yaml
```

## Regra operacional

Scripts novos não devem hardcodar caminhos de Dockerfile. Eles devem ler ou seguir o contrato de `images.yaml`.
