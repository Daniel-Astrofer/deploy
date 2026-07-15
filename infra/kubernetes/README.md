# Kerosene Kubernetes

Esta é a raiz canônica para Kubernetes/Kustomize da Kerosene.

## Estado atual

A árvore Kubernetes canônica fica em:

```text
infra/kubernetes
```

Kubernetes aqui representa o quorum local integrado. A interface pública normal
fica em `infra/*.sh`; scripts dentro de `infra/kubernetes/scripts/` são helpers
internos para aplicar, aguardar, diagnosticar e validar o quorum.

## Entrada única local-full

Para iniciar a aplicação inteira no Kubernetes local, use:

```bash
bash infra/start.sh
bash infra/status.sh
```

`infra/start.sh` valida `overlays/local-full`, constrói/importa imagens locais,
aplica os manifests, aguarda os workloads e mostra o onion local. Os Services
do `local-full` ficam `ClusterIP`; o deploy rejeita `NodePort` e
`LoadBalancer` para não disputar portas do host. O gateway público é o hidden
service `tor-onion`, com a porta onion `80` encaminhada para `web-page:8080`.

Antes do deploy, o start tenta iniciar `containerd.service`, `docker.service` e
`kubelet.service` quando estiver em um host systemd. Desative com:

```bash
KEROSENE_AUTO_START_HOST_SERVICES=0 bash infra/start.sh
```

Se o import para containerd não puder usar `sudo ctr`, o deploy continua com as
imagens já presentes no cluster. Use `--strict-image-import` para tornar essa
falha bloqueante.

Em um terminal interativo, o importador pede `sudo` quando precisar acessar o
containerd. Depois de uma importação bem-sucedida, o deploy grava o ID local da
imagem em `kerosene.io/local-image-id` no template dos pods. Isso força rollout
quando a imagem muda e mantém os workloads parados quando a imagem continua a
mesma.

O onion fica estável enquanto as chaves persistidas em
`$HOME/.local/state/kerosene/tor/keys/local-full` (ou
`KEROSENE_LOCAL_ONION_KEYS_PATH`) forem preservadas. Paths de hostPath do
overlay local-full são reescritos automaticamente a partir de `$HOME` e da
raiz do repositório.

## Helpers internos

Aplicar o quorum Kubernetes local:

```bash
bash infra/kubernetes/scripts/apply.sh --wait
```

Aguardar workloads:

```bash
bash infra/kubernetes/scripts/wait.sh
```

Status e logs:

```bash
bash infra/kubernetes/scripts/status.sh
bash infra/kubernetes/scripts/logs.sh server
bash infra/logs.sh --follow
```

Validação local-full sem aplicar:

```bash
bash infra/start.sh --dry-run
```

Compatibilidade legada:

```bash
bash infra/deploy.sh
bash infra/kubernetes/deploy.sh
```

## Layout

```text
infra/kubernetes/
  base/
  overlays/
  components/
  scripts/
  tests/
  docs/
  examples/
```

## Regra operacional

Kubernetes não deve construir imagem. Kustomize deve apenas referenciar imagens definidas pelo contrato de `infra/docker/images.yaml` e receber tags/digests por overlay ou pipeline.
