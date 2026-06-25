# Kerosene Kubernetes

Esta é a futura raiz canônica para Kubernetes/Kustomize da Kerosene.

## Estado atual

A árvore Kubernetes canônica já foi transferida para:

```text
infra/kubernetes
```

Os manifests legados em `backend/kerosene-infrastructure/k8s` continuam
presentes para compatibilidade durante a transição.

## Comandos atuais

Render de produção:

```bash
kubectl kustomize infra/kubernetes/overlays/production
```

Deploy local:

```bash
infra/kubernetes/deploy.sh local
```

Deploy local-ha:

```bash
infra/kubernetes/scripts/deploy-local-ha.sh
```

Validação local-ha:

```bash
infra/kubernetes/scripts/validate-local-ha.sh
```

## Layout

```text
infra/kubernetes/
  base/
  overlays/
  components/
  scripts/
  docs/
  examples/
```

## Regra operacional

Kubernetes não deve construir imagem. Kustomize deve apenas referenciar imagens definidas pelo contrato de `infra/docker/images.yaml` e receber tags/digests por overlay ou pipeline.
