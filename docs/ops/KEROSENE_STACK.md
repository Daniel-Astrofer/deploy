# Kerosene Stack: Cell release lock e atualização segura

`kerosene-stack` é o ponto de partida do controlador operacional da **Kerosene
Cell completa**, não de um contêiner isolado. O lock descreve o artefato Admin
do operador e exige Core, KFE, Node, Vault, web-page, PostgreSQL, Redis, Bitcoin,
LND e Tor; também prende os commits dos repositórios `admin`, `clients`,
`contracts`, `core`, `deploy`, `kfe`, `node`, `rails`, `shared` e `vault`.

O Admin é um artefato CLI efêmero. A entrada `admin` no lock não autoriza nem
cria um Deployment, StatefulSet, DaemonSet ou Service Kubernetes e não deve ser
embutida nas imagens de Core, Node ou Vault. O operador executa a versão
imutável em uma estação/bastion endurecido ou em um contêiner administrativo
local de vida curta, fora do Kubernetes, e remove o artefato e as credenciais
temporárias ao terminar.

O corte atual valida a estrutura do lock, os digests imutáveis, a cadeia TUF
offline completa, o recibo BFT, o relatório assinado e recente dos observadores
Bank e uma atestação de snapshots ligada a uma restauração verificada. Com essas
provas e confirmação explícita do operador, `--apply` executa o adaptador real
da Cell de staging: primeiro `staging-vault`, depois `staging`, usando os
overlays Kubernetes já existentes. Produção continua bloqueada no repositório
público e deve usar o adaptador privado de operações.

## O que é fonte da verdade

O `release-lock` não contém segredos, bancos, chaves, macaroons ou shares. Ele
liga três coisas diferentes, que não devem ser confundidas:

1. O bundle de fonte Git somente leitura, endereçado por digest OCI, e os
   commits exatos de cada repositório.
2. Artefatos executáveis e configurações, todos fixados por `sha256`; nenhum
   serviço pode usar `latest` ou ser reconstruído no host durante a atualização.
3. A decisão de distribuição: metadata TUF assinado, o recibo de commit BFT,
   relatório temporal dos observadores Bank e a compatibilidade do quorum
   Vault. A cadeia TUF e as assinaturas BFT/Bank são verificadas localmente;
   compatibilidade Vault continua sendo uma declaração de entrada que não
   ordena releases.

Os Vaults não são espelhos históricos de Git, registro de releases nem fonte de
verdade para código. Seus volumes guardam estado criptográfico e exigem backup;
o conteúdo de release vem do bundle/commits imutáveis autenticados por TUF. Isso
evita transformar disponibilidade de signer em autorização de deploy ou expor
estado de Vault a ferramentas de build/administração.

O lock novo é o schema
[`infra/stack/release-lock-v2.schema.json`](../../infra/stack/release-lock-v2.schema.json).
Os contratos de evidência estão em `infra/stack/*-receipt.schema.json`,
`infra/stack/snapshot-attestation-request.schema.json`,
`infra/stack/release-roster.schema.json` e
`infra/stack/bank-observer-report.schema.json`. O exemplo v2 é sintético e
precisa entrar em metadata TUF real antes de ser utilizável:
[`infra/stack/examples/release-lock-v2.example.json`](../../infra/stack/examples/release-lock-v2.example.json).
O exemplo v1 permanece somente como compatibilidade estrutural; não pode
autorizar `check-update` nem `--apply`.

## Uso atual

No checkout de Deploy, exponha `infra/` no `PATH` ou invoque o arquivo
diretamente:

```bash
PATH="$PWD/infra:$PATH" kerosene-stack verify-release \
  --release infra/stack/examples/release-lock-v2.example.json

PATH="$PWD/infra:$PATH" kerosene-stack update \
  --release infra/stack/examples/release-lock-v2.example.json \
  --output /tmp/kerosene-update-plan.json
```

### 1. Consultar a decisão assinada do Bank

Para o operador consultar a decisão do quorum sem alterar nada, use
`check-update` com o pacote TUF offline, o recibo BFT, o roster confiável e o
relatório assinado dos observadores Bank. O root confiável vem de um canal
separado do pacote TUF; veja [o perfil TUF offline](KEROSENE_TUF_OFFLINE.md).

```bash
kerosene-stack check-update \
  --release release-lock.json \
  --environment staging-cell \
  --tuf-metadata-dir /srv/kerosene/releases/tuf \
  --tuf-trusted-root /etc/kerosene-stack/trusted-root.json \
  --tuf-state-dir /var/lib/kerosene-stack \
  --bft-receipt release-receipt.json \
  --validator-roster release-roster.json \
  --bank-observer-report bank-observer-report.json \
  --state-dir /var/lib/kerosene-stack \
  --json
```

O comando verifica localmente a cadeia `root → timestamp → snapshot → targets`,
o threshold BFT contra o roster e a compatibilidade/threshold dos observadores
Bank. O relatório Bank v2 tem `issuedAt`, `expiresAt` de no máximo uma hora e
observações com até quinze minutos de idade. Ele também lê o `update-state.json`
anterior quando `--state-dir` é informado. Não consulta Kubernetes, não altera
a Cell e não valida o snapshot; o snapshot é uma barreira adicional do
`update --apply`.

Só prossiga se a saída indicar `updateRequired: true`,
`manualRecoveryRequired: false` e `nextAction: "kerosene-stack update --apply"`.
`tufSignatureVerified` deve ser `true`; `bftSignaturesVerified` e
`bankObserversVerified` são contagens e devem atingir os thresholds declarados
no lock. `false`, metadata ou relatório expirado, sequência não mais nova, threshold
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

