"""Modelo ORM para TB_LIVROAPIS (chaves de API externas)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.repositories.database import Base


class LivroAPI(Base):
    """Mapeia a tabela TB_LIVROAPIS."""

    __tablename__ = "TB_LIVROAPIS"

    cd_sequencial: Mapped[int] = mapped_column(
        "CD_SEQUENCIAL", BigInteger, primary_key=True, autoincrement=True
    )
    tx_key: Mapped[str] = mapped_column("TX_KEY", Text, unique=True, nullable=False)
    dt_expiracao: Mapped[datetime | None] = mapped_column("DT_EXPIRACAO", DateTime)

    # Extensoes
    tx_nome: Mapped[str | None] = mapped_column("TX_NOME", Text)
    tx_servico: Mapped[str | None] = mapped_column("TX_SERVICO", Text)
    fl_ativo: Mapped[str | None] = mapped_column("FL_ATIVO", Text, default="S")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LivroAPI cd={self.cd_sequencial} servico='{self.tx_servico}'>"
