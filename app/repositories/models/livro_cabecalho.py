"""Modelo ORM para TB_LIVROCABECALHO (cabeçalho de livros)."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.repositories.database import Base

if TYPE_CHECKING:
    from app.repositories.models.livro_fala import LivroFala
    from app.repositories.models.livro_pagina import LivroPagina
    from app.repositories.models.livro_personagem import LivroPersonagem


class EstadoPipeline(str, enum.Enum):
    """Estados possíveis do pipeline de produção de um livro."""

    AGUARDANDO = "aguardando"
    EXTRACAO = "extracao"
    PERSONAGENS = "personagens"
    VOZES = "vozes"
    PRODUCAO = "producao"
    JUNCAO = "juncao"
    CONCLUIDO = "concluido"
    PAUSADO = "pausado"
    ERRO = "erro"


class LivroCabecalho(Base):
    """Mapeia a tabela TB_LIVROCABECALHO."""

    __tablename__ = "TB_LIVROCABECALHO"

    cd_sequencial: Mapped[int] = mapped_column(
        "CD_SEQUENCIAL", BigInteger, primary_key=True, autoincrement=True
    )
    tx_titulo: Mapped[str | None] = mapped_column("TX_TITULO", Text, unique=True)
    fl_lido: Mapped[str | None] = mapped_column("FL_LIDO", Text)
    fl_normalizado: Mapped[str | None] = mapped_column("FL_NORMALIZADO", Text)
    fl_narrador: Mapped[str | None] = mapped_column("FL_NARRADOR", Text)
    fl_produzido: Mapped[str | None] = mapped_column("FL_PRODUZIDO", Text)
    dt_manutencao: Mapped[datetime | None] = mapped_column(
        "DT_MANUTECAO", DateTime, server_default=func.current_timestamp()
    )

    # Colunas de extensao adicionadas pela task_03
    estado_pipeline: Mapped[str] = mapped_column(
        "TX_ESTADO_PIPELINE", Text, default=EstadoPipeline.AGUARDANDO.value
    )
    progresso_atual: Mapped[int] = mapped_column("PROGRESSO_ATUAL", Integer, default=0)
    progresso_total: Mapped[int] = mapped_column("PROGRESSO_TOTAL", Integer, default=6)
    dt_inicio: Mapped[datetime | None] = mapped_column("DT_INICIO", DateTime)
    dt_conclusao: Mapped[datetime | None] = mapped_column("DT_CONCLUSAO", DateTime)
    erro_mensagem: Mapped[str | None] = mapped_column("ERRO_MENSAGEM", Text)
    fila_posicao: Mapped[int | None] = mapped_column("FILA_POSICAO", Integer)
    fila_pausado: Mapped[str | None] = mapped_column("FILA_PAUSADO", String(1), default="N")
    autor: Mapped[str | None] = mapped_column("TX_AUTOR", Text)
    caminho_pdf: Mapped[str | None] = mapped_column("TX_CAMINHO_PDF", Text)
    caminho_audio_final: Mapped[str | None] = mapped_column("TX_CAMINHO_AUDIO", Text)

    # Relacionamentos
    paginas: Mapped[list["LivroPagina"]] = relationship(
        "LivroPagina", back_populates="livro", cascade="all, delete-orphan"
    )
    personagens: Mapped[list["LivroPersonagem"]] = relationship(
        "LivroPersonagem", back_populates="livro", cascade="all, delete-orphan"
    )
    falas: Mapped[list["LivroFala"]] = relationship(
        "LivroFala", back_populates="livro", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LivroCabecalho cd={self.cd_sequencial} "
            f"titulo='{self.tx_titulo}' estado='{self.estado_pipeline}'>"
        )
