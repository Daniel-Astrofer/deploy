# Plano de integração segura dos serviços

Data da análise: 2026-07-27
Escopo: mudanças locais das últimas seis horas, KFE, contracts, vault, adapters, Docker e Kubernetes.

## 1. Estado observado

Não há commits no período. O worktree contém 249 entradas modificadas ou novas. O recorte é um snapshot não versionado; horário de arquivo não define dependência nem autoria.

Mudanças principais:

- `kerosene-contracts`: contratos V2 tipados para intent, PSBT, receipt, quorum, notificações, auditoria e administração.
- `kfe-service`: estado de transação, outbox, finality, pricing, production gate, proof of reserves, webhooks e integração multi-vault.
- `kerosene-vault`: reshare wire, envelope híbrido/PQ, TPM TSS, migração quântica, métricas e testes adversariais.
- adapters Bitcoin/Lightning: autenticação por escopo, TLS, idempotência, rate limit e validações de rede/pagamento.
- infraestrutura: workloads de vault, NetworkPolicy, Tor authorized clients, imagens e scripts de deploy.

Atualização após integração:

- backend completo: `clean test` verde;
- KFE: 389 testes verdes;
- Vault: matrizes `production,hybrid` e `dealer_lab` verdes;
- imagens `server`, `kfe-service` e `kerosene-vault` construídas;
- overlays local/staging renderizam e passam no dry-run cliente;
- Compose staging/Tor validado.

**SALIENTE — ainda é NO-GO para produção.** O código compila e pode ser
executado em laboratório/staging, mas as garantias anunciadas como PQ e hardware
não estão completas. `HybridEnvelopeAdapter::seal/open` ainda retorna
`ProductionGate`, KATs criptográficos ML-KEM/ML-DSA continuam ignorados, o
controller de migração/sweep contém caminhos `NotImplemented`, TPM depende de
ferramentas/HW externos e TEE real exige `tee_hw`. Além disso, crypto de colunas,
aprovação, quorum, MPC e rail health ainda possuem consumidores legados no
runtime Java. Compatibilidade não equivale a adoção V2.

### Estado executável do staging

- KFE usa Vault Mesh em `mesh-only`; MPC signing permanece desligado.
- KFE alcança os três vaults por HTTPS/mTLS.
- Cada vault possui Secret de dados e Secret mTLS próprios.
- NetworkPolicies permitem somente KFE→vault e vault↔vault na porta 7801.
- O rollout dos três vaults é obrigatório; perda de qualquer deployment falha o deploy.
- Testnet3 é a rede do staging. Lightning usa a denominação `testnet` do LND e invoices `lntb`.
- Adapters validados: Bitcoin Core 14 testes; Lightning 27 testes.

O staging depende de imagens publicadas, Secrets centrais e materiais de
cerimônia. Consulte `docs/ops/STAGING_DEPLOY.md`.

## 2. Decisão de arquitetura

Produção deve separar dois ambientes:

1. Serviços centrais em Kubernetes: edge, aplicação, KFE, adapters, PostgreSQL, Redis e observabilidade.
2. Vaults externos: máquinas independentes, operadas fora do cluster, conectadas por Onion e mTLS.

Três vaults no mesmo cluster servem apenas para desenvolvimento e staging. Colocá-los no mesmo cluster, nó, provedor ou domínio administrativo anula a independência esperada do quorum.

Fluxo de confiança:

```text
cliente
  -> edge/app
  -> KFE (ledger, policy, intent)
  -> adapter BTC/LND ou gateway do vault
  -> vaults externos 2/3
  -> Bitcoin Core/LND
```

Regras:

- JWT de usuário termina no edge/KFE; não é credencial entre serviços.
- Serviços usam identidades próprias, mTLS e autorização por operação.
- KFE nunca recebe shares nem material privado dos vaults.
- Vault assina somente intent canônico, PSBT vinculado e política local válida.
- Receipt só altera o ledger após verificação criptográfica e finality definida.
- Rede, autenticação, rate limit, quorum e auditoria falham fechados em produção.

## 3. Bloqueadores

### P0 — antes de qualquer integração

