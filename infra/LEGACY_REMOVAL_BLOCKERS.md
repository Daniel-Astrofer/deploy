# Legacy removal blockers

A limpeza destrutiva dos diretórios legados deve apagar duplicatas, sem criar área de archive.

## Não apagar automaticamente nesta fase

```text
certificados reais
chaves privadas
onion keys
identidade local
wallet/bootstrap Lightning real
secrets de produção ou ambiente real
```

Motivo: estes itens envolvem material operacional sensível. Eles não devem ser movidos ou removidos automaticamente sem plano de rotação, backup e validação fora do repositório.

## Deve ser apagado após validação final

Duplicatas que já possuem equivalente canônico em:

```text
infra/docker
infra/kubernetes
infra/runtime
infra/scripts
```

Condição: confirmar que nenhum script ativo, teste ativo, Compose, Kustomize ou documentação operacional ainda referencia a duplicata como fonte executável.

## Estado canônico

```text
infra/docker
infra/kubernetes
infra/runtime
infra/scripts
```
