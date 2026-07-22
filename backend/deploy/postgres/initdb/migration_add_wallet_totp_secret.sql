-- ============================================================
-- Migration: Adicionar coluna totp_secret em financial.wallets
-- Problema: O banco foi criado com uma versão antiga do init.sql
--           que não tinha a coluna totp_secret.
--           O WalletEntity agora mapeia essa coluna e o Hibernate
--           falha com: ERROR: column we1_0.totp_secret does not exist
--
-- Rodar em TODOS os nós PostgreSQL:
--   docker exec kerosene_db_se psql -U $POSTGRES_USER -d kerosene -f /migration.sql
--   docker exec kerosene_db_ee psql -U $POSTGRES_USER -d kerosene -f /migration.sql
--   docker exec kerosene_db_is psql -U $POSTGRES_USER -d kerosene -f /migration.sql
--
-- Ou via psql direto:
--   psql -h <host> -U <user> -d kerosene -f migration.sql
-- ============================================================

-- ADD COLUMN IF NOT EXISTS é idempotente — seguro rodar múltiplas vezes
ALTER TABLE financial.wallets
    ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(255) NOT NULL DEFAULT '';

-- Verificar resultado:
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'financial'
  AND table_name   = 'wallets'
  AND column_name  = 'totp_secret';
