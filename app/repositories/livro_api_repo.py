"""Repositório CRUD para chaves de API (TB_LIVROAPIS)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.repositories.models.livro_api import LivroAPI

logger = logging.getLogger(__name__)


class LivroAPIRepositorio:
    """Operacoes de persistencia para LivroAPI.

    Esta classe NAO faz commit. O caller deve usar `session_scope()`.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------
    def salvar_chave(
        self,
        tx_key: str,
        tx_nome: str,
        tx_servico: str,
        dt_expiracao: datetime | None = None,
    ) -> LivroAPI:
        """Cria e persiste uma nova chave de API.

        Args:
            tx_key: Valor da chave (token).
            tx_nome: Nome de exibicao da chave.
            tx_servico: Identificador do servico (ex: 'llm', 'tts').
            dt_expiracao: Data de expiracao (None = sem expiracao).

        Returns:
            A instancia persistida de LivroAPI.
        """
        try:
            api = LivroAPI(
                tx_key=tx_key,
                tx_nome=tx_nome,
                tx_servico=tx_servico,
                dt_expiracao=dt_expiracao,
                fl_ativo="S",
            )
            self._session.add(api)
            self._session.flush()
            self._session.refresh(api)
            return api
        except Exception:
            logger.exception("Erro ao salvar chave de API (servico=%s)", tx_servico)
            self._session.rollback()
            raise

    def remover_chave(self, chave_id: int) -> None:
        """Remove uma chave de API pelo seu ID."""
        try:
            stmt = select(LivroAPI).where(LivroAPI.cd_sequencial == chave_id)
            api = self._session.execute(stmt).scalar_one_or_none()
            if api is None:
                return
            self._session.delete(api)
            self._session.flush()
        except Exception:
            logger.exception("Erro ao remover chave de API id=%s", chave_id)
            self._session.rollback()
            raise

    def desativar(self, chave_id: int) -> None:
        """Marca uma chave como inativa (fl_ativo = 'N'), sem remove-la."""
        try:
            stmt = (
                update(LivroAPI)
                .where(LivroAPI.cd_sequencial == chave_id)
                .values(fl_ativo="N")
            )
            self._session.execute(stmt)
            self._session.flush()
        except Exception:
            logger.exception("Erro ao desativar chave de API id=%s", chave_id)
            self._session.rollback()
            raise

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------
    def listar(self, apenas_ativas: bool = True) -> list[LivroAPI]:
        """Lista chaves de API cadastradas.

        Args:
            apenas_ativas: Se True, retorna apenas chaves com fl_ativo='S'.

        Returns:
            Lista de LivroAPI ordenadas pelo servico e nome.
        """
        try:
            stmt = select(LivroAPI).order_by(
                LivroAPI.tx_servico.asc(), LivroAPI.tx_nome.asc()
            )
            if apenas_ativas:
                stmt = stmt.where(LivroAPI.fl_ativo == "S")
            return list(self._session.execute(stmt).scalars().all())
        except Exception:
            logger.exception("Erro ao listar chaves de API")
            self._session.rollback()
            raise

    def buscar_por_servico(self, servico: str) -> LivroAPI | None:
        """Busca a primeira chave ativa do servico informado.

        Args:
            servico: Identificador do servico (ex: 'llm', 'tts').

        Returns:
            Primeira LivroAPI ativa encontrada ou None.
        """
        try:
            stmt = (
                select(LivroAPI)
                .where(LivroAPI.tx_servico == servico, LivroAPI.fl_ativo == "S")
                .order_by(LivroAPI.cd_sequencial.asc())
            )
            return self._session.execute(stmt).scalars().first()
        except Exception:
            logger.exception("Erro ao buscar chave de API por servico=%s", servico)
            self._session.rollback()
            raise

    def verificar_validade(self, chave_id: int) -> bool:
        """Verifica se a chave ainda e valida (nao expirada).

        Returns:
            True se a chave existe e sua data de expiracao e None ou futura.
            False caso contrario (inexistente ou expirada).
        """
        try:
            stmt = select(LivroAPI).where(LivroAPI.cd_sequencial == chave_id)
            api = self._session.execute(stmt).scalar_one_or_none()
            if api is None:
                return False
            if api.dt_expiracao is None:
                return True
            # Comparacao considerando timezone: se dt_expiracao vier naive, trata como UTC
            exp = api.dt_expiracao
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            return exp > datetime.now(timezone.utc)
        except Exception:
            logger.exception("Erro ao verificar validade da chave id=%s", chave_id)
            self._session.rollback()
            raise
