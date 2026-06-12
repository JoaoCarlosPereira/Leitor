"""Repositório CRUD para falas/narração de livros (TB_LIVROFALAS)."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.repositories.models.livro_fala import LivroFala

logger = logging.getLogger(__name__)


class LivroFalaRepositorio:
    """Operações de persistência para LivroFala.

    Esta classe NAO faz commit das transações. O caller deve envolver
    o uso em `session_scope()` (ou gerenciar o commit manualmente).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------
    def salvar(self, fala: LivroFala) -> LivroFala:
        """Persiste uma fala (insert ou update) e faz flush + refresh."""
        try:
            self._session.add(fala)
            self._session.flush()
            self._session.refresh(fala)
            return fala
        except Exception:
            logger.exception("Erro ao salvar fala")
            self._session.rollback()
            raise

    def salvar_em_lote(self, falas: Sequence[LivroFala]) -> list[LivroFala]:
        """Insere múltiplas falas em batch (bulk insert)."""
        try:
            if not falas:
                return []
            self._session.add_all(falas)
            self._session.flush()
            for f in falas:
                self._session.refresh(f)
            return list(falas)
        except Exception:
            logger.exception("Erro ao salvar falas em lote")
            self._session.rollback()
            raise

    def salvar_varias(self, falas: Sequence[LivroFala]) -> list[LivroFala]:
        """Alias para salvar_em_lote (compatibilidade)."""
        return self.salvar_em_lote(falas)

    def marcar_processada(self, fala_id: int) -> None:
        """Marca uma fala como processada (fl_processado = 'S')."""
        try:
            fala = self.buscar_por_id(fala_id)
            if fala is None:
                return
            fala.fl_processado = "S"
            self._session.flush()
        except Exception:
            logger.exception("Erro ao marcar fala id=%s como processada", fala_id)
            self._session.rollback()
            raise

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------
    def buscar_por_id(self, fala_id: int) -> LivroFala | None:
        """Busca uma fala pelo seu ID."""
        try:
            stmt = select(LivroFala).where(LivroFala.cd_sequencial == fala_id)
            return self._session.execute(stmt).scalar_one_or_none()
        except Exception:
            logger.exception("Erro ao buscar fala id=%s", fala_id)
            self._session.rollback()
            raise

    def listar_por_personagem(self, personagem_id: int) -> list[LivroFala]:
        """Lista falas de um personagem ordenadas por nr_ordem."""
        try:
            stmt = (
                select(LivroFala)
                .where(LivroFala.cd_sequencialpersonagem == personagem_id)
                .order_by(LivroFala.nr_ordem.asc())
            )
            return list(self._session.execute(stmt).scalars().all())
        except Exception:
            logger.exception(
                "Erro ao listar falas do personagem id=%s", personagem_id
            )
            self._session.rollback()
            raise

    def listar_por_pagina(self, pagina_id: int) -> list[LivroFala]:
        """Lista falas de uma página ordenadas por nr_ordem."""
        try:
            stmt = (
                select(LivroFala)
                .where(LivroFala.cd_sequencialpagina == pagina_id)
                .order_by(LivroFala.nr_ordem.asc())
            )
            return list(self._session.execute(stmt).scalars().all())
        except Exception:
            logger.exception(
                "Erro ao listar falas da pagina id=%s", pagina_id
            )
            self._session.rollback()
            raise

    def listar_por_livro(self, livro_id: int) -> list[LivroFala]:
        """Lista todas as falas de um livro ordenadas por nr_ordem."""
        try:
            stmt = (
                select(LivroFala)
                .where(LivroFala.cd_sequenciallivro == livro_id)
                .order_by(LivroFala.nr_ordem.asc())
            )
            return list(self._session.execute(stmt).scalars().all())
        except Exception:
            logger.exception("Erro ao listar falas do livro id=%s", livro_id)
            self._session.rollback()
            raise

    def contar_por_personagem(self, personagem_id: int) -> int:
        """Conta quantas falas o personagem possui."""
        try:
            stmt = (
                select(func.count())
                .select_from(LivroFala)
                .where(LivroFala.cd_sequencialpersonagem == personagem_id)
            )
            return int(self._session.execute(stmt).scalar() or 0)
        except Exception:
            logger.exception(
                "Erro ao contar falas do personagem id=%s", personagem_id
            )
            self._session.rollback()
            raise

    def proxima_nr_ordem(self, livro_id: int) -> int:
        """Calcula o próximo nr_ordem sequencial para o livro (max+1)."""
        try:
            stmt = select(func.max(LivroFala.nr_ordem)).where(
                LivroFala.cd_sequenciallivro == livro_id
            )
            max_ordem = self._session.execute(stmt).scalar()
            return (max_ordem or 0) + 1
        except Exception:
            logger.exception(
                "Erro ao calcular proximo nr_ordem do livro id=%s", livro_id
            )
            self._session.rollback()
            raise

    def listar_sem_emocao(self, livro_id: int) -> list[LivroFala]:
        """Lista falas do livro sem instrução de emoção definida."""
        try:
            stmt = (
                select(LivroFala)
                .where(
                    LivroFala.cd_sequenciallivro == livro_id,
                    (LivroFala.tx_instrucao_emocao.is_(None))
                    | (LivroFala.tx_instrucao_emocao == ""),
                )
                .order_by(LivroFala.nr_ordem)
            )
            return list(self._session.execute(stmt).scalars())
        except Exception:
            logger.exception(
                "Erro ao listar falas sem emocao do livro id=%s", livro_id
            )
            self._session.rollback()
            raise

    def listar_nao_processadas(self, livro_id: int) -> list[LivroFala]:
        """Lista falas do livro com fl_processado != 'S' (inclui NULL)."""
        try:
            stmt = (
                select(LivroFala)
                .where(
                    LivroFala.cd_sequenciallivro == livro_id,
                    (LivroFala.fl_processado.is_(None)) | (LivroFala.fl_processado != "S"),
                )
                .order_by(LivroFala.nr_ordem)
            )
            return list(self._session.execute(stmt).scalars())
        except Exception:
            logger.exception(
                "Erro ao listar falas nao processadas do livro id=%s", livro_id
            )
            self._session.rollback()
            raise

    def listar_rejeitadas(self, livro_id: int) -> list[LivroFala]:
        """Lista falas marcadas como rejeitadas."""
        try:
            stmt = (
                select(LivroFala)
                .where(
                    LivroFala.cd_sequenciallivro == livro_id,
                    LivroFala.fl_rejeitado == "S",
                )
                .order_by(LivroFala.nr_ordem)
            )
            return list(self._session.execute(stmt).scalars())
        except Exception:
            logger.exception(
                "Erro ao listar falas rejeitadas do livro id=%s", livro_id
            )
            self._session.rollback()
            raise

    def atualizar_fala(self, fala_id: int, **kwargs: Any) -> None:
        """Atualiza campos dinamicamente em uma fala."""
        try:
            fala = self.buscar_por_id(fala_id)
            if fala is None:
                return
            for key, value in kwargs.items():
                if hasattr(fala, key):
                    setattr(fala, key, value)
            self._session.flush()
        except Exception:
            logger.exception("Erro ao atualizar fala id=%s", fala_id)
            self._session.rollback()
            raise

    def marcar_rejeitada(self, fala_id: int) -> None:
        """Marca uma fala como rejeitada (fl_rejeitado = 'S')."""
        try:
            fala = self.buscar_por_id(fala_id)
            if fala is None:
                return
            fala.fl_rejeitado = "S"
            self._session.flush()
        except Exception:
            logger.exception("Erro ao marcar fala id=%s como rejeitada", fala_id)
            self._session.rollback()
            raise

    def salvar_instrucao_audio(
        self,
        fala_id: int,
        emocao: str,
        prosodia: str,
        paralinguistica: str,
    ) -> None:
        """Salva instrução de áudio (emoção, prosódia, paralinguística)."""
        try:
            fala = self.buscar_por_id(fala_id)
            if fala is None:
                return
            fala.tx_instrucao_emocao = emocao
            fala.tx_instrucao_prosodia = prosodia
            fala.tx_instrucao_paralinguistica = paralinguistica
            self._session.flush()
        except Exception:
            logger.exception("Erro ao salvar instrucao de audio da fala id=%s", fala_id)
            self._session.rollback()
            raise