1. ~~Restaurar build verde do backend.~~ Concluído; manter como gate obrigatório de CI.
2. Integrar contratos V2 ao runtime. Criar adapters de compatibilidade temporários e eliminar uso legado por fluxo, não apenas marcar `@Deprecated`.
3. Definir serialização canônica Java/Rust com fixtures compartilhadas para intent, hash do PSBT e receipt.
4. Verificar receipt criptograficamente. Campos opcionais ou uma string `signature` copiada não constituem prova.
5. Tornar constitution, suite, session, threshold, participantes e hashes obrigatórios. Ausência deve rejeitar.
6. Substituir o parser BOLT11 próprio por biblioteca madura ou `decodepayreq` do LND. A interpretação atual de timestamp, expiry e unidade não é confiável.
7. Validar o reshare wire em E2E multinó com falha parcial; testes de reshare local são agora restritos a `dealer_lab`.
8. Não declarar PQ, TPM ou migração quântica como implementados enquanto:
   - abertura do envelope híbrido depender de injeção ainda ausente;
   - ML-KEM/ML-DSA usarem bytes placeholder;
   - TSS/DCAP não estiverem ligados;
   - sweep/drill forem no-op;
   - KATs completos estiverem ignorados.

### P0 — Kubernetes

1. Declarar ou provisionar explicitamente `ServiceAccount`, ConfigMaps, Secrets e PVCs referenciados pelos vaults. Hoje os manifests dependem de objetos não definidos no overlay.
2. Remover sucesso não fatal do rollout dos vaults. Sem quorum saudável, deploy deve falhar.
3. Remover `--force-conflicts` do caminho normal. Conflito de ownership exige revisão.
4. Corrigir probes: kubelet não apresenta certificado cliente em probe HTTP. Usar endpoint local mínimo, `exec` ou listener de health separado.
5. Remover `VAULT_API_TOKEN` do workload de produção mTLS.
6. Separar segredo e certificado por vault. Um Secret compartilhado aumenta o raio de comprometimento.

### P1 — endurecimento

- Imagens por digest, assinatura Cosign e SBOM; tags `:staging` não são identidade de release.
- Admission policy para recusar imagem sem digest/assinatura, root, capabilities, host mounts e service-account token desnecessário.
- `PodDisruptionBudget`, anti-affinity e topology spread para workloads de quorum em staging.
- Egress explícito para DNS, Onion/Tor, Bitcoin Core/LND e endpoints de attestation necessários. Todo o restante negado.
- Rate limit Redis deve falhar fechado em produção nos adapters BTC/LND.
- Issuer e audience JWT ausentes devem impedir boot, não apenas gerar log.
- `cargo audit` não pode terminar com `|| true`.
- Features Rust são de compilação; remover `--features production,hybrid` dos argumentos de runtime e validar a variante dentro da imagem.

## 4. Sequência de integração

### Fase 0 — congelar o snapshot

- Pausar mudanças concorrentes.
- Salvar patch e inventário.
- Separar commits por domínio: contracts, KFE, vault, adapters, Docker, Kubernetes e docs.
- Cada commit deve compilar e declarar dependências.
- Não misturar correção funcional com manifests de produção.

Gate: histórico revisável, nenhum arquivo crítico sem dono e build-base reproduzível.

### Fase 1 — contratos e compatibilidade

- Fixar `VaultMeshIntentV2`, request PSBT e receipts selados.
- Definir versionamento, domain separator, canonical encoding e limites.
- Implementar testes Java/Rust usando os mesmos vetores.
- Criar compatibility adapters com telemetria de uso legado.
- Migrar primeiro leitura, depois escrita; remover fallback após soak.

Gate: contratos possuem testes reais, hashes idênticos entre linguagens e nenhum downgrade silencioso.

### Fase 2 — KFE e consistência financeira

- Integrar state machine, reservation token, outbox e finality numa única transação lógica.
- Garantir idempotência por intent, pagamento e evento.
- Separar estados `accepted`, `signed`, `broadcast`, `confirmed`, `rejected` e `fail-stop`.
- Verificar receipt antes do commit financeiro.
- Fazer reconciliação periódica entre ledger, mempool/chain e receipts.

Gate: crash/retry não duplica débito, assinatura, broadcast ou notificação.

### Fase 3 — adapters Bitcoin e Lightning

- Trocar parser BOLT11.
- Validar network, amount, destination, expiry e payment hash.
- Usar credenciais distintas read/write/admin e mTLS.
- Tornar rate limit, Redis e idempotência fail-closed no perfil production.
- Limitar egress do adapter ao daemon correspondente.

Gate: testes com invoice inválida, replay, rede errada, Redis indisponível e daemon divergente.

### Fase 4 — vault

