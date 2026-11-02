# Renovação mTLS na instalação nativa

`renew_mtls.py` atende à instalação `native-local`. Reutiliza as chaves e a CA
existentes, valida estritamente a cadeia e as identidades e guarda backups
privados. A rotina não substitui a CA nem altera shares ou signers.

O timer `kerosene-certificate-renewal.timer` verifica os certificados a cada
quinze minutos. As folhas duram 24 horas e são renovadas quando qualquer uma
está a menos de oito horas do vencimento. Uma máquina desligada até os
certificados expirarem exige recuperação operacional: a rotina automática
recusa conexões e precondições inválidas, sem desabilitar a validação TLS.

Antes de cada reinício, os três Nodes devem comprovar quórum com três membros
vivos, e os três Vaults devem responder com `financial_ready=true`. Peers apenas
configurados não comprovam disponibilidade. Cada par Vault/Node é reiniciado
separadamente, com nova verificação de saúde antes de avançar.

Depois, a rotina atualiza o certificado do KFE no Secret e em sua fonte
operacional, com verificação de concorrência por `resourceVersion`. O rollout
usa a identidade GitOps autorizada pelo admission controller. O KFE atual tem
uma única réplica com estratégia `Recreate`: esse rollout causa uma interrupção
temporária. Um journal privado permite retomar uma publicação interrompida.

São necessários Python 3, PyYAML, OpenSSL, Docker e kubectl com o contexto
`kind-kerosene-local`. Os testes usam certificados temporários e simulam Docker
e Kubernetes; não executam transações financeiras:

```sh
python3 -m unittest discover -s infra/native -p 'test_*.py' -v
```

`--force` inicia uma rotação mesmo com certificados ainda frescos. Mantém todas
as precondições de saúde e segurança, e deve ser usado em uma janela compatível
com o reinício do KFE.
