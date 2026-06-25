# Kerosene Runtime Assets

Este diretório é o destino dos arquivos de runtime que não são código de aplicação, Dockerfile ou Kustomize puro.

## Destinos

```text
infra/runtime/bitcoin/
infra/runtime/vault/
infra/runtime/tor/
infra/runtime/web/
infra/runtime/postgres/
infra/runtime/observability/
```

## Regra

Não transferir certificados reais, chaves privadas, secrets, onion keys ou material sensível sem plano explícito de rotação e limpeza de histórico.
