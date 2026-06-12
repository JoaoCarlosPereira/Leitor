"""Cria o schema base do sistema Leitor.

Revision ID: 001_criar_schema_base
Revises:
Create Date: 2026-06-11

Cria as 5 tabelas originais do projeto, baseadas em
``sql/script_base_estrutura.sql``:

* ``TB_LIVROCABECALHO``   — cabeçalho dos livros.
* ``TB_LIVROPAGINA``      — páginas extraídas dos livros.
* ``TB_LIVROPERSONAGENS`` — personagens identificados.
* ``TB_LIVROFALAS``       — falas/narração extraídas.
* ``TB_LIVROAPIS``        — chaves de APIs externas.

Cria também os 4 índices de desempenho recomendados no script SQL.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_criar_schema_base"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Cria as 5 tabelas base e os índices de desempenho."""
    # Tabela de cabeçalho dos livros (TB_LIVROCABECALHO).
    op.create_table(
        "TB_LIVROCABECALHO",
        sa.Column(
            "CD_SEQUENCIAL",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column("TX_TITULO", sa.Text(), nullable=True, unique=True),
        sa.Column("FL_LIDO", sa.Text(), nullable=True),
        sa.Column("FL_NORMALIZADO", sa.Text(), nullable=True),
        sa.Column("FL_NARRADOR", sa.Text(), nullable=True),
        sa.Column("FL_PRODUZIDO", sa.Text(), nullable=True),
        sa.Column(
            "DT_MANUTECAO",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=True,
        ),
    )

    # Tabela de páginas dos livros (TB_LIVROPAGINA).
    op.create_table(
        "TB_LIVROPAGINA",
        sa.Column(
            "CD_SEQUENCIAL",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column(
            "CD_SEQUENCIALLIVRO",
            sa.BigInteger(),
            sa.ForeignKey("TB_LIVROCABECALHO.CD_SEQUENCIAL"),
            nullable=False,
        ),
        sa.Column("NR_PAGINA", sa.BigInteger(), nullable=True),
        sa.Column("TX_PAGINA", sa.Text(), nullable=True),
        sa.Column("FL_PROCESSADO", sa.Text(), nullable=True),
        sa.Column(
            "DT_MANUTECAO",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=True,
        ),
    )

    # Tabela de personagens (TB_LIVROPERSONAGENS).
    op.create_table(
        "TB_LIVROPERSONAGENS",
        sa.Column(
            "CD_SEQUENCIAL",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column(
            "CD_SEQUENCIALLIVRO",
            sa.BigInteger(),
            sa.ForeignKey("TB_LIVROCABECALHO.CD_SEQUENCIAL"),
            nullable=False,
        ),
        sa.Column("TX_PERSONAGEM", sa.Text(), nullable=True),
        sa.Column("TX_GENERO", sa.Text(), nullable=True),
        sa.Column("TX_IDADE", sa.Text(), nullable=True),
        sa.Column("CD_VOZ", sa.BigInteger(), nullable=True),
        sa.Column(
            "DT_MANUTECAO",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=True,
        ),
    )

    # Tabela de falas/narração (TB_LIVROFALAS).
    op.create_table(
        "TB_LIVROFALAS",
        sa.Column(
            "CD_SEQUENCIAL",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column(
            "CD_SEQUENCIALLIVRO",
            sa.BigInteger(),
            sa.ForeignKey("TB_LIVROCABECALHO.CD_SEQUENCIAL"),
            nullable=False,
        ),
        sa.Column(
            "CD_SEQUENCIALPAGINA",
            sa.BigInteger(),
            sa.ForeignKey("TB_LIVROPAGINA.CD_SEQUENCIAL"),
            nullable=False,
        ),
        sa.Column(
            "CD_SEQUENCIALPERSONAGEM",
            sa.BigInteger(),
            sa.ForeignKey("TB_LIVROPERSONAGENS.CD_SEQUENCIAL"),
            nullable=False,
        ),
        sa.Column("TX_FALA", sa.Text(), nullable=True),
        sa.Column("FL_PROCESSADO", sa.Text(), nullable=True),
        sa.Column(
            "DT_MANUTECAO",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=True,
        ),
    )

    # Tabela de chaves de APIs externas (TB_LIVROAPIS).
    op.create_table(
        "TB_LIVROAPIS",
        sa.Column(
            "CD_SEQUENCIAL",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column("TX_KEY", sa.Text(), nullable=False, unique=True),
        sa.Column("DT_EXPIRACAO", sa.DateTime(), nullable=True),
    )

    # Índices de desempenho.
    op.create_index(
        "idx_livropagina_livro",
        "TB_LIVROPAGINA",
        ["CD_SEQUENCIALLIVRO"],
    )
    op.create_index(
        "idx_personagens_livro",
        "TB_LIVROPERSONAGENS",
        ["CD_SEQUENCIALLIVRO"],
    )
    op.create_index(
        "idx_falas_livro_pagina",
        "TB_LIVROFALAS",
        ["CD_SEQUENCIALLIVRO", "CD_SEQUENCIALPAGINA"],
    )
    op.create_index(
        "idx_falas_personagem",
        "TB_LIVROFALAS",
        ["CD_SEQUENCIALPERSONAGEM"],
    )


def downgrade() -> None:
    """Remove os índices e as 5 tabelas base (ordem inversa à criação)."""
    op.drop_index("idx_falas_personagem", table_name="TB_LIVROFALAS")
    op.drop_index("idx_falas_livro_pagina", table_name="TB_LIVROFALAS")
    op.drop_index("idx_personagens_livro", table_name="TB_LIVROPERSONAGENS")
    op.drop_index("idx_livropagina_livro", table_name="TB_LIVROPAGINA")

    op.drop_table("TB_LIVROFALAS")
    op.drop_table("TB_LIVROPERSONAGENS")
    op.drop_table("TB_LIVROPAGINA")
    op.drop_table("TB_LIVROAPIS")
    op.drop_table("TB_LIVROCABECALHO")
