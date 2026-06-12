"""Estende o schema base com colunas do pipeline de produção.

Revision ID: 002_livro_cabecalho_ext
Revises: 001_criar_schema_base
Create Date: 2026-06-11

Observação: o nome do arquivo é ``002_extender_livrocabecalho_pipeline.py``
(legibilidade humana), mas o ``revision_id`` é ``002_livro_cabecalho_ext``
porque a coluna ``alembic_version.version_num`` é ``VARCHAR(32)``.

Adiciona colunas de extensão ao schema base criado pela migration
``001_criar_schema_base``. Essas colunas dão suporte ao pipeline de
produção de audiolivros,Voice Design, fila de execução e armazenamento
de chunks de áudio gerados.

Tabelas afetadas:

* ``TB_LIVROCABECALHO``   — estado do pipeline, progresso, fila, metadados
  do PDF/áudio final e autor.
* ``TB_LIVROPERSONAGENS`` — instruções de estilo, voz de referência,
  origem da voz e flags de aprovação/narrador.
* ``TB_LIVROFALAS``       — ordem da fala, instruções de emoção/prosódia/
  paralinguística, caminho do chunk de áudio e flags.
* ``TB_LIVROAPIS``        — nome amigável, serviço e flag de ativação.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_livro_cabecalho_ext"
down_revision: str | None = "001_criar_schema_base"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Adiciona colunas de extensão às tabelas base."""
    # --- TB_LIVROCABECALHO -----------------------------------------------
    op.add_column(
        "TB_LIVROCABECALHO",
        sa.Column(
            "TX_ESTADO_PIPELINE",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'aguardando'"),
        ),
    )
    op.add_column(
        "TB_LIVROCABECALHO",
        sa.Column(
            "PROGRESSO_ATUAL",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "TB_LIVROCABECALHO",
        sa.Column(
            "PROGRESSO_TOTAL",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("6"),
        ),
    )
    op.add_column(
        "TB_LIVROCABECALHO",
        sa.Column("DT_INICIO", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "TB_LIVROCABECALHO",
        sa.Column("DT_CONCLUSAO", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "TB_LIVROCABECALHO",
        sa.Column("ERRO_MENSAGEM", sa.Text(), nullable=True),
    )
    op.add_column(
        "TB_LIVROCABECALHO",
        sa.Column("FILA_POSICAO", sa.Integer(), nullable=True),
    )
    op.add_column(
        "TB_LIVROCABECALHO",
        sa.Column(
            "FILA_PAUSADO",
            sa.String(length=1),
            nullable=False,
            server_default=sa.text("'N'"),
        ),
    )
    op.add_column(
        "TB_LIVROCABECALHO",
        sa.Column("TX_AUTOR", sa.Text(), nullable=True),
    )
    op.add_column(
        "TB_LIVROCABECALHO",
        sa.Column("TX_CAMINHO_PDF", sa.Text(), nullable=True),
    )
    op.add_column(
        "TB_LIVROCABECALHO",
        sa.Column("TX_CAMINHO_AUDIO", sa.Text(), nullable=True),
    )

    # --- TB_LIVROPAGINA --------------------------------------------------
    # (nenhuma coluna de extensão — campos base são suficientes)

    # --- TB_LIVROPERSONAGENS ---------------------------------------------
    op.add_column(
        "TB_LIVROPERSONAGENS",
        sa.Column("TX_INSTRUCAO_ESTILO", sa.Text(), nullable=True),
    )
    op.add_column(
        "TB_LIVROPERSONAGENS",
        sa.Column("TX_VOZ_REFERENCIA", sa.Text(), nullable=True),
    )
    op.add_column(
        "TB_LIVROPERSONAGENS",
        sa.Column("TX_VOZ_ORIGEM", sa.Text(), nullable=True),
    )
    op.add_column(
        "TB_LIVROPERSONAGENS",
        sa.Column("FL_VOZ_APROVADA", sa.String(length=1), nullable=True),
    )
    op.add_column(
        "TB_LIVROPERSONAGENS",
        sa.Column("FL_EH_NARRADOR", sa.String(length=1), nullable=True),
    )

    # --- TB_LIVROFALAS ---------------------------------------------------
    op.add_column(
        "TB_LIVROFALAS",
        sa.Column("NR_ORDEM", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "TB_LIVROFALAS",
        sa.Column("TX_INSTRUCAO_EMOCAO", sa.Text(), nullable=True),
    )
    op.add_column(
        "TB_LIVROFALAS",
        sa.Column("TX_INSTRUCAO_PROSODIA", sa.Text(), nullable=True),
    )
    op.add_column(
        "TB_LIVROFALAS",
        sa.Column("TX_INSTRUCAO_PARALINGUISTICA", sa.Text(), nullable=True),
    )
    op.add_column(
        "TB_LIVROFALAS",
        sa.Column("TX_CAMINHO_AUDIO_CHUNK", sa.Text(), nullable=True),
    )
    op.add_column(
        "TB_LIVROFALAS",
        sa.Column("NR_CHUNK", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "TB_LIVROFALAS",
        sa.Column("FL_REJEITADO", sa.String(length=1), nullable=True),
    )
    op.add_column(
        "TB_LIVROFALAS",
        sa.Column("FL_EH_NARRACAO", sa.String(length=1), nullable=True),
    )

    # --- TB_LIVROAPIS ----------------------------------------------------
    op.add_column(
        "TB_LIVROAPIS",
        sa.Column("TX_NOME", sa.Text(), nullable=True),
    )
    op.add_column(
        "TB_LIVROAPIS",
        sa.Column("TX_SERVICO", sa.Text(), nullable=True),
    )
    op.add_column(
        "TB_LIVROAPIS",
        sa.Column(
            "FL_ATIVO",
            sa.String(length=1),
            nullable=False,
            server_default=sa.text("'S'"),
        ),
    )


def downgrade() -> None:
    """Remove as colunas de extensão adicionadas no upgrade."""
    # --- TB_LIVROAPIS ----------------------------------------------------
    op.drop_column("TB_LIVROAPIS", "FL_ATIVO")
    op.drop_column("TB_LIVROAPIS", "TX_SERVICO")
    op.drop_column("TB_LIVROAPIS", "TX_NOME")

    # --- TB_LIVROFALAS ---------------------------------------------------
    op.drop_column("TB_LIVROFALAS", "FL_EH_NARRACAO")
    op.drop_column("TB_LIVROFALAS", "FL_REJEITADO")
    op.drop_column("TB_LIVROFALAS", "NR_CHUNK")
    op.drop_column("TB_LIVROFALAS", "TX_CAMINHO_AUDIO_CHUNK")
    op.drop_column("TB_LIVROFALAS", "TX_INSTRUCAO_PARALINGUISTICA")
    op.drop_column("TB_LIVROFALAS", "TX_INSTRUCAO_PROSODIA")
    op.drop_column("TB_LIVROFALAS", "TX_INSTRUCAO_EMOCAO")
    op.drop_column("TB_LIVROFALAS", "NR_ORDEM")

    # --- TB_LIVROPERSONAGENS ---------------------------------------------
    op.drop_column("TB_LIVROPERSONAGENS", "FL_EH_NARRADOR")
    op.drop_column("TB_LIVROPERSONAGENS", "FL_VOZ_APROVADA")
    op.drop_column("TB_LIVROPERSONAGENS", "TX_VOZ_ORIGEM")
    op.drop_column("TB_LIVROPERSONAGENS", "TX_VOZ_REFERENCIA")
    op.drop_column("TB_LIVROPERSONAGENS", "TX_INSTRUCAO_ESTILO")

    # --- TB_LIVROPAGINA --------------------------------------------------
    # (nenhuma coluna a remover)

    # --- TB_LIVROCABECALHO -----------------------------------------------
    op.drop_column("TB_LIVROCABECALHO", "TX_CAMINHO_AUDIO")
    op.drop_column("TB_LIVROCABECALHO", "TX_CAMINHO_PDF")
    op.drop_column("TB_LIVROCABECALHO", "TX_AUTOR")
    op.drop_column("TB_LIVROCABECALHO", "FILA_PAUSADO")
    op.drop_column("TB_LIVROCABECALHO", "FILA_POSICAO")
    op.drop_column("TB_LIVROCABECALHO", "ERRO_MENSAGEM")
    op.drop_column("TB_LIVROCABECALHO", "DT_CONCLUSAO")
    op.drop_column("TB_LIVROCABECALHO", "DT_INICIO")
    op.drop_column("TB_LIVROCABECALHO", "PROGRESSO_TOTAL")
    op.drop_column("TB_LIVROCABECALHO", "PROGRESSO_ATUAL")
    op.drop_column("TB_LIVROCABECALHO", "TX_ESTADO_PIPELINE")
