"""Testes unitarios para LivroAPIRepositorio."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.repositories.livro_api_repo import LivroAPIRepositorio
from tests.conftest import make_api


class TestSalvarChave:
    def test_salva_chave_basica(self, session):
        repo = LivroAPIRepositorio(session)
        api = repo.salvar_chave(
            tx_key="minha-chave-123",
            tx_nome="LLM Local",
            tx_servico="llm",
        )
        session.commit()
        assert api.cd_sequencial is not None
        assert api.tx_key == "minha-chave-123"
        assert api.fl_ativo == "S"

    def test_salva_chave_com_expiracao(self, session):
        repo = LivroAPIRepositorio(session)
        exp = datetime.utcnow() + timedelta(days=10)
        api = repo.salvar_chave(
            tx_key="k1",
            tx_nome="K1",
            tx_servico="tts",
            dt_expiracao=exp,
        )
        session.commit()
        assert api.dt_expiracao is not None


class TestListar:
    def test_listar_apenas_ativas_por_padrao(self, session):
        repo = LivroAPIRepositorio(session)
        repo.salvar_chave("k1", "Ativa 1", "llm")
        repo.salvar_chave("k2", "Ativa 2", "tts")
        api_inativa = repo.salvar_chave("k3", "Inativa", "llm")
        repo.desativar(api_inativa.cd_sequencial)
        session.commit()

        ativas = repo.listar()
        assert len(ativas) == 2
        assert all(a.fl_ativo == "S" for a in ativas)

    def test_listar_todas_quando_apenas_ativas_false(self, session):
        repo = LivroAPIRepositorio(session)
        repo.salvar_chave("k1", "A1", "llm")
        api2 = repo.salvar_chave("k2", "A2", "tts")
        repo.desativar(api2.cd_sequencial)
        session.commit()

        todas = repo.listar(apenas_ativas=False)
        assert len(todas) == 2


class TestRemoverChave:
    def test_remove_existente(self, session):
        repo = LivroAPIRepositorio(session)
        api = repo.salvar_chave("k1", "Remover", "llm")
        session.commit()
        repo.remover_chave(api.cd_sequencial)
        session.commit()
        # Buscar diretamente
        from sqlalchemy import select
        from app.repositories.models.livro_api import LivroAPI
        result = session.execute(
            select(LivroAPI).where(LivroAPI.cd_sequencial == api.cd_sequencial)
        ).scalar_one_or_none()
        assert result is None

    def test_remover_inexistente_nao_falha(self, session):
        repo = LivroAPIRepositorio(session)
        repo.remover_chave(99999)
        session.commit()


class TestVerificarValidade:
    def test_chave_sem_expiracao_e_valida(self, session):
        repo = LivroAPIRepositorio(session)
        api = repo.salvar_chave("k1", "K1", "llm", dt_expiracao=None)
        session.commit()
        assert repo.verificar_validade(api.cd_sequencial) is True

    def test_chave_nao_expirada_e_valida(self, session):
        repo = LivroAPIRepositorio(session)
        api = repo.salvar_chave(
            "k1", "K1", "llm",
            dt_expiracao=datetime.utcnow() + timedelta(days=5),
        )
        session.commit()
        assert repo.verificar_validade(api.cd_sequencial) is True

    def test_chave_expirada_e_invalida(self, session):
        repo = LivroAPIRepositorio(session)
        api = repo.salvar_chave(
            "k1", "K1", "llm",
            dt_expiracao=datetime.utcnow() - timedelta(days=1),
        )
        session.commit()
        assert repo.verificar_validade(api.cd_sequencial) is False

    def test_chave_inexistente_e_invalida(self, session):
        repo = LivroAPIRepositorio(session)
        assert repo.verificar_validade(99999) is False


class TestBuscarPorServico:
    def test_encontra_ativa(self, session):
        repo = LivroAPIRepositorio(session)
        repo.salvar_chave("k1", "K1", "llm")
        repo.salvar_chave("k2", "K2", "tts")
        session.commit()
        result = repo.buscar_por_servico("tts")
        assert result is not None
        assert result.tx_servico == "tts"

    def test_nao_retorna_inativa(self, session):
        repo = LivroAPIRepositorio(session)
        api = repo.salvar_chave("k1", "K1", "llm")
        repo.desativar(api.cd_sequencial)
        session.commit()
        assert repo.buscar_por_servico("llm") is None

    def test_servico_inexistente_retorna_none(self, session):
        repo = LivroAPIRepositorio(session)
        repo.salvar_chave("k1", "K1", "llm")
        session.commit()
        assert repo.buscar_por_servico("nao_existe") is None


class TestDesativar:
    def test_desativar(self, session):
        repo = LivroAPIRepositorio(session)
        api = repo.salvar_chave("k1", "K1", "llm")
        session.commit()
        repo.desativar(api.cd_sequencial)
        session.commit()
        assert repo.buscar_por_servico("llm") is None
        # Confirmar com listar
        ativas = repo.listar(apenas_ativas=True)
        assert all(a.cd_sequencial != api.cd_sequencial for a in ativas)
