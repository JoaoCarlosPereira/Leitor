"""Testes unitarios para LivroRepositorio."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app.repositories.livro_repo import LivroRepositorio
from app.repositories.models.livro_cabecalho import EstadoPipeline
from tests.conftest import make_livro


# ----------------------------- helpers ------------------------------------

def _criar_livro_salvo(session, **kwargs) -> int:
    livro = make_livro(**kwargs)
    session.add(livro)
    session.commit()
    session.refresh(livro)
    return livro.cd_sequencial


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ----------------------------- testes -------------------------------------

class TestBuscarPorId:
    def test_retorna_none_quando_nao_existe(self, session):
        repo = LivroRepositorio(session)
        result = _run(repo.buscar_por_id(999))
        assert result is None

    def test_retorna_instancia_quando_encontrado(self, session):
        livro_id = _criar_livro_salvo(session, tx_titulo="Dom Casmurro")
        repo = LivroRepositorio(session)
        result = _run(repo.buscar_por_id(livro_id))
        assert result is not None
        assert result.cd_sequencial == livro_id
        assert result.tx_titulo == "Dom Casmurro"


class TestSalvar:
    def test_salvar_insere_e_retorna_instancia(self, session):
        repo = LivroRepositorio(session)
        livro = make_livro(tx_titulo="O Cortico")
        saved = repo.salvar(livro)
        session.commit()
        assert saved.cd_sequencial is not None
        assert saved.tx_titulo == "O Cortico"

    def test_salvar_atualiza_existente(self, session):
        livro_id = _criar_livro_salvo(session, tx_titulo="Titulo Antigo")
        repo = LivroRepositorio(session)
        livro = _run(repo.buscar_por_id(livro_id))
        livro.tx_titulo = "Titulo Novo"
        repo.salvar(livro)
        session.commit()
        refreshed = _run(repo.buscar_por_id(livro_id))
        assert refreshed.tx_titulo == "Titulo Novo"


class TestAtualizarEstado:
    def test_atualizar_estado_para_extracao_preenche_dt_inicio(self, session):
        livro_id = _criar_livro_salvo(session)
        repo = LivroRepositorio(session)
        repo.atualizar_estado(livro_id, EstadoPipeline.EXTRACAO, progresso_atual=1)
        session.commit()
        refreshed = _run(repo.buscar_por_id(livro_id))
        assert refreshed.estado_pipeline == "extracao"
        assert refreshed.progresso_atual == 1
        assert refreshed.dt_inicio is not None

    def test_atualizar_estado_para_concluido_preenche_dt_conclusao(self, session):
        livro_id = _criar_livro_salvo(session)
        repo = LivroRepositorio(session)
        repo.atualizar_estado(livro_id, EstadoPipeline.CONCLUIDO, progresso_atual=6)
        session.commit()
        refreshed = _run(repo.buscar_por_id(livro_id))
        assert refreshed.estado_pipeline == "concluido"
        assert refreshed.dt_conclusao is not None

    def test_atualizar_estado_para_erro(self, session):
        livro_id = _criar_livro_salvo(session)
        repo = LivroRepositorio(session)
        repo.atualizar_estado(livro_id, EstadoPipeline.ERRO)
        session.commit()
        refreshed = _run(repo.buscar_por_id(livro_id))
        assert refreshed.estado_pipeline == "erro"

    def test_atualizar_estado_livro_inexistente_nao_falha(self, session):
        repo = LivroRepositorio(session)
        repo.atualizar_estado(99999, EstadoPipeline.EXTRACAO)
        session.commit()  # nao deve levantar excecao


class TestDefinirErro:
    def test_definir_erro_persiste_mensagem(self, session):
        livro_id = _criar_livro_salvo(session)
        repo = LivroRepositorio(session)
        repo.definir_erro(livro_id, "Falha ao extrair PDF")
        session.commit()
        refreshed = _run(repo.buscar_por_id(livro_id))
        assert refreshed.estado_pipeline == "erro"
        assert refreshed.erro_mensagem == "Falha ao extrair PDF"


class TestListar:
    def test_listar_todos_retorna_todos(self, session):
        _criar_livro_salvo(session, tx_titulo="Livro A")
        _criar_livro_salvo(session, tx_titulo="Livro B")
        repo = LivroRepositorio(session)
        result = repo.listar_todos()
        assert len(result) == 2

    def test_listar_por_estado(self, session):
        _criar_livro_salvo(session, tx_titulo="Aguardando")
        livro_id = _criar_livro_salvo(session, tx_titulo="Em extracao")
        repo = LivroRepositorio(session)
        # altera estado do segundo
        livro = _run(repo.buscar_por_id(livro_id))
        livro.estado_pipeline = EstadoPipeline.EXTRACAO.value
        session.commit()
        result = repo.listar_por_estado(EstadoPipeline.EXTRACAO)
        assert len(result) == 1
        assert result[0].cd_sequencial == livro_id

    def test_listar_nao_concluidos_exclui_concluidos(self, session):
        a = _criar_livro_salvo(session, tx_titulo="Pendente")
        b = _criar_livro_salvo(session, tx_titulo="Finalizado")
        repo = LivroRepositorio(session)
        livro = _run(repo.buscar_por_id(b))
        livro.estado_pipeline = EstadoPipeline.CONCLUIDO.value
        session.commit()

        result = repo.listar_nao_concluidos()
        ids = [l.cd_sequencial for l in result]
        assert a in ids
        assert b not in ids


class TestFila:
    def test_proxima_posicao_fila_vazia(self, session):
        repo = LivroRepositorio(session)
        assert repo.proxima_posicao_fila() == 1

    def test_proxima_posicao_fila_com_livros(self, session):
        _criar_livro_salvo(session, tx_titulo="A", fila_posicao=1)
        _criar_livro_salvo(session, tx_titulo="B", fila_posicao=2)
        _criar_livro_salvo(session, tx_titulo="C", fila_posicao=3)
        repo = LivroRepositorio(session)
        assert repo.proxima_posicao_fila() == 4

    def test_listar_fila_apenas_com_fila_posicao(self, session):
        _criar_livro_salvo(session, tx_titulo="Sem fila", fila_posicao=None)
        _criar_livro_salvo(session, tx_titulo="Na fila A", fila_posicao=2)
        _criar_livro_salvo(session, tx_titulo="Na fila B", fila_posicao=1)
        repo = LivroRepositorio(session)
        result = repo.listar_fila()
        assert len(result) == 2
        # Deve vir ordenado por posicao
        assert result[0].fila_posicao == 1
        assert result[1].fila_posicao == 2

    def test_reordenar_fila_move_para_frente(self, session):
        a = _criar_livro_salvo(session, tx_titulo="A", fila_posicao=1)
        b = _criar_livro_salvo(session, tx_titulo="B", fila_posicao=2)
        c = _criar_livro_salvo(session, tx_titulo="C", fila_posicao=3)
        repo = LivroRepositorio(session)
        # Move A para a posicao 3
        repo.reordenar_fila(a, 3)
        session.commit()
        # Ordem esperada: B(1), C(2), A(3)
        refreshed = {l.cd_sequencial: l.fila_posicao for l in repo.listar_fila()}
        assert refreshed[b] == 1
        assert refreshed[c] == 2
        assert refreshed[a] == 3

    def test_reordenar_fila_move_para_tras(self, session):
        a = _criar_livro_salvo(session, tx_titulo="A", fila_posicao=1)
        b = _criar_livro_salvo(session, tx_titulo="B", fila_posicao=2)
        c = _criar_livro_salvo(session, tx_titulo="C", fila_posicao=3)
        repo = LivroRepositorio(session)
        # Move C para a posicao 1
        repo.reordenar_fila(c, 1)
        session.commit()
        refreshed = {l.cd_sequencial: l.fila_posicao for l in repo.listar_fila()}
        assert refreshed[c] == 1
        assert refreshed[a] == 2
        assert refreshed[b] == 3

    def test_reordenar_fila_mesma_posicao_nao_altera(self, session):
        a = _criar_livro_salvo(session, tx_titulo="A", fila_posicao=1)
        b = _criar_livro_salvo(session, tx_titulo="B", fila_posicao=2)
        repo = LivroRepositorio(session)
        repo.reordenar_fila(a, 1)
        session.commit()
        refreshed = {l.cd_sequencial: l.fila_posicao for l in repo.listar_fila()}
        assert refreshed[a] == 1
        assert refreshed[b] == 2


class TestRemover:
    def test_remover_livro_existente(self, session):
        livro_id = _criar_livro_salvo(session, tx_titulo="Apagar")
        repo = LivroRepositorio(session)
        repo.remover(livro_id)
        session.commit()
        assert _run(repo.buscar_por_id(livro_id)) is None

    def test_remover_livro_inexistente_nao_falha(self, session):
        repo = LivroRepositorio(session)
        repo.remover(99999)
        session.commit()  # nao deve levantar