### 2. Coletar e atestar o conjunto completo de snapshots

Antes de qualquer `--apply`, colete uma requisição sem assinatura para todos os
PVCs persistentes da Cell: PostgreSQL, Redis, Bitcoin, LND, Tor, os três Vaults
de integração, Vault principal e Tor do Vault. O Admin não tem PVC: é o
artefato efêmero do operador. O digest canônico do lock vem da saída de
`kerosene-stack plan --json`.

```bash
bash infra/kubernetes/scripts/collect-staging-volumesnapshot-attestation-request.sh \
  --release-id bank-mainnet-2026.09.28.1 \
  --release-lock-digest sha256:<digest-canônico-do-lock> \
  --snapshot-selector 'kerosene.io/release-id=bank-mainnet-2026.09.28.1' \
  --output /srv/kerosene/releases/snapshot-attestation-request.json
```

O coletor só aceita exatamente um `VolumeSnapshot` pronto por PVC, gera uma
ordem determinística e não possui chave de assinatura. Um atestador externo e
independente deve restaurar e verificar o conjunto, então produzir o recibo
`kerosene.snapshot-receipt/v2`. O recibo liga `snapshotSetDigest` e
`attestationRequestDigest`, declara `restoreTested: true`, expira e é assinado
pela chave pública fornecida separadamente em `--snapshot-provider-key`. Um
JSON criado ou alterado pelo operador não é prova de backup.

### 3. Fazer o dry-run com todas as provas

Para uma execução real, o operador precisa fornecer as provas produzidas pela
governança, pelos servidores Bank e pelo atestador de snapshots. Substitua o
valor de exemplo em `--confirm-release` pelo `releaseId` exato do lock:

```bash
kerosene-stack update \
  --release release-lock.json \
  --apply \
  --environment staging-cell \
  --confirm-release bank-mainnet-2026.09.28.1 \
  --tuf-metadata-dir /srv/kerosene/releases/tuf \
  --tuf-trusted-root /etc/kerosene-stack/trusted-root.json \
  --tuf-state-dir /var/lib/kerosene-stack \
  --bft-receipt release-receipt.json \
  --validator-roster release-roster.json \
  --bank-observer-report bank-observer-report.json \
  --snapshot-attestation-request snapshot-attestation-request.json \
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
sem `--dry-run`. O `--apply` exige todos os documentos TUF/BFT/Bank, a
requisição de atestação e o recibo restore-tested, a confirmação exata do
`releaseId` e um `--state-dir` persistente e controlado pelo operador.
`--tuf-state-dir` deve ser o mesmo diretório real. Ele grava
`update-state.json` nas fases `verified`, `snapshot-accepted`,
`rollout-started` e `validate-and-commit`. O adaptador aplica primeiro
`staging-vault` e depois `staging`, confirma que cada digest chegou ao workload
esperado e aguarda os rollouts e smoke gates existentes. A produção continua
bloqueada no repositório público.

`committed` é o único resultado de sucesso. Se o adaptador falhar, o estado
fica `failed` com `manualRecoveryRequired: true`; pare e siga o procedimento de
recuperação. Não repita o comando nem faça rollback automático de PostgreSQL,
Bitcoin, LND ou Vault.

O `--apply` cria `update.lock` de forma exclusiva no `--state-dir` e o mantém
por toda a execução. Se o lock já existir, o comando bloqueia antes de iniciar
qualquer rollout: isso pode indicar outro operador ativo ou um lock antigo de
uma interrupção inesperada. Nos dois casos, preserve `update.lock` e
`update-state.json`, investigue manualmente se há uma execução em curso e
reconcilie o estado da Cell antes de uma liberação autorizada. Não apague o lock
automaticamente e não use tentativas repetidas para contornar essa barreira.

Sem cadeia TUF completa, recibo BFT, relatório Bank atual, requisição e recibo
de snapshot, confirmação exata do release ou diretório de estado, `--apply`
falha com código `78`.

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
- O recibo de snapshot deve ser assinado pelo provedor, atar a requisição
  determinística dos dez PVCs e afirmar uma restauração verificada antes do
  `--apply`.
- O `release-lock` v2 ancora em um `root.json` TUF protegido, verifica
  `timestamp`, `snapshot`, `targets` e delegações, e rejeita rollback de
  versões persistidas.
- O relatório Bank v2 é curto, assinado pelo quorum e só aceita observações
  recentes do release exato.
- O lock rejeita campos que pareçam carregar material secreto, e não aceita
  campos desconhecidos silenciosamente.
- `allowSourceBuild` e `vaultSignerActivation` devem ser `false`.

## Próximos cortes

1. Formalizar os schemas v2 no repositório `contracts` e gerar clientes para
   Core, Admin e os Bank observers.
2. Ligar o atestador a um provedor real de VolumeSnapshot/restic e um ambiente
   de restore isolado; o coletor público não cria snapshots nem possui chave.
3. Fazer os servidores Bank publicarem as observações diretamente por mTLS,
   com contrato, identidade de servidor, nonce/replay e retenção definidos; o
   adaptador público não inventa esse endpoint.
4. Adicionar provenance Cosign/SLSA ao bundle antes de sua publicação em TUF.
5. Adicionar smoke gates financeiros e recuperação automatizada por componente,
   mantendo rollback manual para migrações irreversíveis.
