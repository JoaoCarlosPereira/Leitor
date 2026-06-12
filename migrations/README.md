# Migrations Alembic

## Comandos úteis

```bash
# Aplicar todas as migrations
alembic upgrade head

# Reverter todas as migrations
alembic downgrade base

# Verificar versão atual
alembic current

# Gerar nova migration (autogenerate)
alembic revision --autogenerate -m "descricao_da_migration"

# Criar migration manual
alembic revision -m "descricao_da_migration"
```

## Estrutura

- `001_criar_schema_base.py` — Cria as 5 tabelas originais do script SQL
- `002_extender_livrocabecalho_pipeline.py` — Adiciona colunas de pipeline e extensões
