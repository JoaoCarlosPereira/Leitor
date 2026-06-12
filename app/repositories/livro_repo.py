"""Repositório CRUD para livros (TB_LIVROCABECALHO)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.repositories.models.livro_cabecalho import EstadoPipeline, LivroCabecalho

logger = logging.getLogger(__name__)


class LivroRepositorio:
    """Operacoes de persistencia para LivroCabecalho.

    Esta classe NAO faz commit das transacoes. O caller deve envolver
    o uso em `session_scope()` (ou gerenciar o commit manualmente).
    Metodos que escrevem no banco realizam rollback em caso de erro.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------
    async def buscar_por_id(self, livro_id: int) -> LivroCabecalho | None:
        """Busca um livro pelo seu identificador.

        Args:
            livro_id: ID do livro (CD_SEQUENCIAL).

        Returns:
            Instancia de LivroCabecalho ou None se nao encontrado.
        """
        try:
            stmt = select(LivroCabecalho).where(LivroCabecalho.cd_sequencial == livro_id)
            return self._session.execute(stmt).scalar_one_or_none()
        except Exception:
            logger.exception("Erro ao buscar livro por id=%s", livro_id)
            self._session.rollback()
            raise

    def listar_todos(self) -> list[LivroCabecalho]:
        """Lista todos os livros ordenados pela data de manutencao (mais recente primeiro)."""
        try:
            stmt = select(LivroCabecalho).order_by(LivroCabecalho.dt_manutencao.desc())
            return list(self._session.execute(stmt).scalars().all())
        except Exception:
            logger.exception("Erro ao listar todos os livros")
            self._session.rollback()
            raise

    def listar_por_estado(self, estado: EstadoPipeline) -> list[LivroCabecalho]:
        """Lista livros filtrados por estado do pipeline.

        Args:
            estado: Estado do pipeline a ser filtrado.

        Returns:
            Lista de livros no estado informado.
        """
        try:
            stmt = (
                select(LivroCabecalho)
                .where(LivroCabecalho.estado_pipeline == estado.value)
                .order_by(LivroCabecalho.dt_manutencao.desc())
            )
            return list(self._session.execute(stmt).scalars().all())
        except Exception:
            logger.exception("Erro ao listar livros por estado=%s", estado)
            self._session.rollback()
            raise

    def listar_nao_concluidos(self) -> list[LivroCabecalho]:
        """Lista livros que NAO estao no estado 'concluido'.

        Util para o dashboard mostrar livros em producao/erro/pausado/aguardando.
        """
        try:
            stmt = (
                select(LivroCabecalho)
                .where(LivroCabecalho.estado_pipeline != EstadoPipeline.CONCLUIDO.value)
                .order_by(LivroCabecalho.dt_manutencao.desc())
            )
            return list(self._session.execute(stmt).scalars().all())
        except Exception:
            logger.exception("Erro ao listar livros nao concluidos")
            self._session.rollback()
            raise

    def listar_fila(self) -> list[LivroCabecalho]:
        """Lista livros presentes na fila, ordenando por fila_posicao (FIFO).

        Apenas retorna livros cuja `fila_posicao` nao e nula.
        """
        try:
            stmt = (
                select(LivroCabecalho)
                .where(LivroCabecalho.fila_posicao.is_not(None))
                .order_by(LivroCabecalho.fila_posicao.asc())
            )
            return list(self._session.execute(stmt).scalars().all())
        except Exception:
            logger.exception("Erro ao listar livros da fila")
            self._session.rollback()
            raise

    def proxima_posicao_fila(self) -> int:
        """Calcula a proxima posicao disponivel para a fila (max(atual)+1).

        Returns:
            Proxima posicao inteira. Se a fila estiver vazia retorna 1.
        """
        try:
            stmt = select(func.max(LivroCabecalho.fila_posicao))
            max_pos = self._session.execute(stmt).scalar()
            return (max_pos or 0) + 1
        except Exception:
            logger.exception("Erro ao calcular proxima posicao da fila")
            self._session.rollback()
            raise

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------
    def salvar(self, livro: LivroCabecalho) -> LivroCabecalho:
        """Persiste um livro (insert ou update) e faz flush + refresh.

        O commit NAO e realizado aqui — responsabilidade do caller
        (usualmente session_scope()).

        Args:
            livro: Instancia de LivroCabecalho (nova ou existente).

        Returns:
            A mesma instancia, recarregada com o estado apos persistencia.
        """
        try:
            self._session.add(livro)
            self._session.flush()
            self._session.refresh(livro)
            return livro
        except Exception:
            logger.exception("Erro ao salvar livro")
            self._session.rollback()
            raise

    def atualizar_estado(
        self,
        livro_id: int,
        estado: EstadoPipeline,
        progresso_atual: int | None = None,
    ) -> None:
        """Atualiza o estado do pipeline e campos correlatos.

        Regras automaticas:
          - Se estado == EXTRACAO: preenche `dt_inicio` com agora.
          - Se estado == CONCLUIDO: preenche `dt_conclusao` com agora.
          - Se estado == ERRO: limpa `erro_mensagem` por padrao (caller pode
            passar mensagem via outro metodo se necessario).
          - Outros: nao altera dt_inicio/dt_conclusao.

        Args:
            livro_id: ID do livro.
            estado: Novo estado do pipeline.
            progresso_atual: Valor opcional para atualizar o progresso.
        """
        try:
            livro = self.buscar_por_id_sync(livro_id)
            if livro is None:
                logger.warning("Livro id=%s nao encontrado para atualizar estado", livro_id)
                return

            agora = datetime.utcnow()
            livro.estado_pipeline = estado.value

            if progresso_atual is not None:
                livro.progresso_atual = progresso_atual

            if estado == EstadoPipeline.EXTRACAO:
                livro.dt_inicio = agora
            elif estado == EstadoPipeline.CONCLUIDO:
                livro.dt_conclusao = agora
            elif estado == EstadoPipeline.ERRO:
                # Caller pode setar erro_mensagem via metodo dedicado
                # aqui apenas garantimos que nao persistam dados antigos
                pass

            self._session.flush()
        except Exception:
            logger.exception("Erro ao atualizar estado do livro id=%s", livro_id)
            self._session.rollback()
            raise

    def definir_erro(self, livro_id: int, mensagem: str) -> None:
        """Define o estado ERRO e a mensagem de erro do livro.

        Args:
            livro_id: ID do livro.
            mensagem: Texto descritivo do erro para exibicao no dashboard.
        """
        try:
            livro = self.buscar_por_id_sync(livro_id)
            if livro is None:
                return
            livro.estado_pipeline = EstadoPipeline.ERRO.value
            livro.erro_mensagem = mensagem
            self._session.flush()
        except Exception:
            logger.exception("Erro ao definir erro no livro id=%s", livro_id)
            self._session.rollback()
            raise

    def reordenar_fila(self, livro_id: int, nova_posicao: int) -> None:
        """Move um livro para uma nova posicao na fila, ajustando os demais.

        Os outros livros na fila terao suas posicoes recalculadas para
        manter uma sequencia contigua (1, 2, 3, ...). Se a nova_posicao
        extrapolar o tamanho da fila, o livro vai para o final.

        Args:
            livro_id: ID do livro a ser movido.
            nova_posicao: Posicao alvo (1-based).
        """
        try:
            livro = self.buscar_por_id_sync(livro_id)
            if livro is None or livro.fila_posicao is None:
                logger.warning(
                    "Livro id=%s nao encontrado ou fora da fila para reordenar", livro_id
                )
                return

            # Busca fila atual ordenada
            fila = self.listar_fila()
            posicao_atual = livro.fila_posicao

            if posicao_atual == nova_posicao:
                return  # nada a fazer

            # Remove o livro da lista e reinsere na nova posicao
            fila = [l for l in fila if l.cd_sequencial != livro_id]
            if nova_posicao < 1:
                nova_posicao = 1
            if nova_posicao > len(fila) + 1:
                nova_posicao = len(fila) + 1
            fila.insert(nova_posicao - 1, livro)

            # Reatribui posicoes contiguas
            for idx, l in enumerate(fila, start=1):
                if l.fila_posicao != idx:
                    l.fila_posicao = idx
            self._session.flush()
        except Exception:
            logger.exception("Erro ao reordenar livro id=%s na fila", livro_id)
            self._session.rollback()
            raise

    def remover(self, livro_id: int) -> None:
        """Remove um livro (e cascata: paginas, personagens, falas)."""
        try:
            livro = self.buscar_por_id_sync(livro_id)
            if livro is None:
                return
            self._session.delete(livro)
            self._session.flush()
        except Exception:
            logger.exception("Erro ao remover livro id=%s", livro_id)
            self._session.rollback()
            raise

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------
    def buscar_por_id_sync(self, livro_id: int) -> LivroCabecalho | None:
        """Versao sincrona de buscar_por_id para uso interno da classe."""
        stmt = select(LivroCabecalho).where(LivroCabecalho.cd_sequencial == livro_id)
        return self._session.execute(stmt).scalar_one_or_none()
