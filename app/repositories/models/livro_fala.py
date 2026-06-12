"""Modelo ORM para TB_LIVROFALAS (falas/narração extraídas)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.repositories.database import Base

if TYPE_CHECKING:
    from app.repositories.models.livro_cabecalho import LivroCabecalho
    from app.repositories.models.livro_pagina import LivroPagina
    from app.repositories.models.livro_personagem import LivroPersonagem


class LivroFala(Base):
    """Mapeia a tabela TB_LIVROFALAS."""

    __tablename__ = "TB_LIVROFALAS"

    cd_sequencial: Mapped[int] = mapped_column(
        "CD_SEQUENCIAL", BigInteger, primary_key=True, autoincrement=True
    )
    cd_sequenciallivro: Mapped[int] = mapped_column(
        "CD_SEQUENCIALLIVRO",
        BigInteger,
        ForeignKey("TB_LIVROCABECALHO.CD_SEQUENCIAL"),
        nullable=False,
    )
    cd_sequencialpagina: Mapped[int] = mapped_column(
        "CD_SEQUENCIALPAGINA",
        BigInteger,
        ForeignKey("TB_LIVROPAGINA.CD_SEQUENCIAL"),
        nullable=False,
    )
    cd_sequencialpersonagem: Mapped[int] = mapped_column(
        "CD_SEQUENCIALPERSONAGEM",
        BigInteger,
        ForeignKey("TB_LIVROPERSONAGENS.CD_SEQUENCIAL"),
        nullable=False,
    )
    tx_fala: Mapped[str | None] = mapped_column("TX_FALA", Text)
    fl_processado: Mapped[str | None] = mapped_column("FL_PROCESSADO", Text)
    dt_manutencao: Mapped[datetime | None] = mapped_column(
        "DT_MANUTECAO", DateTime, server_default=func.current_timestamp()
    )

    # Extensoes para audio e emocoes
    nr_ordem: Mapped[int | None] = mapped_column("NR_ORDEM", BigInteger)
    tx_instrucao_emocao: Mapped[str | None] = mapped_column("TX_INSTRUCAO_EMOCAO", Text)
    tx_instrucao_prosodia: Mapped[str | None] = mapped_column("TX_INSTRUCAO_PROSODIA", Text)
    tx_instrucao_paralinguistica: Mapped[str | None] = mapped_column(
        "TX_INSTRUCAO_PARALINGUISTICA", Text
    )
    caminho_audio_chunk: Mapped[str | None] = mapped_column("TX_CAMINHO_AUDIO_CHUNK", Text)
    nr_chunk: Mapped[int | None] = mapped_column("NR_CHUNK", BigInteger)
    fl_rejeitado: Mapped[str | None] = mapped_column("FL_REJEITADO", Text)
    eh_narracao: Mapped[str | None] = mapped_column("FL_EH_NARRACAO", Text)

    livro: Mapped["LivroCabecalho"] = relationship("LivroCabecalho", back_populates="falas")
    pagina: Mapped["LivroPagina"] = relationship("LivroPagina", back_populates="falas")
    personagem: Mapped["LivroPersonagem"] = relationship(
        "LivroPersonagem", back_populates="falas"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LivroFala cd={self.cd_sequencial} personagem={self.cd_sequencialpersonagem}>"
