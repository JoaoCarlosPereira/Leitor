"""Repositório CRUD para páginas de livros (TB_LIVROPAGINA)."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.repositories.models.livro_pagina import LivroPagina

logger = logging.getLogger(__name__)


class LivroPaginaRepositorio:
    """Operacoes de persistencia para LivroPagina.

    Esta classe NAO faz commit. O caller deve usar `session_scope()`.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------
    def salvar_paginas(self, livro_id: int, paginas: list[LivroPagina]) -> int:
        """Insere multiplas paginas em batch para um livro.

        Se as instancias nao tiverem `cd_sequenciallivro` definido,
        e atribuido o livro_id recebido. Nao faz commit.

        Args:
            livro_id: ID do livro dono das paginas.
            paginas: Lista de instancias de LivroPagina.

        Returns:
            Quantidade de paginas inseridas/atualizadas.
        """
        try:
            if not paginas:
                return 0
            for p in paginas:
                if p.cd_sequenciallivro is None:
                    p.cd_sequenciallivro = livro_id
            self._session.add_all(paginas)
            self._session.flush()
            return len(paginas)
        except Exception:
            logger.exception("Erro ao salvar paginas em batch (livro_id=%s)", livro_id)
            self._session.rollback()
            raise

    def marcar_processada(self, pagina_id: int) -> None:
        """Marca uma pagina como processada (fl_processado = 'S')."""
        try:
            pagina = self.buscar_por_id(pagina_id)
            if pagina is None:
                return
            pagina.fl_processado = "S"
            self._session.flush()
        except Exception:
            logger.exception("Erro ao marcar pagina id=%s como processada", pagina_id)
            self._session.rollback()
            raise

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------
    def listar_por_livro(self, livro_id: int) -> list[LivroPagina]:
        """Lista todas as paginas de um livro, ordenadas por nr_pagina."""
        try:
            stmt = (
                select(LivroPagina)
                .where(LivroPagina.cd_sequenciallivro == livro_id)
                .order_by(LivroPagina.nr_pagina.asc())
            )
            return list(self._session.execute(stmt).scalars().all())
        except Exception:
            logger.exception("Erro ao listar paginas do livro id=%s", livro_id)
            self._session.rollback()
            raise

    def listar_nao_processadas(self, livro_id: int) -> list[LivroPagina]:
        """Lista paginas do livro que ainda nao foram processadas."""
        try:
            stmt = (
                select(LivroPagina)
                .where(
                    LivroPagina.cd_sequenciallivro == livro_id,
                    (LivroPagina.fl_processado.is_(None)) | (LivroPagina.fl_processado != "S"),
                )
                .order_by(LivroPagina.nr_pagina.asc())
            )
            return list(self._session.execute(stmt).scalars().all())
        except Exception:
            logger.exception("Erro ao listar paginas nao processadas do livro id=%s", livro_id)
            self._session.rollback()
            raise

    def contar_total(self, livro_id: int) -> int:
        """Conta o total de paginas de um livro."""
        try:
            stmt = (
                select(func.count())
                .select_from(LivroPagina)
                .where(LivroPagina.cd_sequenciallivro == livro_id)
            )
            return int(self._session.execute(stmt).scalar() or 0)
        except Exception:
            logger.exception("Erro ao contar paginas do livro id=%s", livro_id)
            self._session.rollback()
            raise

    def buscar_por_id(self, pagina_id: int) -> LivroPagina | None:
        """Busca uma pagina pelo seu ID."""
        try:
            stmt = select(LivroPagina).where(LivroPagina.cd_sequencial == pagina_id)
            return self._session.execute(stmt).scalar_one_or_none()
        except Exception:
            logger.exception("Erro ao buscar pagina id=%s", pagina_id)
            self._session.rollback()
            raise
