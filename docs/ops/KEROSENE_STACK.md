# Kerosene Stack: Cell release lock e atualização segura

`kerosene-stack` é o ponto de partida do controlador operacional da **Kerosene
Cell completa**, não de um contêiner isolado. O lock descreve o artefato Admin
do operador e exige Core, KFE, Node, Vault, web-page, PostgreSQL, Redis, Bitcoin,
LND e Tor; também prende os commits dos repositórios `admin`, `clients`,
`contracts`, `core`, `deploy`, `kfe`, `node`, `rails`, `shared` e `vault`.

O Admin é um artefato CLI efêmero. A entrada `admin` no lock não autoriza nem
cria um Deployment, StatefulSet, DaemonSet ou Service Kubernetes e não deve ser
embutida nas imagens de Core, Node ou Vault. O operador executa a versão
imutável em uma estação/bastion endurecido ou em um contêiner administrativo de
vida curta e remove o artefato e as credenciais temporárias ao terminar.

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

### 1. Consultar a decisão assinada do Bank

Para o operador consultar a decisão do quorum sem alterar nada, use
`check-update` com a prova TUF, o recibo BFT, o roster confiável e o relatório
assinado dos observadores Bank:

```bash
kerosene-stack check-update \
  --release release-lock.json \
  --environment staging-cell \
  --tuf-proof tuf-target-proof.json \
  --tuf-root-key tuf-root-key.b64 \
  --bft-receipt release-receipt.json \
  --validator-roster release-roster.json \
  --bank-observer-report bank-observer-report.json \
  --state-dir /var/lib/kerosene-stack \
  --json
```

O comando verifica localmente a assinatura e a validade temporal da prova TUF,
o threshold BFT contra o roster e a compatibilidade/threshold dos observadores
Bank. Ele também lê o `update-state.json` anterior quando `--state-dir` é
informado. Não consulta Kubernetes, não altera a Cell e não valida o snapshot;
o snapshot é uma barreira adicional do `update --apply`.

Só prossiga se a saída indicar `updateRequired: true`,
`manualRecoveryRequired: false` e `nextAction: "kerosene-stack update --apply"`.
`tufSignatureVerified` deve ser `true`; `bftSignaturesVerified` e
`bankObserversVerified` são contagens e devem atingir os thresholds declarados
no lock. `false`, prova expirada, sequência não mais nova, threshold
insuficiente ou qualquer exigência de recuperação é condição de parada. O
relatório Bank é uma observação assinada fornecida ao comando; o adaptador
público atual não substitui essa evidência por uma consulta live não
autenticada.

Sem `--apply`, `update` gera apenas um plano de alteração zero. O plano impõe a
sequência: observar o plano Bank, verificar autorização, aceitar o snapshot,
atualizar fundação, Vault, Node e aplicações e, por último, validar/registrar o
resultado. O Admin não é um workload durante essa sequência: ele é o processo
temporário do operador que verifica e inicia a operação. O Node continua sendo
um componente da Cell e deve estar presente no lock e no rollout.

### 2. Aceitar somente o snapshot assinado

Antes de qualquer `--apply`, o operador deve obter um recibo de snapshot ligado
ao `releaseId`, à sequência e ao ambiente `staging-cell`. O recibo precisa ser
assinado pelo provedor; a chave pública confiável do provedor é passada
separadamente em `--snapshot-provider-key`. Um JSON criado ou alterado pelo
operador não é prova de backup e não autoriza a atualização. O controlador
também verifica status, digest, identificador e expiração do recibo.

### 3. Fazer o dry-run com todas as provas

Para uma execução real, o operador precisa fornecer as provas produzidas pela
governança, pelos servidores Bank e pelo provedor de snapshots. Substitua o
valor de exemplo em `--confirm-release` pelo `releaseId` exato do lock:

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
  --snapshot-provider-key snapshot-provider-key.b64 \
  --state-dir /var/lib/kerosene-stack \
  --dry-run --json
```

O `--dry-run` executa todas as verificações de evidência e renderiza os dois
overlays com `kubectl --dry-run=server`, mas não altera recursos Kubernetes. Ele
grava o estado como `dry-run-passed`; isso não equivale a um commit de release.
Revise o `releaseId`, a sequência, a quantidade de observadores e os digests
renderizados antes de avançar.

### 4. Aplicar o mesmo release após a revisão

Repita o comando acima com os mesmos arquivos, o mesmo `--confirm-release` e
sem `--dry-run`. O `--apply` exige todos os documentos TUF/BFT/Bank, o recibo de
snapshot assinado, a confirmação exata do `releaseId` e um `--state-dir`
persistente e controlado pelo operador. Ele grava `update-state.json` nas fases
`verified`, `snapshot-accepted`, `rollout-started` e `validate-and-commit`. O
adaptador aplica primeiro `staging-vault` e depois `staging`, confirma que cada
digest chegou ao workload esperado e aguarda os rollouts e smoke gates
existentes. A produção continua bloqueada no repositório público.

`committed` é o único resultado de sucesso. Se o adaptador falhar, o estado
fica `failed` com `manualRecoveryRequired: true`; pare e siga o procedimento de
recuperação. Não repita o comando nem faça rollback automático de PostgreSQL,
Bitcoin, LND ou Vault.

Sem prova TUF, recibo BFT, relatório Bank, snapshot, confirmação exata do
release ou `--state-dir`, `--apply` falha com código `78`.

## Invariantes já codificados

- Todas as imagens e o bundle de fonte são endereçados por digest.
- O manifesto renderizado também rejeita tags mutáveis em init containers e
  imagens auxiliares, não somente nos serviços listados no lock.
- Um release não pode omitir o artefato Admin, Node ou qualquer outro componente
  da Cell; o artefato Admin é verificado pelo operador e não é um workload
  Kubernetes.
- O Bank exige pelo menos um quorum BFT de `3/4` (ou maior para memberships
  maiores) e o mínimo de observadores não pode ficar abaixo desse limiar.
- Compatibilidade Vault exige no mínimo `2/3`, mas não é usada como ledger de
  ordenação global de releases.
- A atualização exige snapshot; uma migração é `reversible` ou traz evidência
  de recuperação aprovada.
- O recibo de snapshot deve ser assinado pelo provedor e corresponder à chave
  pública confiável fornecida ao operador antes do `--apply`.
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