- Conectar reshare distribuído ao runtime e persistência.
- Aplicar autenticação, transcript binding, epoch e anti-replay em todas as rodadas.
- Implementar seal TPM real e política PCR/upgrades com recuperação documentada.
- Concluir envelope híbrido e algoritmos reais antes de ativar a suite PQ.
- Aplicar verificação local independente de intent, PSBT, outputs, fee e constitution.

Gate: 2/3 funciona; 1/3 não assina; perda, replay, nó malicioso, restart e reshare preservam a chave pública e o ledger de consumo.

### Fase 5 — imagens Docker

- Uma imagem mínima por serviço e variante.
- Multi-stage, usuário não root, filesystem somente leitura e sem shell quando possível.
- Pin de base e dependências por digest/lock.
- BuildKit secrets; nenhum segredo em `ARG`, `ENV`, layer ou log.
- Gerar SBOM, scan de CVE, assinatura e provenance.
- Publicar pelo SHA; promover o mesmo digest entre ambientes.

Gate: scan sem exceção P0/P1, assinatura validada e execução sem privilégios.

### Fase 6 — Kubernetes

- Gerar Secrets via External Secrets/CSI; nunca por YAML real no Git.
- Usar default-deny ingress/egress por namespace.
- Declarar NetworkPolicy por fluxo e testar conectividade permitida e negada.
- Adotar policy-as-code para security context e supply chain.
- Executar migração de banco como Job versionado, antes do rollout compatível.
- Rollout canário: contracts compatíveis -> KFE -> adapters -> gateway vault.
- Manter vaults domésticos fora do cluster; staging usa instâncias isoladas e distribuídas entre nós.

Gate: manifests renderizados, `kubeconform`, policies, dry-run server-side, rollout e rollback testados.

### Fase 7 — E2E, caos e go-live

Cenários obrigatórios:

- KFE reinicia entre reserve, sign, broadcast e receipt.
- Redis/PostgreSQL indisponíveis.
- Um vault offline e dois online; dois offline.
- Nó vault envia share, epoch ou receipt inválido.
- Rotação de certificado, share, suite e chave de storage.
- Tor indisponível, latência, duplicação e reordenação.
- Reorg Bitcoin e divergência BTC/LND.
- Imagem não assinada, Secret ausente e NetworkPolicy bloqueando.

Gate: fail-stop comprovado, reconciliação sem perda financeira, RTO/RPO definidos e runbook exercitado.

## 5. Contrato mínimo de implantação

Cada serviço deve declarar:

| Campo | Exigência |
|---|---|
| Identidade | SPIFFE/SAN exclusivo |
| Entrada | portas, protocolo e caller autorizado |
| Saída | destinos exatos e motivo |
| Secrets | proprietário, origem, rotação e revogação |
| Dados | schema/versionamento e retenção |
| Falha | fail-open ou fail-closed, explicitamente |
| Health | liveness, readiness e dependências |
| Observabilidade | métricas, audit ID e correlation ID |
| Imagem | digest, SBOM, assinatura e provenance |
| Rollback | compatibilidade e limite seguro |

## 6. Critério de conclusão

A integração está pronta somente quando:

- build e testes de todos os módulos passam pelo `smart_build.py`;
- não há contrato V2 sem implementação ou teste;
- nenhum caminho legado assina ou commita em paralelo;
- receipt e intent são verificados ponta a ponta;
- manifests não dependem de objetos implícitos;
- quorum externo continua independente do cluster central;
- imagens e configuração são imutáveis e verificáveis;
- falhas de auth, Redis, quorum, Tor e policy fecham o fluxo financeiro;
- testes E2E e caos produzem evidência auditável.

## 7. NÃO INTEGRADO / NÃO CONFORME — contratos `4f242cdd`

> **BLOQUEIO DE RELEASE:** o backend não compila após a mudança dos contratos.
> Nenhuma feature V2 abaixo deve ser anunciada como integrada ou segura enquanto
> os consumidores, verificadores e testes não passarem pelo `smart_build.py`.

### Implementado e validado

- `VaultMeshIntent.createdAt` foi migrado de epoch `long` para `Instant` no cálculo
  legado de hash do cliente KFE.
- Esta alteração isolada não foi validada por build verde: a compilação encontrou
  outros 34 erros de integração.

### Contratos ainda sem integração completa

