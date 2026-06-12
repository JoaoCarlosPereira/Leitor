"""Excecoes customizadas usadas pelo pipeline de producao de audiolivros.

Este modulo define a hierarquia de excecoes levantadas pela
:class:`tasks.pipeline.PipelineOrquestrador` e consumidas pela tarefa
Celery ``executar_pipeline_task``. Manter estas excecoes em um modulo
dedicado evita ciclos de importacao com ``tasks.pipeline`` e permite
que outros modulos (rotas FastAPI, scripts CLI, etc.) capturem falhas
do pipeline sem depender da implementacao interna.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Excecao base para todos os erros do pipeline de producao.

    Todas as demais excecoes deste modulo herdam desta classe, o que
    permite que chamadores capturem genericamente ``PipelineError``
    para tratar qualquer falha do orquestrador sem precisar enumerar
    cada subclasse.
    """


class PipelinePausadoError(PipelineError):
    """Levantada quando o pipeline detecta que o livro foi pausado.

    A verificacao ocorre antes de cada etapa do pipeline. Quando o
    administrador pausa um livro (via flag ``fila_pausado`` ou estado
    ``pausado`` no banco), o orquestrador interrompe o ciclo de
    execucao levantando esta excecao. A tarefa Celery a captura e
    finaliza o job com status ``pausado`` em vez de disparar retry.
    """


class PipelineErroError(PipelineError):
    """Levantada quando uma etapa do pipeline falha de forma recuperavel.

    A falha e registrada na coluna ``erro_mensagem`` do livro e o
    estado passa a ``erro``. A tarefa Celery captura o erro e
    reagenda com backoff (atualmente 60 segundos), conforme definido
    na configuracao de ``retry`` da tarefa.
    """


class LivroNaoEncontradoError(PipelineError):
    """Levantada quando o ``livro_id`` informado nao existe no banco.

    Ocorre tipicamente quando o livro foi removido entre o agendamento
    da tarefa Celery e a sua execucao. E tratada como erro nao
    recuperavel: o orquestrador nao deve prosseguir e o livro nao
    pode ter seu estado alterado.
    """


__all__ = [
    "PipelineError",
    "PipelinePausadoError",
    "PipelineErroError",
    "LivroNaoEncontradoError",
]
