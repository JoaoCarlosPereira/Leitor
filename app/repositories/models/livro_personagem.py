"""Modelo ORM para TB_LIVROPERSONAGENS (personagens do livro)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.repositories.database import Base

if TYPE_CHECKING:
    from app.repositories.models.livro_cabecalho import LivroCabecalho
    from app.repositories.models.livro_fala import LivroFala


class LivroPersonagem(Base):
    """Mapeia a tabela TB_LIVROPERSONAGENS."""

    __tablename__ = "TB_LIVROPERSONAGENS"

    cd_sequencial: Mapped[int] = mapped_column(
        "CD_SEQUENCIAL", BigInteger, primary_key=True, autoincrement=True
    )
    cd_sequenciallivro: Mapped[int] = mapped_column(
        "CD_SEQUENCIALLIVRO",
        BigInteger,
        ForeignKey("TB_LIVROCABECALHO.CD_SEQUENCIAL"),
        nullable=False,
    )
    tx_personagem: Mapped[str | None] = mapped_column("TX_PERSONAGEM", Text)
    tx_genero: Mapped[str | None] = mapped_column("TX_GENERO", Text)
    tx_idade: Mapped[str | None] = mapped_column("TX_IDADE", Text)
    cd_voz: Mapped[int | None] = mapped_column("CD_VOZ", BigInteger)
    dt_manutencao: Mapped[datetime | None] = mapped_column(
        "DT_MANUTECAO", DateTime, server_default=func.current_timestamp()
    )

    # Extensoes para Voice Design e instrucoes
    tx_instrucao_estilo: Mapped[str | None] = mapped_column("TX_INSTRUCAO_ESTILO", Text)
    tx_voz_referencia_path: Mapped[str | None] = mapped_column("TX_VOZ_REFERENCIA", Text)
    tx_voz_origem: Mapped[str | None] = mapped_column("TX_VOZ_ORIGEM", Text)  # catalogo|design
    fl_voz_aprovada: Mapped[str | None] = mapped_column("FL_VOZ_APROVADA", Text)
    fl_eh_narrador: Mapped[str | None] = mapped_column("FL_EH_NARRADOR", Text)

    livro: Mapped["LivroCabecalho"] = relationship(
        "LivroCabecalho", back_populates="personagens"
    )
    falas: Mapped[list["LivroFala"]] = relationship(
        "LivroFala", back_populates="personagem", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LivroPersonagem cd={self.cd_sequencial} "
            f"nome='{self.tx_personagem}' genero='{self.tx_genero}'>"
        )
