"""Testes unitarios para LivroPersonagemRepositorio."""

from __future__ import annotations

import pytest

from app.repositories.livro_personagem_repo import LivroPersonagemRepositorio
from app.repositories.livro_fala_repo import LivroFalaRepositorio
from tests.conftest import make_fala, make_livro, make_pagina, make_personagem


def _setup_livro_com_pagina(session):
    """Cria livro + pagina e retorna seus IDs."""
    livro = make_livro()
    session.add(livro)
    session.commit()
    session.refresh(livro)
    pagina = make_pagina(livro.cd_sequencial, nr=1)
    session.add(pagina)
    session.commit()
    session.refresh(pagina)
    return livro.cd_sequencial, pagina.cd_sequencial


class TestSalvar:
    def test_salvar_insere_e_retorna_instancia(self, session):
        livro_id, _ = _setup_livro_com_pagina(session)
        repo = LivroPersonagemRepositorio(session)
        p = make_personagem(livro_id, nome="Maria")
        saved = repo.salvar(p)
        session.commit()
        assert saved.cd_sequencial is not None
        assert saved.tx_personagem == "Maria"


class TestSalvarVarios:
    def test_salva_lista(self, session):
        livro_id, _ = _setup_livro_com_pagina(session)
        repo = LivroPersonagemRepositorio(session)
        lista = [
            make_personagem(livro_id, nome="Maria"),
            make_personagem(livro_id, nome="Joao"),
        ]
        repo.salvar_varios(lista)
        session.commit()
        assert repo.contar_por_livro(livro_id) == 2

    def test_salvar_lista_vazia_nao_falha(self, session):
        repo = LivroPersonagemRepositorio(session)
        repo.salvar_varios([])
        session.commit()


class TestAtualizar:
    def test_atualiza_campos_dinamicamente(self, session):
        livro_id, _ = _setup_livro_com_pagina(session)
        repo = LivroPersonagemRepositorio(session)
        p = make_personagem(livro_id, nome="Maria")
        repo.salvar(p)
        session.commit()

        repo.atualizar(p.cd_sequencial, tx_genero="Male", tx_idade="Elderly")
        session.commit()

        refreshed = repo.buscar_por_id(p.cd_sequencial)
        assert refreshed.tx_genero == "Male"
        assert refreshed.tx_idade == "Elderly"

    def test_atualizar_sem_kwargs_nao_falha(self, session):
        livro_id, _ = _setup_livro_com_pagina(session)
        repo = LivroPersonagemRepositorio(session)
        p = make_personagem(livro_id)
        repo.salvar(p)
        session.commit()
        repo.atualizar(p.cd_sequencial)
        session.commit()


class TestRemover:
    def test_remove_personagem(self, session):
        livro_id, _ = _setup_livro_com_pagina(session)
        repo = LivroPersonagemRepositorio(session)
        p = make_personagem(livro_id, nome="Apagar")
        repo.salvar(p)
        session.commit()
        repo.remover(p.cd_sequencial)
        session.commit()
        assert repo.buscar_por_id(p.cd_sequencial) is None


class TestListarPorLivro:
    def test_retorna_ordenado_por_nome(self, session):
        livro_id, _ = _setup_livro_com_pagina(session)
        repo = LivroPersonagemRepositorio(session)
        for nome in ["Zeca", "Ana", "Maria"]:
            repo.salvar(make_personagem(livro_id, nome=nome))
        session.commit()
        result = repo.listar_por_livro(livro_id)
        assert [p.tx_personagem for p in result] == ["Ana", "Maria", "Zeca"]


class TestBuscarPorNome:
    def test_encontrado(self, session):
        livro_id, _ = _setup_livro_com_pagina(session)
        repo = LivroPersonagemRepositorio(session)
        repo.salvar(make_personagem(livro_id, nome="Maria"))
        session.commit()
        result = repo.buscar_por_nome(livro_id, "Maria")
        assert result is not None
        assert result.tx_personagem == "Maria"

    def test_nao_encontrado(self, session):
        livro_id, _ = _setup_livro_com_pagina(session)
        repo = LivroPersonagemRepositorio(session)
        assert repo.buscar_por_nome(livro_id, "Inexistente") is None


class TestListarNomesUnicos:
    def test_retorna_apenas_nomes_distintos(self, session):
        livro_id, _ = _setup_livro_com_pagina(session)
        repo = LivroPersonagemRepositorio(session)
        # Inserimos o mesmo nome 2x (possivel no dedupe)
        for _ in range(2):
            repo.salvar(make_personagem(livro_id, nome="Maria"))
        repo.salvar(make_personagem(livro_id, nome="Joao"))
        session.commit()
        result = repo.listar_nomes_unicos(livro_id)
        assert sorted(result) == ["Joao", "Maria"]


class TestContarPorLivro:
    def test_conta_personagens(self, session):
        livro_id, _ = _setup_livro_com_pagina(session)
        repo = LivroPersonagemRepositorio(session)
        for nome in ["A", "B", "C"]:
            repo.salvar(make_personagem(livro_id, nome=nome))
        session.commit()
        assert repo.contar_por_livro(livro_id) == 3

    def test_livro_vazio(self, session):
        livro_id, _ = _setup_livro_com_pagina(session)
        repo = LivroPersonagemRepositorio(session)
        assert repo.contar_por_livro(livro_id) == 0


class TestMesclar:
    def test_mesclar_move_falas_e_remove_origem(self, session):
        livro_id, pagina_id = _setup_livro_com_pagina(session)
        repo_p = LivroPersonagemRepositorio(session)
        repo_f = LivroFalaRepositorio(session)

        origem = make_personagem(livro_id, nome="Maria Velha")
        destino = make_personagem(livro_id, nome="Maria")
        repo_p.salvar(origem)
        repo_p.salvar(destino)
        session.commit()

        # Cria 2 falas atribuidas a origem
        repo_f.salvar_varias([
            make_fala(livro_id, pagina_id, origem.cd_sequencial, texto="Fala 1"),
            make_fala(livro_id, pagina_id, origem.cd_sequencial, texto="Fala 2"),
        ])
        session.commit()
        assert repo_f.contar_por_personagem(origem.cd_sequencial) == 2

        # Mescla
        repo_p.mesclar(origem.cd_sequencial, destino.cd_sequencial)
        session.commit()

        # Origem removida
        assert repo_p.buscar_por_id(origem.cd_sequencial) is None
        # Falas migradas para destino
        assert repo_f.contar_por_personagem(destino.cd_sequencial) == 2
        assert repo_f.contar_por_personagem(origem.cd_sequencial) == 0

    def test_mesclar_com_origem_igual_destino_nao_faz_nada(self, session):
        livro_id, _ = _setup_livro_com_pagina(session)
        repo = LivroPersonagemRepositorio(session)
        p = make_personagem(livro_id, nome="Solo")
        repo.salvar(p)
        session.commit()
        repo.mesclar(p.cd_sequencial, p.cd_sequencial)
        session.commit()
        assert repo.buscar_por_id(p.cd_sequencial) is not None
