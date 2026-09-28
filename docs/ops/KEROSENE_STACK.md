# Kerosene Stack: Cell release lock e atualização segura

`kerosene-stack` é o ponto de partida do controlador operacional da **Kerosene
Cell completa**, não de um contêiner isolado. O lock exige Admin, Core, KFE,
Node, Vault, Rails, PostgreSQL, Redis, Bitcoin, LND e Tor; também prende os
commits dos repositórios `admin`, `clients`, `contracts`, `core`, `deploy`,
`kfe`, `node`, `rails`, `shared` e `vault`.

O corte atual é deliberadamente seguro por omissão: valida a estrutura do lock,
os digests imutáveis e as regras que impedem build local e ativação de signers
Vault. Ele produz o plano de rollout, mas **não executa atualização**. Ainda
faltam o verificador criptográfico de metadados TUF/recibo BFT, a coleta
autenticada de observações do plano Bank e os adaptadores de rollout para que
`--apply` possa existir com segurança.

## O que é fonte da verdade

O `release-lock` não contém segredos, bancos, chaves, macaroons ou shares. Ele
liga três coisas diferentes, que não devem ser confundidas:

1. O bundle de fonte Git somente leitura, endereçado por digest OCI, e os
   commits exatos de cada repositório.
2. Artefatos executáveis e configurações, todos fixados por `sha256`; nenhum
   serviço pode usar `latest` ou ser reconstruído no host durante a atualização.
3. A decisão de distribuição: o alvo TUF, o recibo de commit BFT e a
   compatibilidade do quorum Vault. Nesta etapa esses campos são validados como
   referências estruturais; eles ainda não são prova criptográfica local.

O schema público está em
[`infra/stack/release-lock.schema.json`](../../infra/stack/release-lock.schema.json).
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

`update` é atualmente equivalente a gerar um plano de alteração zero. O plano
impõe a sequência: observar o plano Bank, verificar autorização, capturar
snapshots, drenar aplicações, atualizar fundação, Vault um a um, Node um a um,
aplicações e, por último, validar/registrar o resultado. O Admin e o Node são
passos obrigatórios, não apêndices opcionais.

```bash
kerosene-stack update --release release-lock.json --apply
```

Esse comando falha intencionalmente com código `78` nesta revisão. Não é
seguro transformar um lock apenas estrutural em autoridade para `kubectl`,
Compose, banco de dados ou Vault.

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

## Próximos cortes para habilitar apply

1. Formalizar este contrato no repositório `contracts` e gerar clientes para
   Core, Admin e Bank observers.
2. Implementar verificação TUF, Cosign/SLSA e recibo BFT real, sobre
   serialização canônica assinada.
3. Fazer os servidores Bank publicarem observações assinadas de versão,
   compatibilidade e urgência ao Admin.
4. Implementar adaptadores idempotentes para Kubernetes/Compose, snapshots e
   gates de saúde/reconciliação.
5. Habilitar `--apply` somente após essas verificações e uma aprovação humana
   explícita, registrando um recibo de atualização assinado.
