# Kerosene Docker Compose

Este diretório é o destino canônico dos arquivos Compose da Kerosene.

## Estado de transição

Os Compose files canônicos já foram transferidos para este diretório. Os paths
internos foram reescritos para a nova localização e os builds principais passam
pelos Dockerfiles de `infra/docker/images`.

Arquivos canônicos:

```text
infra/docker/compose/vault-mesh-lab.compose.yaml      # deploy.sh settlement (testnet3 clearnet)
infra/docker/compose/vault-mesh-staging.compose.yaml  # mTLS staging (opt-in)
infra/docker/compose/vault-mesh-tor.compose.yaml      # real Tor private mesh + distributed_wire
infra/docker/compose/vault-mesh-ceremony.compose.yaml # production-native flags (pair with Tor for go-live)
infra/docker/compose/local.limits.compose.yaml             # legacy override; not standalone
```

`bash infra/deploy.sh` defaults to **vault-mesh-lab** (kerosene-vault ×3, clearnet ports,
`dealer_lab`) and enables kfe vaultmesh (`mesh-only=true`). Legacy `local.compose.yaml` /
`mpc-sidecar` compose paths are removed.

Optional Tor mesh from the same deploy path:

```bash
KEROSENE_VAULT_MESH_PROFILE=tor bash infra/deploy.sh --wait
```

That starts `vault-mesh-tor.compose.yaml` (`distributed_wire`, no clearnet vault ports).
For a full over-wire DKG exercise, use the script owned by the independent
`kerosene-vault` repository:

```bash
export KEROSENE_VAULT_DIR=/path/to/kerosene-vault
bash "$KEROSENE_VAULT_DIR/scripts/lab_dkg_wire_tor.sh"
```

See `docs/CEREMONY_TOR.md` in that repository.

A topologia `hardened` fica fora do repositório público (ops privado).

Os caminhos legados continuam presentes para compatibilidade com scripts antigos.
`local.limits.compose.yaml` ainda aponta para serviços do Compose local removido;
não o use isoladamente nem com os Compose atuais do Vault Mesh.

## Wrappers de compatibilidade

Estes wrappers continuam executáveis para não quebrar fluxos locais antigos,
mas automações novas devem usar `infra/start.sh`:

```bash
bash infra/docker/scripts/compose-local.sh up -d
bash infra/docker/scripts/compose-local.sh ps
bash infra/docker/scripts/compose-local-kfe.sh up -d
```

`compose-local-kfe.sh` hoje é apenas um alias da composição Vault Mesh. Ele não
inicia o KFE; o nome permanece somente por compatibilidade.

## Próxima etapa

Migrar consumidores desses wrappers para `infra/start.sh` e remover os Compose
legados apenas depois de uma janela de validação.
