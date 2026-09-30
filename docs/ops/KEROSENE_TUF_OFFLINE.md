# TUF offline para releases da Kerosene Cell

O `kerosene-stack` autoriza `check-update` e `update --apply` somente com um
lock `kerosene.release-lock/v2` e um conjunto TUF offline completo. O lock é o
arquivo-alvo de TUF; portanto, ele não contém o hash do próprio arquivo. O
hash, tamanho, expiração e versão pertencem ao metadata TUF assinado.

Este documento descreve o perfil implementado. Ele não transforma o
controlador em um cliente TUF que baixa conteúdo da rede, nem cria ou armazena
chaves privadas.

## Entradas e âncora de confiança

O operador recebe três entradas separadas:

```text
/srv/kerosene/releases/release-lock-v2.json        alvo assinado
/srv/kerosene/releases/tuf/                        pacote de metadata imutável
/etc/kerosene-stack/trusted-root.json              root confiável fora do pacote
/var/lib/kerosene-stack/                           estado local protegido
```

O `trusted-root.json` é uma âncora pública entregue por canal independente e
protegida contra escrita pelo usuário que executa o rollout. Ele define as
chaves e thresholds de `root`, `timestamp`, `snapshot` e `targets`. A chave
privada de root nunca entra nessas pastas, no lock, nas imagens ou no cluster.

O pacote pode conter `1.root.json`, `2.root.json`, etc. A versão igual à
âncora só é aceita se os bytes forem idênticos. Cada rotação posterior precisa
ser sequencial, ter seu próprio threshold de root e também o threshold do root
anterior. Saltos, versões antigas, symlinks e metadados duplicados falham antes
de qualquer deploy.

## Cadeia verificada

O perfil aceita metadata TUF 1.x, JSON canônico, chaves/assinaturas Ed25519 e
SHA-256. A ordem de verificação é:

1. Validar a assinatura e a expiração do root confiável; avançar apenas por
   rotações de root contíguas e duplamente autorizadas.
2. Validar `timestamp.json` com o papel `timestamp` do root ativo.
3. Validar a referência de tamanho, hash e versão de `snapshot.json` presente
   em `timestamp.json`, depois sua assinatura.
4. Validar a referência de `targets.json` (e de qualquer delegação seguida)
   presente em `snapshot.json`, depois as assinaturas e limites de delegação.
5. Encontrar `releases/<releaseId>.json`; o alvo deve ter exatamente o tamanho
   e o SHA-256 dos bytes passados em `--release`, além de `custom` com
   `keroseneReleaseLockCanonicalDigest` igual ao digest canônico do lock.

O controlador também guarda as maiores versões aceitas de root, timestamp,
snapshot, targets e delegações em `trusted-tuf-state.json`. Um pacote com
qualquer papel abaixo da versão persistida é rejeitado. `check-update` apenas
lê esse estado; `update --apply` o grava de modo atômico antes do rollout e sob
o mesmo `update.lock` do estado de atualização.

O diretório de estado é criado com modo `0700`; `update-state.json` e
`trusted-tuf-state.json` são escritos com modo `0600`. Preserve-os em storage
persistente controlado pelo operador. Apagá-los para aceitar metadata mais
antigo é uma violação operacional, não um procedimento de recuperação.

## Comandos

Use um diretório de estado único para o estado da atualização e do TUF:

```bash
kerosene-stack check-update \
  --release /srv/kerosene/releases/release-lock-v2.json \
  --environment staging-cell \
  --tuf-metadata-dir /srv/kerosene/releases/tuf \
  --tuf-trusted-root /etc/kerosene-stack/trusted-root.json \
  --tuf-state-dir /var/lib/kerosene-stack \
  --bft-receipt /srv/kerosene/releases/release-receipt.json \
  --validator-roster /etc/kerosene-stack/release-roster.json \
  --bank-observer-report /srv/kerosene/releases/bank-observer-report.json \
  --state-dir /var/lib/kerosene-stack \
  --json
```

Para `update --apply`, `--tuf-state-dir` e `--state-dir` devem apontar para o
mesmo diretório real. O controlador rejeita links simbólicos nos locks,
evidências e metadados que abre, e verifica a cadeia inteiramente localmente.

`--tuf-proof` e `--tuf-root-key` permanecem somente para inspeção estrutural
de locks v1. Eles não autorizam `check-update` nem `update --apply`.

## Limites assumidos

- A distribuição segura do root inicial, a criação/rotação das chaves e a
  publicação assinada do pacote são responsabilidade da governança de release.
- O perfil não baixa metadata, não implementa retry de espelhos e não substitui
  uma política de provenance de build. Cosign/SLSA podem ser adicionados como
  uma evidência adicional, não como substituto de TUF.
- Produção não é um alvo deste adaptador público. O fluxo público só aplica os
  overlays completos `staging-vault` e `staging`.
