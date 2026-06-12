"""Fila FIFO de producao de livros.

Encapsula a logica de negocio que gerencia a fila de processamento FIFO dos
livros, incluindo enfileiramento, remocao, reordenacao, pausa/retomo e
callbacks de sucesso/falha do pipeline. Tambem expoe a task periodica
``processar_fila_task`` que escalona o proximo livro para execucao.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from celery.signals import task_failure, task_success
from sqlalchemy import select

from app.repositories import LivroRepositorio, session_scope
from app.repositories.models.livro_cabecalho import EstadoPipeline, LivroCabecalho
from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Constantes
# -----------------------------------------------------------------------------

ESTADOS_EM_PRODUCAO: tuple[str, ...] = (
    EstadoPipeline.EXTRACAO.value,
    EstadoPipeline.PERSONAGENS.value,
    EstadoPipeline.VOZES.value,
    EstadoPipeline.PRODUCAO.value,
    EstadoPipeline.JUNCAO.value,
)

ESTADO_ANTERIOR_KEY = "_estado_anterior_fila"


# -----------------------------------------------------------------------------
# Operacoes de fila
# -----------------------------------------------------------------------------


def enfileirar_livro(livro_id: int) -> int:
    """Adiciona um livro a fila de producao com a proxima posicao disponivel.

    Se o livro ja estiver na fila, retorna a posicao atual sem reordenar.
    Se nao houver livro em producao, agenda imediatamente a execucao do
    pipeline. Caso contrario, o livro ficara aguardando o slot.

    Args:
        livro_id: ID (CD_SEQUENCIAL) do livro a enfileirar.

    Returns:
        Posicao atribuida ao livro na fila (1-based).

    Raises:
        ValueError: Se o livro nao for encontrado no banco.
    """
    with session_scope() as session:
        repo = LivroRepositorio(session)
        livro = repo.buscar_por_id_sync(livro_id)
        if livro is None:
            raise ValueError(f"Livro id={livro_id} nao encontrado")

        # Se ja esta na fila, apenas devolve a posicao atual
        if livro.fila_posicao is not None:
            logger.info(
                "Livro id=%s ja esta na fila (posicao=%s)",
                livro_id,
                livro.fila_posicao,
            )
            return int(livro.fila_posicao)

        # Calcula proxima posicao: max(atual) + 1, ou 1 se vazia
        proxima_posicao = repo.proxima_posicao_fila()
        livro.fila_posicao = proxima_posicao
        livro.fila_pausado = "N"
        livro.estado_pipeline = EstadoPipeline.AGUARDANDO.value
        session.flush()

        logger.info(
            "Livro id=%s enfileirado na posicao=%s", livro_id, proxima_posicao
        )
        atribuir = proxima_posicao

    # Fora da sessao para checar producao de forma independente
    if not tem_livro_em_producao():
        _agendar_pipeline(livro_id)

    return atribuir


def remover_da_fila(livro_id: int) -> None:
    """Remove um livro da fila, sem excluir o registro do banco.

    Reordena as posicoes dos livros posteriores para manter a fila contigua
    (1, 2, 3, ...). O livro removido tera ``fila_posicao`` setada como NULL.

    Args:
        livro_id: ID do livro a remover da fila.

    Raises:
        ValueError: Se o livro nao existir.
    """
    with session_scope() as session:
        repo = LivroRepositorio(session)
        livro = repo.buscar_por_id_sync(livro_id)
        if livro is None:
            raise ValueError(f"Livro id={livro_id} nao encontrado")

        if livro.fila_posicao is None:
            logger.info("Livro id=%s ja nao esta na fila", livro_id)
            return

        posicao_removida = int(livro.fila_posicao)
        livro.fila_posicao = None
        livro.fila_pausado = "N"
        session.flush()

        # Reordena: livros com posicao > posicao_removida recebem -1
        stmt = select_fila_apos(posicao_removida)
        for outro in session.execute(stmt).scalars().all():
            outro.fila_posicao = int(outro.fila_posicao) - 1
        session.flush()

        logger.info(
            "Livro id=%s removido da fila (era posicao=%s)", livro_id, posicao_removida
        )


def reordenar_fila(livro_id: int, nova_posicao: int) -> None:
    """Move um livro para uma nova posicao na fila.

    Ajusta as posicoes dos demais livros para manter a sequencia contigua.
    Nao permite reordenar livros que estao atualmente em producao.

    Args:
        livro_id: ID do livro a reordenar.
        nova_posicao: Posicao alvo (1-based).

    Raises:
        ValueError: Se o livro nao existir, nao estiver na fila ou
            estiver em producao.
    """
    with session_scope() as session:
        repo = LivroRepositorio(session)
        livro = repo.buscar_por_id_sync(livro_id)
        if livro is None:
            raise ValueError(f"Livro id={livro_id} nao encontrado")

        if livro.fila_posicao is None:
            raise ValueError(f"Livro id={livro_id} nao esta na fila")

        if livro.estado_pipeline in ESTADOS_EM_PRODUCAO:
            raise ValueError(
                f"Livro id={livro_id} esta em producao "
                f"(estado='{livro.estado_pipeline}') e nao pode ser reordenado"
            )

        posicao_atual = int(livro.fila_posicao)

        # Limites: 1 <= nova_posicao <= tamanho_da_fila
        total_fila = repo.listar_fila()
        tamanho = len(total_fila)
        if nova_posicao < 1:
            nova_posicao = 1
        if nova_posicao > tamanho:
            nova_posicao = tamanho

        if nova_posicao == posicao_atual:
            return

        if nova_posicao < posicao_atual:
            # Movendo para cima: livros entre nova_posicao e posicao_atual-1
            # precisam ser deslocados para baixo (+1)
            stmt = (
                select(LivroCabecalho)
                .where(LivroCabecalho.fila_posicao.is_not(None))
                .where(LivroCabecalho.fila_posicao >= nova_posicao)
                .where(LivroCabecalho.fila_posicao < posicao_atual)
                .where(LivroCabecalho.cd_sequencial != livro_id)
            )
            for outro in session.execute(stmt).scalars().all():
                outro.fila_posicao = int(outro.fila_posicao) + 1
        else:
            # Movendo para baixo: livros entre posicao_atual+1 e nova_posicao
            # precisam ser deslocados para cima (-1)
            stmt = (
                select(LivroCabecalho)
                .where(LivroCabecalho.fila_posicao.is_not(None))
                .where(LivroCabecalho.fila_posicao > posicao_atual)
                .where(LivroCabecalho.fila_posicao <= nova_posicao)
                .where(LivroCabecalho.cd_sequencial != livro_id)
            )
            for outro in session.execute(stmt).scalars().all():
                outro.fila_posicao = int(outro.fila_posicao) - 1

        livro.fila_posicao = nova_posicao
        session.flush()
        logger.info(
            "Livro id=%s reordenado de %s para %s",
            livro_id,
            posicao_atual,
            nova_posicao,
        )


def pausar_livro(livro_id: int) -> None:
    """Pausa a producao de um livro.

    Marca ``fila_pausado='S'``. Se o livro estiver em producao, altera
    tambem o estado do pipeline para 'pausado'.

    Args:
        livro_id: ID do livro a pausar.

    Raises:
        ValueError: Se o livro nao existir.
    """
    with session_scope() as session:
        repo = LivroRepositorio(session)
        livro = repo.buscar_por_id_sync(livro_id)
        if livro is None:
            raise ValueError(f"Livro id={livro_id} nao encontrado")

        livro.fila_pausado = "S"
        if livro.estado_pipeline in ESTADOS_EM_PRODUCAO:
            livro.estado_pipeline = EstadoPipeline.PAUSADO.value
        session.flush()
        logger.info("Livro id=%s pausado", livro_id)


def retomar_livro(livro_id: int) -> None:
    """Retoma a producao de um livro previamente pausado.

    Marca ``fila_pausado='N'``. Se o livro estava em estado 'pausado',
    retorna para 'aguardando'. Se nao houver outro livro em producao e
    o livro retomado for o proximo da fila, agenda o pipeline
    imediatamente.

    Args:
        livro_id: ID do livro a retomar.

    Raises:
        ValueError: Se o livro nao existir.
    """
    with session_scope() as session:
        repo = LivroRepositorio(session)
        livro = repo.buscar_por_id_sync(livro_id)
        if livro is None:
            raise ValueError(f"Livro id={livro_id} nao encontrado")

        livro.fila_pausado = "N"
        if livro.estado_pipeline == EstadoPipeline.PAUSADO.value:
            livro.estado_pipeline = EstadoPipeline.AGUARDANDO.value
        session.flush()

        eh_proximo = _eh_proximo_na_fila_db(session, livro_id)
        logger.info("Livro id=%s retomado (eh_proximo=%s)", livro_id, eh_proximo)

    # Verifica producao de forma independente para evitar usar a sessao fechada
    if eh_proximo and not tem_livro_em_producao():
        _agendar_pipeline(livro_id)


# -----------------------------------------------------------------------------
# Consultas
# -----------------------------------------------------------------------------


def listar_fila() -> list[dict[str, Any]]:
    """Retorna a fila atual de livros, ordenada por ``fila_posicao``.

    Returns:
        Lista de dicionarios com metadados de cada livro na fila.
    """
    with session_scope() as session:
        repo = LivroRepositorio(session)
        livros = repo.listar_fila()
        return [_livro_para_dict(l) for l in livros]


def proximo_livro_a_processar() -> int | None:
    """Retorna o ID do proximo livro a ser processado.

    Criterios de selecao:
      * Possui ``fila_posicao`` (esta na fila)
      * NAO esta pausado (``fila_pausado != 'S'``)
      * Estado do pipeline e 'aguardando'
      * Menor ``fila_posicao`` entre os elegiveis

    Returns:
        ``cd_sequencial`` do proximo livro, ou ``None`` se a fila estiver
        vazia ou todos os livros estiverem pausados/em producao.
    """
    with session_scope() as session:
        livro = _proximo_livro_db(session)
        return livro.cd_sequencial if livro is not None else None


def tem_livro_em_producao() -> bool:
    """Verifica se existe algum livro em estado de producao ativo.

    Considera em producao: extracao, personagens, vozes, producao, juncao.

    Returns:
        True se houver livro em producao, False caso contrario.
    """
    with session_scope() as session:
        return tem_livro_em_producao_db(session)


# -----------------------------------------------------------------------------
# Callbacks
# -----------------------------------------------------------------------------


def on_pipeline_success(livro_id: int) -> None:
    """Callback invocado quando o pipeline termina com sucesso.

    Marca o livro como concluido, registra a data de conclusao e o remove
    da fila. Em seguida, agenda o proximo livro da fila para processamento.

    Args:
        livro_id: ID do livro que concluiu o pipeline.
    """
    with session_scope() as session:
        repo = LivroRepositorio(session)
        livro = repo.buscar_por_id_sync(livro_id)
        if livro is None:
            logger.warning(
                "on_pipeline_success: livro id=%s nao encontrado", livro_id
            )
            return

        livro.estado_pipeline = EstadoPipeline.CONCLUIDO.value
        livro.dt_conclusao = datetime.utcnow()
        livro.fila_posicao = None
        livro.fila_pausado = "N"
        livro.progresso_atual = livro.progresso_total or 0
        session.flush()
        logger.info("Livro id=%s concluido com sucesso", livro_id)

        proximo = _proximo_livro_db(session)
        proximo_id = proximo.cd_sequencial if proximo is not None else None

    if proximo_id is not None:
        _agendar_pipeline(proximo_id)


def on_pipeline_failure(livro_id: int, erro: str) -> None:
    """Callback invocado quando o pipeline falha.

    Marca o livro em estado 'erro' com a mensagem recebida. NAO inicia
    o proximo livro automaticamente — a decisao fica com o administrador.

    Args:
        livro_id: ID do livro que falhou.
        erro: Mensagem descritiva do erro.
    """
    with session_scope() as session:
        repo = LivroRepositorio(session)
        livro = repo.buscar_por_id_sync(livro_id)
        if livro is None:
            logger.warning("on_pipeline_failure: livro id=%s nao encontrado", livro_id)
            return

        livro.estado_pipeline = EstadoPipeline.ERRO.value
        livro.erro_mensagem = erro
        session.flush()
        logger.error("Livro id=%s entrou em estado de erro: %s", livro_id, erro)


# -----------------------------------------------------------------------------
# Tarefa Celery
# -----------------------------------------------------------------------------


@celery_app.task(name="tasks.processar_fila")
def processar_fila_task() -> None:
    """Task periodica que verifica a fila e inicia o proximo livro.

    E chamada periodicamente (via beat) ou em resposta a eventos da fila.
    Se ja houver livro em producao, nao faz nada. Caso contrario, busca o
    proximo livro elegivel e agenda ``executar_pipeline_task``.
    """
    if tem_livro_em_producao():
        logger.debug("Ja ha livro em producao; nenhuma acao necessaria")
        return

    proximo_id = proximo_livro_a_processar()
    if proximo_id is None:
        logger.debug("Fila vazia ou sem livros elegiveis")
        return

    _agendar_pipeline(proximo_id)


# -----------------------------------------------------------------------------
# Signals do Celery
# -----------------------------------------------------------------------------


@task_success.connect
def task_success_handler(sender=None, result=None, **kwargs: Any) -> None:
    """Handler do signal ``task_success`` do Celery.

    Se o ``result`` da task for um dicionario contendo ``livro_id``,
    dispara o callback ``on_pipeline_success`` para avancar a fila.
    """
    if isinstance(result, dict) and "livro_id" in result:
        try:
            livro_id = int(result["livro_id"])
        except (TypeError, ValueError):
            logger.warning("task_success_handler: livro_id invalido em %r", result)
            return
        on_pipeline_success(livro_id)


@task_failure.connect
def task_failure_handler(sender=None, task_id=None, exception=None, **kwargs: Any) -> None:
    """Handler do signal ``task_failure`` do Celery.

    Tenta extrair o ``livro_id`` dos argumentos da task. Se encontrado,
    registra o estado de erro no livro correspondente.
    """
    livro_id: int | None = None
    try:
        request = getattr(sender, "request", None)
        if request is not None and getattr(request, "args", None):
            primeiro = request.args[0]
            livro_id = int(primeiro)
    except (TypeError, ValueError, IndexError, AttributeError):
        livro_id = None

    if livro_id is None:
        logger.warning(
            "task_failure_handler: nao foi possivel extrair livro_id de sender=%r",
            sender,
        )
        return

    mensagem = str(exception) if exception is not None else "erro desconhecido"
    on_pipeline_failure(livro_id, mensagem)


# -----------------------------------------------------------------------------
# Helpers internos
# -----------------------------------------------------------------------------


def _agendar_pipeline(livro_id: int) -> None:
    """Agenda a execucao do pipeline para o livro informado.

    Importa ``executar_pipeline_task`` tardiamente para evitar import
    circular entre ``tasks.fila`` e ``tasks.pipeline``.
    """
    try:
        from tasks.pipeline import executar_pipeline_task  # type: ignore[import-not-found]
    except ImportError:
        logger.exception(
            "Modulo tasks.pipeline nao disponivel; impossivel agendar livro id=%s",
            livro_id,
        )
        return

    logger.info("Agendando pipeline para livro id=%s", livro_id)
    try:
        executar_pipeline_task.apply_async(args=[livro_id])
    except Exception:  # noqa: BLE001
        # Em ambiente de teste ou sem broker, nao propagamos o erro
        # de agendamento (pipeline podera ser disparado manualmente).
        logger.exception(
            "Falha ao agendar pipeline para livro id=%s via Celery",
            livro_id,
        )


def tem_livro_em_producao_db(session) -> bool:
    """Versao que recebe a sessao aberta, util dentro de transacoes."""
    stmt = select(LivroCabecalho).where(
        LivroCabecalho.estado_pipeline.in_(ESTADOS_EM_PRODUCAO)
    )
    return session.execute(stmt).first() is not None


def _proximo_livro_db(session) -> LivroCabecalho | None:
    """Implementacao interna que recebe a sessao aberta."""
    stmt = (
        select(LivroCabecalho)
        .where(LivroCabecalho.fila_posicao.is_not(None))
        .where(LivroCabecalho.fila_pausado != "S")
        .where(LivroCabecalho.estado_pipeline == EstadoPipeline.AGUARDANDO.value)
        .order_by(LivroCabecalho.fila_posicao.asc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _eh_proximo_na_fila_db(session, livro_id: int) -> bool:
    """Retorna True se o livro informado e o proximo elegivel da fila."""
    livro = _proximo_livro_db(session)
    return livro is not None and livro.cd_sequencial == livro_id


def _livro_para_dict(livro: LivroCabecalho) -> dict[str, Any]:
    """Converte um ``LivroCabecalho`` em dicionario para serializacao."""
    return {
        "cd_sequencial": livro.cd_sequencial,
        "tx_titulo": livro.tx_titulo,
        "autor": livro.autor,
        "estado_pipeline": livro.estado_pipeline,
        "fila_posicao": livro.fila_posicao,
        "fila_pausado": livro.fila_pausado,
        "progresso_atual": livro.progresso_atual,
        "progresso_total": livro.progresso_total,
        "dt_inicio": livro.dt_inicio.isoformat() if livro.dt_inicio else None,
        "dt_conclusao": (
            livro.dt_conclusao.isoformat() if livro.dt_conclusao else None
        ),
    }


def select_fila_apos(posicao: int):
    """Retorna a query SQLAlchemy para livros da fila com posicao > ``posicao``."""
    return (
        select(LivroCabecalho)
        .where(LivroCabecalho.fila_posicao.is_not(None))
        .where(LivroCabecalho.fila_posicao > posicao)
        .order_by(LivroCabecalho.fila_posicao.asc())
    )
