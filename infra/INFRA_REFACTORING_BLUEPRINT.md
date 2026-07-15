# Infra Quorum Local — plano de simplificação

## 1. Tese correta

`infra/` não é a infraestrutura final de produção dos serviços da Kerosene.

`infra/` é apenas o ambiente local/integrado para simular o quorum completo da Kerosene rodando junto.

Ele deve servir para responder uma pergunta simples:

> Todos os serviços principais conseguem subir, conversar entre si e operar como um conjunto?

Por isso, `infra/` deve ser pequeno, direto e operacional. Ele não deve tentar virar uma plataforma completa com dezenas de scripts, múltiplas estratégias de deploy e abstrações que ainda não são necessárias.

Cada aplicação da Kerosene deve futuramente ter sua própria imagem, seu próprio Dockerfile e seu próprio Kubernetes. Quando isso acontecer, `infra/` continuará existindo apenas como agregador local para testar o conjunto.

## 2. O que `infra/` deve fazer

`infra/` deve fazer somente isto:

1. Subir o quorum local completo.
2. Desligar o quorum local completo.
3. Recriar o quorum local completo.
4. Mostrar status do quorum.
5. Mostrar/coletar logs do quorum.
6. Construir ou importar imagens locais necessárias para o teste integrado.
7. Manter configurações locais de runtime usadas pela simulação.

Nada além disso deve ser tratado como responsabilidade principal de `infra/`.

## 3. O que `infra/` não deve ser

`infra/` não deve ser:

- A infraestrutura final de produção da Kerosene.
- O lugar onde mora código de serviço.
- O lugar onde cada serviço define sua arquitetura definitiva.
- Um diretório com dezenas de scripts públicos.
- Um depósito de scripts antigos.
- Um ambiente para resolver todos os casos: local, staging, produção, HA, Android, release, Vault, debug, agentes e migração ao mesmo tempo.

A regra é simples: se algo não ajuda diretamente a ligar, desligar, recriar, ver status ou diagnosticar o quorum local, não deve ser script público de `infra/`.

## 4. Interface pública final

O operador não deve precisar conhecer a árvore interna.

A interface pública deve ser esta:

```bash
bash infra/start.sh
bash infra/stop.sh
bash infra/recreate.sh
bash infra/status.sh
bash infra/logs.sh
```

Opcionalmente:

```bash
bash infra/test.sh
```

Esses comandos devem ser a única interface normal para humanos e agentes.

### `infra/start.sh`

Sobe o quorum completo local.

Responsabilidades:

- Validar pré-requisitos mínimos.
- Construir/importar imagens locais quando necessário.
- Aplicar Kubernetes local ou iniciar Compose local, conforme o mecanismo escolhido.
- Esperar os serviços principais ficarem prontos.
- Mostrar URLs, portas e status final.

### `infra/stop.sh`

Desliga o quorum completo local.

Responsabilidades:

- Parar workloads do quorum.
- Preservar dados locais por padrão.
- Não apagar volumes, bancos ou chaves sem flag explícita.
- Ser seguro para uso diário.

### `infra/recreate.sh`

Recria o quorum local.

Responsabilidades:

- Derrubar os recursos atuais.
- Recriar workloads.
- Reconstruir/importar imagens se necessário.
- Aguardar readiness.

Esse comando pode futuramente ter flags como:

```bash
bash infra/recreate.sh --clean
bash infra/recreate.sh --clean-volumes
bash infra/recreate.sh --no-build
```

Mas o padrão deve ser seguro.

### `infra/status.sh`

Mostra estado do quorum.

Responsabilidades:

- Mostrar pods/containers.
- Mostrar serviços.
- Mostrar portas.
- Mostrar imagens usadas.
- Mostrar o que está saudável e o que está quebrado.

### `infra/logs.sh`

Mostra logs do quorum.

Responsabilidades:

- Logs gerais.
- Logs por serviço quando informado.
- Logs recentes por padrão.
- Modo follow quando solicitado.

Exemplo futuro:

```bash
bash infra/logs.sh server
bash infra/logs.sh mpc-sidecar --follow
```

## 5. Estrutura alvo mínima

A estrutura deve ser enxuta:

```text
infra/
  README.md
  INFRA_REFACTORING_BLUEPRINT.md

  start.sh
  stop.sh
  recreate.sh
  status.sh
  logs.sh
  test.sh

  docker/
    images.yaml
    images/
      server/
      kfe-service/
      mpc-sidecar/
      web-page/
      tor/
      vault/
    compose/
      local.compose.yaml
      local.kfe.compose.yaml
      local.limits.compose.yaml
      hardened.compose.yaml
    scripts/
      build-image.sh

  kubernetes/
    quorum/
      kustomization.yaml
      namespace.yaml
      server.yaml
      kfe-service.yaml
      mpc-sidecar.yaml
      web-page.yaml
      dependencies.yaml
      policies.yaml
    scripts/
      apply.sh
      wait.sh
      status.sh
      logs.sh

  runtime/
    postgres/
    vault/
    tor/
    bitcoin/
    lightning/
    web/
    observability/

  scripts/
    common.sh
    quorum.sh
    images.sh
```