| Área | Serviço afetado | Evidência | Risco | Ação pendente |
|---|---|---|---|---|
| Ports V2 do vault | `kfe-service`, `kerosene-app` | Consumidores ainda injetam `VaultMeshSettlementPort`; defaults legados lançam `UnsupportedOperationException` | Falha em runtime ou fallback inconsistente | Implementar adapters explícitos para `VaultIntentPort`, `VaultReservationPort`, `VaultPsbtSigningPort`, `VaultGovernancePort` e `VaultDepositDescriptorPort` |
| Timestamps `Instant` | `kfe-service` | Múltiplos construtores ainda fornecem `toEpochMilli()` | Backend não compila | Migrar todos os call sites e testes |
| Quorum tipado | `kfe-service` + vault mesh | Integrado em 2026-07-28: `VaultMeshFinancialQuorumAdapter` valida contexto, roster, threshold, chave USERS e prova BIP340 produzida por FROST 2/3 | A atribuição dos participantes ainda é declaração autenticada do coordenador; a assinatura agregada prova o threshold, mas não incorpora IDs individuais | Manter mTLS, pin da chave de grupo e auditoria dos participantes; não habilitar fallback em produção |
| Aprovação tipada | `kfe-service` | Cliente remoto e fallback ainda usam strings e métodos deprecated | Fatores podem perder vínculo com challenge/proposal | Migrar para `OutboundApprovalChallenge` e assertions tipadas; manter adapter legado observável somente na borda |
| MPC key receipt | `kfe-service` | Adapter e fallback ainda usam assinatura antiga | Provisionamento sem receipt V2 verificável | Implementar `MpcWalletKeyRequest`/`MpcWalletKeyReceipt` |
| Crypto de coluna | `kfe-service`, `kerosene-app`, `kerosene-shared` | Implementações e converters ainda dependem de `getMasterKeyBytes()` ou não implementam `needsRotation(EncryptedValue)` | Exposição de chave mestra ou falha em runtime | Migrar converters para operações de envelope; remover chamadas ativas à exportação de chave |
| Rail health | `kfe-service` | Adapter retorna `ProviderStatus`/`Map`, contrato requer `ProviderHealth`/`List` | Health incompatível e build quebrado | Mapear estados V2 sem converter desconhecido em saudável |
| Audit integrity | `kfe-service` | Adapter implementa root legado e não implementa inclusion/consistency proofs | “verified” sem prova criptográfica | Criar verifier/porta e devolver indisponível até existir prova real |
| Notifications V2 | `kfe-service` | Outbox ainda depende de `FinancialNotificationPort` legado | `UnsupportedOperationException` ou perda de evento | Publicar somente `FinancialNotificationPortV2`; adapter legado deve emitir métrica/audit e nunca descartar |

### Implementação existente sem prova/teste suficiente

| Feature | Lacuna | Condição para conformidade |
|---|---|---|
| Intent canônico | Domain string existe, mas não há encoding canônico normativo nem vetor Java/Rust | `canonicalBytes()` determinístico, hash SHA-256, limites e vetores cruzados |
| Receipt verificável | Proof continua representado como texto sem verifier criptográfico obrigatório | Payload canônico, suite/key/constitution binding e verificação fail-closed |
| PSBT V2 | Arrays e campos críticos precisam de cópia defensiva e invariantes | Testes de mutação, hashes, expiração, descriptors, rede e Base64 |
| `EncryptedValue` | Arrays precisam de cópia defensiva e parâmetros AEAD precisam de validação | Nonce/tag/version/algorithm validados e testes de mutação |
| `HybridAuthorization` | Relaxamento testnet não pode ser escolhido pelo caller | Política derivada de ambiente/rede confiável e teste anti-downgrade |
| Quorum | Set de participantes e roster/proof precisam de vínculo imutável | Cópia defensiva, unicidade, membership, threshold e verifier |
| STOMP/audit proofs | Comprimento/booleans são declarados pelo caller | Derivar comprimento UTF-8 e substituir flags por resultado de verifier |

### Fora do escopo desta integração Java

- Compatibilidade canônica no vault Rust.
- Docker, Kubernetes, TPM/TEE, reshare e PQ.
- Esses componentes só podem ser marcados conformes depois de fixtures Java/Rust
  e E2E KFE-vault verificarem o mesmo intent, PSBT e receipt.

### Estado da validação

- Build completo inicial: 35 erros.
- Após a primeira correção: 34 erros.
- A terceira execução diagnóstica confirmou incompatibilidades nos adapters,
  fallbacks e timestamps.
- O circuit breaker de `AGENTS.md` foi atingido. Nenhuma quarta tentativa deve ser
  executada antes de intervenção manual e `python3 scripts/smart_build.py --reset`.
