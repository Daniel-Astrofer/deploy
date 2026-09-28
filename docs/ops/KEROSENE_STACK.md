# Kerosene Stack: Cell release lock e atualização segura

`kerosene-stack` é o ponto de partida do controlador operacional da **Kerosene
Cell completa**, não de um contêiner isolado. O lock exige Admin, Core, KFE,
Node, Vault, web-page, PostgreSQL, Redis, Bitcoin, LND e Tor; também prende os
commits dos repositórios `admin`, `clients`, `contracts`, `core`, `deploy`,
`kfe`, `node`, `rails`, `shared` e `vault`.

O corte atual valida a estrutura do lock, os digests imutáveis, a prova TUF,
o recibo BFT, o relatório assinado dos observadores Bank e o recibo de snapshot.
Com essas provas e confirmação explícita do operador, `--apply` executa o
adaptador real da célula de staging: primeiro `staging-vault`, depois `staging`,
usando os overlays Kubernetes já existentes. Produção continua bloqueada no
repositório público e deve usar o adaptador privado de operações.

## O que é fonte da verdade

O `release-lock` não contém segredos, bancos, chaves, macaroons ou shares. Ele
liga três coisas diferentes, que não devem ser confundidas:

1. O bundle de fonte Git somente leitura, endereçado por digest OCI, e os
   commits exatos de cada repositório.
2. Artefatos executáveis e configurações, todos fixados por `sha256`; nenhum
   serviço pode usar `latest` ou ser reconstruído no host durante a atualização.
3. A decisão de distribuição: o alvo TUF, o recibo de commit BFT e a
   compatibilidade do quorum Vault. A prova TUF e as assinaturas BFT/Bank são
   verificadas localmente; a compatibilidade Vault continua sendo uma
   declaração de entrada que não ordena releases.

O schema público está em
[`infra/stack/release-lock.schema.json`](../../infra/stack/release-lock.schema.json).
Os contratos de prova estão em `infra/stack/*-receipt.schema.json`,
`infra/stack/release-roster.schema.json` e
`infra/stack/bank-observer-report.schema.json`.
O exemplo é sintético e não é um release utilizável:
[`infra/stack/examples/release-lock.example.json`](../../infra/stack/examples/release-lock.example.json).

## Uso atual

No checkout de Deploy, exponha `infra/` no `PATH` ou invoque o arquivo
diretamente:

```bash
PATH="$PWD/infra:$PATH" kerosene-stack verify-release \
  --release infra/stack/examples/release-lock.example.json

PATH="$PWD/infra:$PATH" kerosene-stack update \
  --release infra/stack/examples/release-lock.example.json \
  --output /tmp/kerosene-update-plan.json
```

Sem `--apply`, `update` gera apenas um plano de alteração zero. O plano impõe a
sequência: observar o plano Bank, verificar autorização, aceitar o snapshot,
atualizar fundação, Vault, Node e aplicações e, por último, validar/registrar o
resultado. O Admin e o Node são passos obrigatórios, não apêndices opcionais.

Para uma execução real, o operador precisa fornecer as provas produzidas pela
governança, pelos servidores Bank e pelo provedor de snapshots:

```bash
kerosene-stack update \
  --release release-lock.json \
  --apply \
  --environment staging-cell \
  --confirm-release bank-mainnet-2026.09.28.1 \
  --tuf-proof tuf-target-proof.json \
  --tuf-root-key tuf-root-key.b64 \
  --bft-receipt release-receipt.json \
  --validator-roster release-roster.json \
  --bank-observer-report bank-observer-report.json \
  --snapshot-receipt snapshot-receipt.json \
  --state-dir /var/lib/kerosene-stack
```

O comando grava `update-state.json` com as fases `verified`,
`snapshot-accepted`, `rollout-started` e `validate-and-commit`. O adaptador
renderiza os manifests, confirma que cada digest recebido chegou a um workload,
aplica os dois overlays e aguarda os rollouts e smoke gates existentes. Se o
adaptador falhar, o estado fica como `failed` e exige recuperação manual; não há
rollback automático de PostgreSQL, Bitcoin, LND ou Vault.

Antes da mudança, use `--dry-run` com os mesmos documentos para renderizar e
validar os dois overlays sem modificar recursos Kubernetes. Sem prova TUF,
recibo BFT, relatório Bank, snapshot ou confirmação exata do release, `--apply`
falha com código `78`.

## Invariantes já codificados

- Todas as imagens e o bundle de fonte são endereçados por digest.
- Um release não pode omitir Admin, Node ou qualquer outro componente da Cell.
- O Bank exige pelo menos um quorum BFT de `3/4` (ou maior para memberships
  maiores) e o mínimo de observadores não pode ficar abaixo desse limiar.
- Compatibilidade Vault exige no mínimo `2/3`, mas não é usada como ledger de
  ordenação global de releases.
- A atualização exige snapshot; uma migração é `reversible` ou traz evidência
  de recuperação aprovada.
- O lock rejeita campos que pareçam carregar material secreto, e não aceita
  campos desconhecidos silenciosamente.
- `allowSourceBuild` e `vaultSignerActivation` devem ser `false`.

## Próximos cortes

1. Formalizar este contrato no repositório `contracts` e gerar clientes para
   Core, Admin e Bank observers.
2. Substituir a prova TUF mínima por metadados TUF completos, com delegação de
   root/targets, rotação de root e verificação de provenance Cosign/SLSA.
3. Ligar o recibo de snapshot a um provedor real de VolumeSnapshot/restic e
   exigir restauração verificada antes de aceitar `snapshot-accepted`.
4. Fazer os servidores Bank publicarem as observações diretamente por mTLS,
   em vez de depender apenas de arquivos transportados pelo operador.
5. Adicionar smoke gates financeiros e recuperação automatizada por componente,
   mantendo rollback manual para migrações irreversíveis.