A ideia não é criar mais camadas. A ideia é reduzir a árvore mental.

O usuário chama `infra/start.sh`. Internamente ele pode chamar `infra/scripts/quorum.sh`, `infra/docker/scripts/build-image.sh` ou `infra/kubernetes/scripts/apply.sh`. Mas esses scripts internos não devem virar API pública.

## 6. Regra sobre código de serviço

`infra/` não deve conter código real de aplicação.

Código real deve morar fora de `infra/`:

```text
backend/             servidor Java Spring
frontend/            web/app/admin
mpc-sidecar/         implementação do sidecar, se existir como projeto próprio
kfe/                 futuro serviço financeiro independente
```

Dentro de `infra/` podem existir apenas:

- Dockerfiles.
- Compose.
- Kubernetes manifests.
- Configuração local de runtime.
- Scripts de orquestração.
- Scripts de entrypoint usados por imagens de dependência.

## 7. Regra sobre imagens

Cada aplicação deve caminhar para ter sua própria imagem.

Imagens do quorum local:

```text
kerosene/server
kerosene/kfe-service
kerosene/mpc-sidecar
kerosene/web-page
kerosene/tor
kerosene/vault
```

O contrato de imagens pode continuar em:

```text
infra/docker/images.yaml
```

Esse arquivo deve ser a fonte de verdade para o ambiente local integrado.

Mas o ponto importante é: a imagem pertence ao serviço. `infra/` apenas sabe como construir ou usar essa imagem durante o teste do quorum.

## 8. Kubernetes neste momento

Kubernetes aqui deve ser somente o Kubernetes do quorum local.

Não precisa modelar produção agora.
Não precisa modelar staging agora.
Não precisa modelar uma plataforma HA definitiva agora.

O Kubernetes em `infra/` deve responder apenas:

> Como eu subo todos os serviços juntos localmente para testar o conjunto?

A estrutura recomendada é uma única área clara:

```text
infra/kubernetes/quorum/
```

Ela deve conter os manifests necessários para o quorum local.

Se no futuro cada serviço tiver seu próprio Kubernetes, a estrutura vira:

```text
services/server/kubernetes/
services/kfe/kubernetes/
services/mpc-sidecar/kubernetes/
apps/web/kubernetes/
infra/kubernetes/quorum/
```

Nesse futuro, `infra/kubernetes/quorum/` apenas junta tudo para teste integrado.

## 9. Compose neste momento

Compose só deve existir se ajudar o teste local.

Ele não deve competir com Kubernetes.

Escolha prática:

- Se o quorum local oficial for Kubernetes, Compose vira suporte legado/local específico.
- Se o quorum local oficial for Compose por enquanto, Kubernetes fica reduzido até realmente ser necessário.

O erro atual é tentar manter os dois como caminhos principais ao mesmo tempo.

A decisão recomendada para simplificar:

> Definir Kubernetes local como caminho principal do quorum e deixar Compose como auxiliar/legado.

Mas se Kubernetes estiver atrapalhando agora, o inverso também é aceitável temporariamente:

> Definir Compose como caminho principal do quorum local até Kubernetes ficar limpo.

O importante é ter um caminho principal, não dois.

## 10. Problema atual dos scripts

Hoje há scripts demais e eles estão misturados.

O problema não é ter scripts. O problema é que scripts de domínios diferentes estão no mesmo lugar e parecem todos públicos.

Exemplos de mistura atual:

- Script local dentro de `infra/kubernetes/scripts/`.
- MCP dentro de `infra/kubernetes/scripts/`.
- Build web/backend no mesmo espaço de deploy Kubernetes.
- Wrappers antigos apontando para caminhos que parecem canônicos.
- Vários scripts de start/stop/logs/status em vez de uma entrada simples.

Isso faz o operador pensar:

> Qual desses scripts eu rodo?

A refatoração deve eliminar essa dúvida.

## 11. Política simples para scripts

Todo script deve entrar em uma destas categorias:

### Público

Somente estes:

```text
infra/start.sh
infra/stop.sh
infra/recreate.sh
infra/status.sh
infra/logs.sh
infra/test.sh
```

### Interno

Scripts chamados pelos públicos.

Exemplos:

```text
infra/scripts/common.sh
infra/scripts/quorum.sh
infra/scripts/images.sh
infra/kubernetes/scripts/apply.sh
infra/kubernetes/scripts/wait.sh
```

### Legado temporário

Wrappers antigos que ainda existem para não quebrar chamadas.

Regra:

