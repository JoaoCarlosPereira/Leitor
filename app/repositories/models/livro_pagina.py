"""Modelo ORM para TB_LIVROPAGINA (páginas extraídas dos livros)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.repositories.database import Base

if TYPE_CHECKING:
    from app.repositories.models.livro_cabecalho import LivroCabecalho
    from app.repositories.models.livro_fala import LivroFala


class LivroPagina(Base):
    """Mapeia a tabela TB_LIVROPAGINA."""

    __tablename__ = "TB_LIVROPAGINA"

    cd_sequencial: Mapped[int] = mapped_column(
        "CD_SEQUENCIAL", BigInteger, primary_key=True, autoincrement=True
    )
    cd_sequenciallivro: Mapped[int] = mapped_column(
        "CD_SEQUENCIALLIVRO",
        BigInteger,
        ForeignKey("TB_LIVROCABECALHO.CD_SEQUENCIAL"),
        nullable=False,
    )
    nr_pagina: Mapped[int | None] = mapped_column("NR_PAGINA", BigInteger)
    tx_pagina: Mapped[str | None] = mapped_column("TX_PAGINA", Text)
    fl_processado: Mapped[str | None] = mapped_column("FL_PROCESSADO", Text)
    dt_manutencao: Mapped[datetime | None] = mapped_column(
        "DT_MANUTECAO", DateTime, server_default=func.current_timestamp()
    )

    livro: Mapped["LivroCabecalho"] = relationship("LivroCabecalho", back_populates="paginas")
    falas: Mapped[list["LivroFala"]] = relationship(
        "LivroFala", back_populates="pagina", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LivroPagina cd={self.cd_sequencial} nr={self.nr_pagina}>"
