"""Testes unitarios para LivroPaginaRepositorio."""

from __future__ import annotations

import pytest

from app.repositories.livro_pagina_repo import LivroPaginaRepositorio
from tests.conftest import make_livro, make_pagina


def _criar_livro(session) -> int:
    livro = make_livro()
    session.add(livro)
    session.commit()
    session.refresh(livro)
    return livro.cd_sequencial


class TestSalvarPaginas:
    def test_salvar_paginas_retorna_count(self, session):
        livro_id = _criar_livro(session)
        repo = LivroPaginaRepositorio(session)
        paginas = [make_pagina(livro_id, nr=i) for i in range(1, 6)]
        count = repo.salvar_paginas(livro_id, paginas)
        session.commit()
        assert count == 5

    def test_salvar_paginas_vazias_retorna_zero(self, session):
        livro_id = _criar_livro(session)
        repo = LivroPaginaRepositorio(session)
        count = repo.salvar_paginas(livro_id, [])
        assert count == 0

    def test_salvar_paginas_atribui_livro_id_se_ausente(self, session):
        livro_id = _criar_livro(session)
        repo = LivroPaginaRepositorio(session)
        # Pagina sem cd_sequenciallivro definido
        pagina = make_pagina(0, nr=1)
        pagina.cd_sequenciallivro = None
        repo.salvar_paginas(livro_id, [pagina])
        session.commit()
        refreshed = repo.buscar_por_id(pagina.cd_sequencial)
        assert refreshed.cd_sequenciallivro == livro_id


class TestListarPorLivro:
    def test_retorna_paginas_ordenadas_por_nr(self, session):
        livro_id = _criar_livro(session)
        repo = LivroPaginaRepositorio(session)
        for nr in [3, 1, 2]:
            repo.salvar_paginas(livro_id, [make_pagina(livro_id, nr=nr)])
        session.commit()
        result = repo.listar_por_livro(livro_id)
        assert [p.nr_pagina for p in result] == [1, 2, 3]

    def test_filtra_por_livro(self, session):
        livro_a = _criar_livro(session)
        livro_b = _criar_livro(session)
        repo = LivroPaginaRepositorio(session)
        repo.salvar_paginas(livro_a, [make_pagina(livro_a, nr=1)])
        repo.salvar_paginas(livro_b, [make_pagina(livro_b, nr=1)])
        session.commit()
        result = repo.listar_por_livro(livro_a)
        assert all(p.cd_sequenciallivro == livro_a for p in result)
        assert len(result) == 1


class TestListarNaoProcessadas:
    def test_retorna_apenas_nao_processadas(self, session):
        livro_id = _criar_livro(session)
        repo = LivroPaginaRepositorio(session)
        p1 = make_pagina(livro_id, nr=1, fl_processado="S")
        p2 = make_pagina(livro_id, nr=2, fl_processado="N")
        p3 = make_pagina(livro_id, nr=3, fl_processado=None)
        repo.salvar_paginas(livro_id, [p1, p2, p3])
        session.commit()
        result = repo.listar_nao_processadas(livro_id)
        nrs = {p.nr_pagina for p in result}
        assert nrs == {2, 3}


class TestMarcarProcessada:
    def test_marca_pagina_como_processada(self, session):
        livro_id = _criar_livro(session)
        repo = LivroPaginaRepositorio(session)
        p = make_pagina(livro_id, nr=1, fl_processado="N")
        repo.salvar_paginas(livro_id, [p])
        session.commit()
        repo.marcar_processada(p.cd_sequencial)
        session.commit()
        refreshed = repo.buscar_por_id(p.cd_sequencial)
        assert refreshed.fl_processado == "S"

    def test_marcar_pagina_inexistente_nao_falha(self, session):
        repo = LivroPaginaRepositorio(session)
        repo.marcar_processada(99999)
        session.commit()


class TestContarTotal:
    def test_conta_paginas(self, session):
        livro_id = _criar_livro(session)
        repo = LivroPaginaRepositorio(session)
        for nr in range(1, 4):
            repo.salvar_paginas(livro_id, [make_pagina(livro_id, nr=nr)])
        session.commit()
        assert repo.contar_total(livro_id) == 3

    def test_livro_sem_paginas(self, session):
        livro_id = _criar_livro(session)
        repo = LivroPaginaRepositorio(session)
        assert repo.contar_total(livro_id) == 0


class TestBuscarPorId:
    def test_encontrado(self, session):
        livro_id = _criar_livro(session)
        repo = LivroPaginaRepositorio(session)
        p = make_pagina(livro_id, nr=1)
        repo.salvar_paginas(livro_id, [p])
        session.commit()
        found = repo.buscar_por_id(p.cd_sequencial)
        assert found is not None
        assert found.cd_sequencial == p.cd_sequencial

    def test_nao_encontrado(self, session):
        repo = LivroPaginaRepositorio(session)
        assert repo.buscar_por_id(99999) is None