- Wrapper não pode ter lógica.
- Wrapper só chama script novo.
- Wrapper deve ser removido depois.

### Removível

Qualquer script que:

- Não é chamado pelos scripts públicos.
- Não é necessário para subir/parar/recriar/status/logs/teste do quorum.
- Não é sensível.
- Não tem função clara.

## 12. O que fazer com MCP

MCP não deve ficar misturado com Kubernetes.

Mas também não precisa virar uma arquitetura gigante.

Destino simples:

```text
infra/mcp/
  kerosene-mcp
  kerosene-readonly-mcp
  kerosene_mcp.py
  kerosene_readonly_mcp.py
  kerosene_server.py
  kerosene_work_tools.py
  k_more_tools.py
```

Ou:

```text
tools/mcp/
```

A escolha depende de como você enxerga MCP:

- Se MCP é ferramenta operacional da infra local, fica em `infra/mcp/`.
- Se MCP é ferramenta geral do repositório/agentes, fica em `tools/mcp/`.

O que não faz sentido é MCP morar em `infra/kubernetes/scripts/`, porque ele não é Kubernetes.

## 13. O que fazer com scripts locais atuais

Os scripts locais atuais devem ser reduzidos.

Em vez de muitos comandos públicos:

```text
start-local.sh
stop-local.sh
status-local.sh
logs-local.sh
capture-local-logs.sh
backup-local-db.sh
migrate-local-db.sh
repair-local-bitcoin-core.sh
recreate-local-mpc-sidecars.sh
```

A interface deve virar:

```text
infra/start.sh
infra/stop.sh
infra/recreate.sh
infra/status.sh
infra/logs.sh
```

Operações específicas podem virar subcomandos internos ou flags depois:

```bash
bash infra/status.sh
bash infra/logs.sh server
bash infra/recreate.sh mpc-sidecar
bash infra/recreate.sh --clean
```

Mas a primeira meta é não expor tudo como script separado.

## 14. Plano de ajuste por fases

### Fase 1 — corrigir a direção

Objetivo: parar de tratar `infra/` como plataforma final.

Ações:

- Manter este documento como referência curta.
- Atualizar `infra/README.md` para dizer que `infra/` é simulador de quorum local.
- Declarar os scripts públicos finais.
- Parar de adicionar novos scripts públicos.

### Fase 2 — criar os scripts públicos simples

Criar:

```text
infra/start.sh
infra/stop.sh
infra/recreate.sh
infra/status.sh
infra/logs.sh
infra/test.sh
```

Inicialmente eles podem apenas chamar os scripts existentes.

Exemplo:

```text
infra/start.sh -> chama o deploy/start atual
infra/stop.sh -> chama o stop atual
infra/status.sh -> chama o status atual
```

A primeira etapa não precisa reescrever tudo. Ela só cria uma interface limpa.

### Fase 3 — transformar scripts antigos em internos

Depois que a interface simples funcionar:

- Remover documentação dos scripts antigos.
- Marcar scripts antigos como internos ou legados.
- Fazer agentes usarem somente `infra/start.sh`, `infra/stop.sh`, `infra/recreate.sh`, `infra/status.sh`, `infra/logs.sh`.

### Fase 4 — mover MCP para fora de Kubernetes

Mover MCP para:

```text
infra/mcp/
```

ou:

```text
tools/mcp/
```

Manter wrapper temporário se necessário.

### Fase 5 — reduzir Kubernetes ao quorum local

Consolidar a intenção em:

```text
infra/kubernetes/quorum/
```

O que for staging/production/HA definitivo deve sair do escopo agora, ou ser marcado como futuro/experimental.

### Fase 6 — apagar duplicatas

Depois que os scripts públicos simples funcionarem:

- Buscar chamadas antigas.
- Remover wrappers não usados.
- Apagar scripts duplicados.
- Manter apenas scripts internos realmente chamados.

## 15. Decisão arquitetural principal

A decisão mais importante é esta:

> `infra/` é laboratório de quorum, não plataforma final dos serviços.

Isso significa que a pergunta para cada arquivo deve ser:

> Esse arquivo ajuda a simular todos os serviços juntos localmente?

Se sim, ele pode ficar.

Se não, ele deve sair, virar interno, virar ferramenta separada ou ser apagado.

## 16. Resultado esperado

Depois do ajuste, o operador deve conseguir fazer tudo com poucos comandos:

```bash
bash infra/start.sh
bash infra/status.sh
bash infra/logs.sh
bash infra/stop.sh
bash infra/recreate.sh
```

E deve ficar claro que:

- `infra/` não contém código de serviço.
- `infra/` não é dono definitivo do Kubernetes de cada app.
- `infra/` só junta os serviços para testar quorum local.
- Cada app futuramente terá sua imagem e seu Kubernetes próprios.
- Scripts antigos não são API pública.

Esse é o desenho simples e correto para o momento atual da Kerosene.
