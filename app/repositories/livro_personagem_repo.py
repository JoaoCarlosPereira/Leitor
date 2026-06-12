"""Repositório CRUD para personagens de livros (TB_LIVROPERSONAGENS)."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.repositories.models.livro_personagem import LivroPersonagem

logger = logging.getLogger(__name__)


class LivroPersonagemRepositorio:
    """Operações de persistência para LivroPersonagem.

    Esta classe NAO faz commit das transações. O caller deve envolver
    o uso em `session_scope()` (ou gerenciar o commit manualmente).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------
    def salvar(self, personagem: LivroPersonagem) -> LivroPersonagem:
        """Persiste um personagem (insert ou update) e faz flush + refresh."""
        try:
            self._session.add(personagem)
            self._session.flush()
            self._session.refresh(personagem)
            return personagem
        except Exception:
            logger.exception("Erro ao salvar personagem")
            self._session.rollback()
            raise

    def salvar_em_lote(
        self, personagens: Sequence[LivroPersonagem]
    ) -> list[LivroPersonagem]:
        """Insere/atualiza múltiplos personagens em batch (bulk insert)."""
        try:
            if not personagens:
                return []
            self._session.add_all(personagens)
            self._session.flush()
            for p in personagens:
                self._session.refresh(p)
            return list(personagens)
        except Exception:
            logger.exception("Erro ao salvar personagens em lote")
            self._session.rollback()
            raise

    def renomear(self, personagem_id: int, novo_nome: str) -> None:
        """Atualiza o nome (tx_personagem) de um personagem existente."""
        try:
            personagem = self.buscar_por_id(personagem_id)
            if personagem is None:
                return
            personagem.tx_personagem = novo_nome
            self._session.flush()
        except Exception:
            logger.exception("Erro ao renomear personagem id=%s", personagem_id)
            self._session.rollback()
            raise

    def remover(self, personagem_id: int) -> None:
        """Remove um personagem do banco (cascade apaga falas associadas)."""
        try:
            personagem = self.buscar_por_id(personagem_id)
            if personagem is None:
                return
            self._session.delete(personagem)
            self._session.flush()
        except Exception:
            logger.exception("Erro ao remover personagem id=%s", personagem_id)
            self._session.rollback()
            raise

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------
    def buscar_por_id(self, personagem_id: int) -> LivroPersonagem | None:
        """Busca um personagem pelo seu ID."""
        try:
            stmt = select(LivroPersonagem).where(
                LivroPersonagem.cd_sequencial == personagem_id
            )
            return self._session.execute(stmt).scalar_one_or_none()
        except Exception:
            logger.exception("Erro ao buscar personagem id=%s", personagem_id)
            self._session.rollback()
            raise

    def buscar_por_nome(
        self, livro_id: int, nome: str
    ) -> LivroPersonagem | None:
        """Busca o primeiro personagem do livro com o nome exato (case-insensitive)."""
        try:
            stmt = (
                select(LivroPersonagem)
                .where(
                    LivroPersonagem.cd_sequenciallivro == livro_id,
                    func.lower(LivroPersonagem.tx_personagem) == nome.lower(),
                )
                .limit(1)
            )
            return self._session.execute(stmt).scalar_one_or_none()
        except Exception:
            logger.exception(
                "Erro ao buscar personagem por nome (livro_id=%s, nome=%s)",
                livro_id,
                nome,
            )
            self._session.rollback()
            raise

    def listar_por_livro(self, livro_id: int) -> list[LivroPersonagem]:
        """Lista todos os personagens de um livro, ordenados por nome."""
        try:
            stmt = (
                select(LivroPersonagem)
                .where(LivroPersonagem.cd_sequenciallivro == livro_id)
                .order_by(LivroPersonagem.tx_personagem.asc())
            )
            return list(self._session.execute(stmt).scalars().all())
        except Exception:
            logger.exception(
                "Erro ao listar personagens do livro id=%s", livro_id
            )
            self._session.rollback()
            raise

    def contar_falas(self, personagem_id: int) -> int:
        """Conta quantas falas estão associadas ao personagem."""
        try:
            stmt = (
                select(func.count())
                .select_from(LivroPersonagem)
                .where(LivroPersonagem.cd_sequencial == personagem_id)
            )
            return int(self._session.execute(stmt).scalar() or 0)
        except Exception:
            logger.exception(
                "Erro ao contar falas do personagem id=%s", personagem_id
            )
            self._session.rollback()
            raise

    def salvar_varios(
        self, personagens: Sequence[LivroPersonagem]
    ) -> list[LivroPersonagem]:
        """Insere vários personagens em batch."""
        try:
            if not personagens:
                return []
            self._session.add_all(personagens)
            self._session.flush()
            for p in personagens:
                self._session.refresh(p)
            return list(personagens)
        except Exception:
            logger.exception("Erro ao salvar personagens em lote")
            self._session.rollback()
            raise

    def atualizar(self, personagem_id: int, **kwargs) -> None:
        """Atualiza campos dinamicamente em um personagem."""
        try:
            p = self.buscar_por_id(personagem_id)
            if p is None:
                return
            for key, value in kwargs.items():
                if hasattr(p, key):
                    setattr(p, key, value)
            self._session.flush()
        except Exception:
            logger.exception("Erro ao atualizar personagem id=%s", personagem_id)
            self._session.rollback()
            raise

    def listar_nomes_unicos(self, livro_id: int) -> list[str]:
        """Retorna nomes distintos de personagens do livro."""
        try:
            stmt = (
                select(LivroPersonagem.tx_personagem)
                .where(LivroPersonagem.cd_sequenciallivro == livro_id)
                .distinct()
                .order_by(LivroPersonagem.tx_personagem.asc())
            )
            result = self._session.execute(stmt).scalars().all()
            return [n for n in result if n is not None]
        except Exception:
            logger.exception(
                "Erro ao listar nomes unicos do livro id=%s", livro_id
            )
            self._session.rollback()
            raise

    def contar_por_livro(self, livro_id: int) -> int:
        """Conta personagens de um livro."""
        try:
            stmt = (
                select(func.count())
                .select_from(LivroPersonagem)
                .where(LivroPersonagem.cd_sequenciallivro == livro_id)
            )
            return int(self._session.execute(stmt).scalar() or 0)
        except Exception:
            logger.exception(
                "Erro ao contar personagens do livro id=%s", livro_id
            )
            self._session.rollback()
            raise

    def mesclar(self, origem_id: int, destino_id: int) -> None:
        """Move falas do personagem origem para o destino, depois remove origem.

        Se origem == destino, não faz nada.
        """
        if origem_id == destino_id:
            return
        try:
            from app.repositories.models.livro_fala import LivroFala
            # Atualiza falas da origem para apontar ao destino
            stmt = select(LivroFala).where(LivroFala.cd_sequencialpersonagem == origem_id)
            falas = list(self._session.execute(stmt).scalars())
            for f in falas:
                f.cd_sequencialpersonagem = destino_id
            self._session.flush()
            # Remove a origem
            self.remover(origem_id)
        except Exception:
            logger.exception(
                "Erro ao mesclar personagens origem=%s destino=%s",
                origem_id, destino_id,
            )
            self._session.rollback()
            raise
