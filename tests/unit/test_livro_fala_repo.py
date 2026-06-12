"""Testes unitarios para LivroFalaRepositorio."""

from __future__ import annotations

import pytest

from app.repositories.livro_fala_repo import LivroFalaRepositorio
from tests.conftest import make_fala, make_livro, make_pagina, make_personagem


def _setup_completo(session):
    """Cria livro + pagina + personagem; retorna seus IDs."""
    livro = make_livro()
    session.add(livro)
    session.commit()
    session.refresh(livro)

    pagina = make_pagina(livro.cd_sequencial, nr=1)
    session.add(pagina)
    session.commit()
    session.refresh(pagina)

    personagem = make_personagem(livro.cd_sequencial, nome="Narrador")
    session.add(personagem)
    session.commit()
    session.refresh(personagem)

    return livro.cd_sequencial, pagina.cd_sequencial, personagem.cd_sequencial


class TestSalvarVarias:
    def test_salva_lista(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        falas = [make_fala(livro_id, pagina_id, personagem_id, texto=f"Fala {i}", nr_ordem=i)
                 for i in range(3)]
        repo.salvar_varias(falas)
        session.commit()
        assert repo.contar_por_personagem(personagem_id) == 3


class TestAtualizarFala:
    def test_atualiza_campos(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        fala = make_fala(livro_id, pagina_id, personagem_id, texto="Original")
        repo.salvar_varias([fala])
        session.commit()
        repo.atualizar_fala(fala.cd_sequencial, tx_fala="Editado", fl_processado="S")
        session.commit()
        refreshed = repo.buscar_por_id(fala.cd_sequencial)
        assert refreshed.tx_fala == "Editado"
        assert refreshed.fl_processado == "S"


class TestMarcarProcessada:
    def test_marca_processada(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        fala = make_fala(livro_id, pagina_id, personagem_id, fl_processado="N")
        repo.salvar_varias([fala])
        session.commit()
        repo.marcar_processada(fala.cd_sequencial)
        session.commit()
        assert repo.buscar_por_id(fala.cd_sequencial).fl_processado == "S"


class TestMarcarRejeitada:
    def test_marca_rejeitada(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        fala = make_fala(livro_id, pagina_id, personagem_id)
        repo.salvar_varias([fala])
        session.commit()
        repo.marcar_rejeitada(fala.cd_sequencial)
        session.commit()
        assert repo.buscar_por_id(fala.cd_sequencial).fl_rejeitado == "S"


class TestSalvarInstrucaoAudio:
    def test_salva_tres_instrucoes(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        fala = make_fala(livro_id, pagina_id, personagem_id)
        repo.salvar_varias([fala])
        session.commit()
        repo.salvar_instrucao_audio(
            fala.cd_sequencial,
            emocao="alegre",
            prosodia="devagar",
            paralinguistica="[risada]",
        )
        session.commit()
        refreshed = repo.buscar_por_id(fala.cd_sequencial)
        assert refreshed.tx_instrucao_emocao == "alegre"
        assert refreshed.tx_instrucao_prosodia == "devagar"
        assert refreshed.tx_instrucao_paralinguistica == "[risada]"


class TestListarPorLivro:
    def test_filtra_por_livro_e_ordena_por_ordem(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        for ordem in [3, 1, 2]:
            repo.salvar_varias(
                [make_fala(livro_id, pagina_id, personagem_id,
                           texto=f"f{ordem}", nr_ordem=ordem)]
            )
        session.commit()
        result = repo.listar_por_livro(livro_id)
        assert [f.nr_ordem for f in result] == [1, 2, 3]


class TestListarPorPersonagem:
    def test_filtra_por_personagem(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        # Cria 2 personagens, com 1 fala cada
        outro = make_personagem(livro_id, nome="Outro")
        session.add(outro)
        session.commit()
        session.refresh(outro)

        repo.salvar_varias([
            make_fala(livro_id, pagina_id, personagem_id, nr_ordem=1),
            make_fala(livro_id, pagina_id, outro.cd_sequencial, nr_ordem=2),
        ])
        session.commit()
        result = repo.listar_por_personagem(personagem_id)
        assert len(result) == 1


class TestListarPorPagina:
    def test_filtra_por_pagina(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        outra_pagina = make_pagina(livro_id, nr=2)
        session.add(outra_pagina)
        session.commit()
        session.refresh(outra_pagina)

        repo.salvar_varias([
            make_fala(livro_id, pagina_id, personagem_id, nr_ordem=1),
            make_fala(livro_id, outra_pagina.cd_sequencial, personagem_id, nr_ordem=2),
        ])
        session.commit()
        result = repo.listar_por_pagina(pagina_id)
        assert len(result) == 1


class TestListarSemEmocao:
    def test_retorna_apenas_sem_emocao(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        f1 = make_fala(livro_id, pagina_id, personagem_id, nr_ordem=1)
        f2 = make_fala(livro_id, pagina_id, personagem_id, nr_ordem=2,
                       tx_instrucao_emocao="alegre")
        repo.salvar_varias([f1, f2])
        session.commit()
        result = repo.listar_sem_emocao(livro_id)
        assert len(result) == 1
        assert result[0].cd_sequencial == f1.cd_sequencial


class TestListarNaoProcessadas:
    def test_filtra_nao_processadas(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        repo.salvar_varias([
            make_fala(livro_id, pagina_id, personagem_id, nr_ordem=1, fl_processado="S"),
            make_fala(livro_id, pagina_id, personagem_id, nr_ordem=2, fl_processado="N"),
            make_fala(livro_id, pagina_id, personagem_id, nr_ordem=3, fl_processado=None),
        ])
        session.commit()
        result = repo.listar_nao_processadas(livro_id)
        assert len(result) == 2


class TestListarRejeitadas:
    def test_filtra_rejeitadas(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        repo.salvar_varias([
            make_fala(livro_id, pagina_id, personagem_id, nr_ordem=1, fl_rejeitado="S"),
            make_fala(livro_id, pagina_id, personagem_id, nr_ordem=2, fl_rejeitado="N"),
        ])
        session.commit()
        result = repo.listar_rejeitadas(livro_id)
        assert len(result) == 1
        assert result[0].fl_rejeitado == "S"


class TestContarPorPersonagem:
    def test_conta(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        for i in range(4):
            repo.salvar_varias(
                [make_fala(livro_id, pagina_id, personagem_id, nr_ordem=i)]
            )
        session.commit()
        assert repo.contar_por_personagem(personagem_id) == 4


class TestBuscarPorId:
    def test_encontrado_e_nao_encontrado(self, session):
        livro_id, pagina_id, personagem_id = _setup_completo(session)
        repo = LivroFalaRepositorio(session)
        fala = make_fala(livro_id, pagina_id, personagem_id)
        repo.salvar_varias([fala])
        session.commit()
        assert repo.buscar_por_id(fala.cd_sequencial) is not None
        assert repo.buscar_por_id(99999) is None
