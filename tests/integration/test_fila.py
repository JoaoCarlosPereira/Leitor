"""Testes de integracao para ``tasks.fila``.

Estes testes mockam ``session_scope`` e ``executar_pipeline_task`` para
exercitar a logica de gerencia da fila FIFO sem depender de banco de
dados real ou do worker Celery.
"""

from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.repositories.models.livro_cabecalho import EstadoPipeline, LivroCabecalho
from tasks import fila


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def session_mock() -> MagicMock:
    """Sessao SQLAlchemy mockada."""
    return MagicMock(name="session")


@pytest.fixture
def fake_session_scope(session_mock: MagicMock):
    """Substitui ``session_scope`` por um context manager que entrega o mock."""

    @contextmanager
    def _scope() -> Any:
        yield session_mock

    return _scope


def _make_livro(
    cd: int = 1,
    posicao: int | None = None,
    estado: str = EstadoPipeline.AGUARDANDO.value,
    pausado: str = "N",
) -> MagicMock:
    """Cria um mock de ``LivroCabecalho`` com os atributos relevantes."""
    livro = MagicMock(spec=LivroCabecalho)
    livro.cd_sequencial = cd
    livro.tx_titulo = f"Livro {cd}"
    livro.autor = f"Autor {cd}"
    livro.estado_pipeline = estado
    livro.fila_posicao = posicao
    livro.fila_pausado = pausado
    livro.progresso_atual = 0
    livro.progresso_total = 6
    livro.dt_inicio = None
    livro.dt_conclusao = None
    livro.erro_mensagem = None
    return livro


@pytest.fixture
def repo_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Instancia mock de ``LivroRepositorio`` substituida no modulo."""
    repo = MagicMock(name="LivroRepositorio")
    monkeypatch.setattr(fila, "LivroRepositorio", lambda _session: repo)
    return repo


# -----------------------------------------------------------------------------
# enfileirar_livro
# -----------------------------------------------------------------------------


class TestEnfileirarLivro:
    """Cobre a funcao ``enfileirar_livro``."""

    def test_livro_nao_encontrado_levanta_erro(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        """Se o livro nao existir no banco, levanta ``ValueError``."""
        repo_mock.buscar_por_id_sync.return_value = None
        with patch.object(fila, "session_scope", fake_session_scope):
            with pytest.raises(ValueError, match="nao encontrado"):
                fila.enfileirar_livro(99)

    def test_adiciona_livro_com_posicao_1_quando_fila_vazia(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Quando a fila esta vazia, o livro recebe posicao 1 e agenda o pipeline."""
        livro = _make_livro(cd=1, posicao=None)
        repo_mock.buscar_por_id_sync.return_value = livro
        repo_mock.proxima_posicao_fila.return_value = 1

        pipeline_mock = MagicMock(name="executar_pipeline_task")
        pipeline_mod = MagicMock()
        pipeline_mod.executar_pipeline_task = pipeline_mock
        monkeypatch.setitem(sys.modules, "tasks.pipeline", pipeline_mod)

        with patch.object(fila, "session_scope", fake_session_scope):
            with patch.object(fila, "tem_livro_em_producao", return_value=False):
                pos = fila.enfileirar_livro(1)

        assert pos == 1
        assert livro.fila_posicao == 1
        assert livro.fila_pausado == "N"
        assert livro.estado_pipeline == EstadoPipeline.AGUARDANDO.value
        pipeline_mock.apply_async.assert_called_once_with(args=[1])

    def test_livro_ja_na_fila_retorna_posicao_atual(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Se o livro ja esta na fila, retorna a posicao atual sem reordenar."""
        livro = _make_livro(cd=2, posicao=3)
        repo_mock.buscar_por_id_sync.return_value = livro

        pipeline_mock = MagicMock(name="executar_pipeline_task")
        pipeline_mod = MagicMock()
        pipeline_mod.executar_pipeline_task = pipeline_mock
        monkeypatch.setitem(sys.modules, "tasks.pipeline", pipeline_mod)

        with patch.object(fila, "session_scope", fake_session_scope):
            pos = fila.enfileirar_livro(2)

        assert pos == 3
        livro.fila_posicao = 3  # inalterado
        pipeline_mock.apply_async.assert_not_called()
        repo_mock.proxima_posicao_fila.assert_not_called()

    def test_com_livro_em_producao_nao_agenda(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Se ja ha livro em producao, nao agenda o pipeline novamente."""
        livro = _make_livro(cd=5, posicao=None)
        repo_mock.buscar_por_id_sync.return_value = livro
        repo_mock.proxima_posicao_fila.return_value = 2

        pipeline_mock = MagicMock()
        pipeline_mod = MagicMock()
        pipeline_mod.executar_pipeline_task = pipeline_mock
        monkeypatch.setitem(sys.modules, "tasks.pipeline", pipeline_mod)

        with patch.object(fila, "session_scope", fake_session_scope):
            with patch.object(fila, "tem_livro_em_producao", return_value=True):
                pos = fila.enfileirar_livro(5)

        assert pos == 2
        pipeline_mock.apply_async.assert_not_called()


# -----------------------------------------------------------------------------
# remover_da_fila
# -----------------------------------------------------------------------------


class TestRemoverDaFila:
    """Cobre a funcao ``remover_da_fila``."""

    def test_livro_nao_encontrado_levanta_erro(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        """Se o livro nao existir, levanta ``ValueError``."""
        repo_mock.buscar_por_id_sync.return_value = None
        with patch.object(fila, "session_scope", fake_session_scope):
            with pytest.raises(ValueError, match="nao encontrado"):
                fila.remover_da_fila(99)

    def test_livro_fora_da_fila_noop(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        """Se o livro nao tem posicao, nao faz nada."""
        livro = _make_livro(cd=1, posicao=None)
        repo_mock.buscar_por_id_sync.return_value = livro
        with patch.object(fila, "session_scope", fake_session_scope):
            fila.remover_da_fila(1)
        assert livro.fila_posicao is None
        session_mock.execute.assert_not_called()

    def test_remove_e_reordena(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        """Remove o livro e reordena as posicoes dos demais (decrementa)."""
        livro = _make_livro(cd=1, posicao=2)
        livro_pos3 = _make_livro(cd=2, posicao=3)
        livro_pos4 = _make_livro(cd=3, posicao=4)

        repo_mock.buscar_por_id_sync.return_value = livro

        # Mock da query: retorna os livros com posicao > 2
        session_mock.execute.return_value.scalars.return_value.all.return_value = [
            livro_pos3,
            livro_pos4,
        ]

        with patch.object(fila, "session_scope", fake_session_scope):
            fila.remover_da_fila(1)

        assert livro.fila_posicao is None
        assert livro.fila_pausado == "N"
        assert livro_pos3.fila_posicao == 2
        assert livro_pos4.fila_posicao == 3


# -----------------------------------------------------------------------------
# reordenar_fila
# -----------------------------------------------------------------------------


class TestReordenarFila:
    """Cobre a funcao ``reordenar_fila``."""

    def test_livro_nao_encontrado_levanta_erro(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        repo_mock.buscar_por_id_sync.return_value = None
        with patch.object(fila, "session_scope", fake_session_scope):
            with pytest.raises(ValueError, match="nao encontrado"):
                fila.reordenar_fila(99, 1)

    def test_livro_fora_da_fila_levanta_erro(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        livro = _make_livro(cd=1, posicao=None)
        repo_mock.buscar_por_id_sync.return_value = livro
        with patch.object(fila, "session_scope", fake_session_scope):
            with pytest.raises(ValueError, match="nao esta na fila"):
                fila.reordenar_fila(1, 1)

    def test_livro_em_producao_levanta_erro(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        livro = _make_livro(cd=1, posicao=2, estado=EstadoPipeline.EXTRACAO.value)
        repo_mock.buscar_por_id_sync.return_value = livro
        with patch.object(fila, "session_scope", fake_session_scope):
            with pytest.raises(ValueError, match="em producao"):
                fila.reordenar_fila(1, 1)

    def test_mover_para_cima_decrementa_intermediarios(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        """Mover para cima: livros entre nova e antiga posicao ganham +1."""
        livro = _make_livro(cd=3, posicao=4)
        repo_mock.buscar_por_id_sync.return_value = livro
        repo_mock.listar_fila.return_value = [
            _make_livro(cd=1, posicao=1),
            _make_livro(cd=2, posicao=2),
            _make_livro(cd=3, posicao=4),
            _make_livro(cd=4, posicao=5),
        ]
        # Mock da query: executa o ``select`` para livros com posicao >= 1 e < 4
        intermed1 = _make_livro(cd=1, posicao=1)
        intermed2 = _make_livro(cd=2, posicao=2)
        session_mock.execute.return_value.scalars.return_value.all.return_value = [
            intermed1,
            intermed2,
        ]

        with patch.object(fila, "session_scope", fake_session_scope):
            fila.reordenar_fila(3, 1)

        assert livro.fila_posicao == 1
        assert intermed1.fila_posicao == 2
        assert intermed2.fila_posicao == 3

    def test_mover_para_baixo_incrementa_intermediarios(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        """Mover para baixo: livros entre antiga e nova posicao ganham -1."""
        livro = _make_livro(cd=1, posicao=1)
        repo_mock.buscar_por_id_sync.return_value = livro
        repo_mock.listar_fila.return_value = [
            _make_livro(cd=1, posicao=1),
            _make_livro(cd=2, posicao=2),
            _make_livro(cd=3, posicao=3),
            _make_livro(cd=4, posicao=4),
        ]
        intermed2 = _make_livro(cd=2, posicao=2)
        intermed3 = _make_livro(cd=3, posicao=3)
        intermed4 = _make_livro(cd=4, posicao=4)
        session_mock.execute.return_value.scalars.return_value.all.return_value = [
            intermed2,
            intermed3,
            intermed4,
        ]

        with patch.object(fila, "session_scope", fake_session_scope):
            fila.reordenar_fila(1, 4)

        assert livro.fila_posicao == 4
        assert intermed2.fila_posicao == 1
        assert intermed3.fila_posicao == 2
        assert intermed4.fila_posicao == 3

    def test_mesma_posicao_noop(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        livro = _make_livro(cd=1, posicao=2)
        repo_mock.buscar_por_id_sync.return_value = livro
        repo_mock.listar_fila.return_value = [
            _make_livro(cd=1, posicao=2),
            _make_livro(cd=2, posicao=3),
        ]

        with patch.object(fila, "session_scope", fake_session_scope):
            fila.reordenar_fila(1, 2)

        assert livro.fila_posicao == 2
        # Nenhuma reordenacao e feita
        session_mock.execute.assert_not_called()


# -----------------------------------------------------------------------------
# pausar_livro / retomar_livro
# -----------------------------------------------------------------------------


class TestPausarERetomar:
    """Cobre ``pausar_livro`` e ``retomar_livro``."""

    def test_pausar_livro_em_espera(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        livro = _make_livro(cd=1, posicao=2, estado=EstadoPipeline.AGUARDANDO.value)
        repo_mock.buscar_por_id_sync.return_value = livro
        with patch.object(fila, "session_scope", fake_session_scope):
            fila.pausar_livro(1)
        assert livro.fila_pausado == "S"
        # Estado aguardando nao deve virar pausado (regra: so muda se em producao)
        assert livro.estado_pipeline == EstadoPipeline.AGUARDANDO.value

    def test_pausar_livro_em_producao_muda_estado(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        livro = _make_livro(cd=1, posicao=2, estado=EstadoPipeline.EXTRACAO.value)
        repo_mock.buscar_por_id_sync.return_value = livro
        with patch.object(fila, "session_scope", fake_session_scope):
            fila.pausar_livro(1)
        assert livro.fila_pausado == "S"
        assert livro.estado_pipeline == EstadoPipeline.PAUSADO.value

    def test_retomar_livro_aguardando(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        livro = _make_livro(
            cd=1, posicao=1, estado=EstadoPipeline.PAUSADO.value, pausado="S"
        )
        repo_mock.buscar_por_id_sync.return_value = livro

        pipeline_mock = MagicMock()
        pipeline_mod = MagicMock()
        pipeline_mod.executar_pipeline_task = pipeline_mock
        monkeypatch.setitem(sys.modules, "tasks.pipeline", pipeline_mod)

        with patch.object(fila, "session_scope", fake_session_scope):
            with patch.object(fila, "tem_livro_em_producao", return_value=True):
                fila.retomar_livro(1)

        assert livro.fila_pausado == "N"
        assert livro.estado_pipeline == EstadoPipeline.AGUARDANDO.value
        # Em producao, nao agenda
        pipeline_mock.apply_async.assert_not_called()

    def test_retomar_livro_proximo_sem_producao_agenda(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        livro = _make_livro(
            cd=1, posicao=1, estado=EstadoPipeline.PAUSADO.value, pausado="S"
        )
        repo_mock.buscar_por_id_sync.return_value = livro
        repo_mock.buscar_por_id_sync.return_value.cd_sequencial = 1

        # A consulta do proximo livro (em retomar) usa a sessao mock
        # entao precisamos que o mesmo livro retornado seja considerado "proximo"
        from sqlalchemy.engine import Result  # noqa: F401  # so para import real

        # O resultado do ``_proximo_livro_db`` precisa devolver o proprio livro
        session_mock.execute.return_value.scalar_one_or_none.return_value = livro

        pipeline_mock = MagicMock()
        pipeline_mod = MagicMock()
        pipeline_mod.executar_pipeline_task = pipeline_mock
        monkeypatch.setitem(sys.modules, "tasks.pipeline", pipeline_mod)

        with patch.object(fila, "session_scope", fake_session_scope):
            with patch.object(fila, "tem_livro_em_producao", return_value=False):
                fila.retomar_livro(1)

        assert livro.fila_pausado == "N"
        assert livro.estado_pipeline == EstadoPipeline.AGUARDANDO.value
        pipeline_mock.apply_async.assert_called_once_with(args=[1])


# -----------------------------------------------------------------------------
# listar_fila / proximo_livro_a_processar / tem_livro_em_producao
# -----------------------------------------------------------------------------


class TestConsultas:
    """Cobre as funcoes de consulta."""

    def test_listar_fila_ordenada(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        livros = [
            _make_livro(cd=1, posicao=1),
            _make_livro(cd=2, posicao=2),
            _make_livro(cd=3, posicao=3),
        ]
        repo_mock.listar_fila.return_value = livros
        with patch.object(fila, "session_scope", fake_session_scope):
            resultado = fila.listar_fila()

        assert len(resultado) == 3
        assert resultado[0]["cd_sequencial"] == 1
        assert resultado[1]["fila_posicao"] == 2
        assert all("tx_titulo" in item for item in resultado)
        assert all("dt_inicio" in item for item in resultado)

    def test_proximo_livro_a_processar_vazio(
        self,
        session_mock: MagicMock,
        fake_session_scope,
    ) -> None:
        session_mock.execute.return_value.scalar_one_or_none.return_value = None
        with patch.object(fila, "session_scope", fake_session_scope):
            assert fila.proximo_livro_a_processar() is None

    def test_proximo_livro_a_processar_encontrado(
        self,
        session_mock: MagicMock,
        fake_session_scope,
    ) -> None:
        livro = _make_livro(cd=42, posicao=1)
        session_mock.execute.return_value.scalar_one_or_none.return_value = livro
        with patch.object(fila, "session_scope", fake_session_scope):
            assert fila.proximo_livro_a_processar() == 42

    def test_tem_livro_em_producao_true(
        self,
        session_mock: MagicMock,
        fake_session_scope,
    ) -> None:
        session_mock.execute.return_value.first.return_value = ("alguma linha",)
        with patch.object(fila, "session_scope", fake_session_scope):
            assert fila.tem_livro_em_producao() is True

    def test_tem_livro_em_producao_false(
        self,
        session_mock: MagicMock,
        fake_session_scope,
    ) -> None:
        session_mock.execute.return_value.first.return_value = None
        with patch.object(fila, "session_scope", fake_session_scope):
            assert fila.tem_livro_em_producao() is False


# -----------------------------------------------------------------------------
# Callbacks
# -----------------------------------------------------------------------------


class TestCallbacks:
    """Cobre ``on_pipeline_success`` e ``on_pipeline_failure``."""

    def test_on_pipeline_success_marca_concluido_e_agenda_proximo(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        livro_atual = _make_livro(cd=1, posicao=1, estado=EstadoPipeline.JUNCAO.value)
        livro_proximo = _make_livro(cd=2, posicao=2)
        repo_mock.buscar_por_id_sync.return_value = livro_atual

        # O unico ``session.execute`` chamado em on_pipeline_success
        # vem de _proximo_livro_db; configuramos seu resultado
        session_mock.execute.return_value.scalar_one_or_none.return_value = (
            livro_proximo
        )

        pipeline_mock = MagicMock()
        pipeline_mod = MagicMock()
        pipeline_mod.executar_pipeline_task = pipeline_mock
        monkeypatch.setitem(sys.modules, "tasks.pipeline", pipeline_mod)

        with patch.object(fila, "session_scope", fake_session_scope):
            fila.on_pipeline_success(1)

        assert livro_atual.estado_pipeline == EstadoPipeline.CONCLUIDO.value
        assert livro_atual.dt_conclusao is not None
        assert livro_atual.fila_posicao is None
        pipeline_mock.apply_async.assert_called_once_with(args=[2])

    def test_on_pipeline_success_sem_proximo(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        livro_atual = _make_livro(cd=1, posicao=1)
        repo_mock.buscar_por_id_sync.return_value = livro_atual

        session_mock.execute.return_value.scalar_one_or_none.return_value = None

        pipeline_mock = MagicMock()
        pipeline_mod = MagicMock()
        pipeline_mod.executar_pipeline_task = pipeline_mock
        monkeypatch.setitem(sys.modules, "tasks.pipeline", pipeline_mod)

        with patch.object(fila, "session_scope", fake_session_scope):
            fila.on_pipeline_success(1)

        assert livro_atual.estado_pipeline == EstadoPipeline.CONCLUIDO.value
        pipeline_mock.apply_async.assert_not_called()

    def test_on_pipeline_failure_marca_erro(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        livro = _make_livro(cd=1, posicao=1, estado=EstadoPipeline.EXTRACAO.value)
        repo_mock.buscar_por_id_sync.return_value = livro

        pipeline_mock = MagicMock()
        pipeline_mod = MagicMock()
        pipeline_mod.executar_pipeline_task = pipeline_mock
        monkeypatch.setitem(sys.modules, "tasks.pipeline", pipeline_mod)

        with patch.object(fila, "session_scope", fake_session_scope):
            fila.on_pipeline_failure(1, "Timeout no LLM")

        assert livro.estado_pipeline == EstadoPipeline.ERRO.value
        assert livro.erro_mensagem == "Timeout no LLM"
        # Nao agenda o proximo
        pipeline_mock.apply_async.assert_not_called()

    def test_on_pipeline_failure_livro_inexistente(
        self,
        session_mock: MagicMock,
        fake_session_scope,
        repo_mock: MagicMock,
    ) -> None:
        repo_mock.buscar_por_id_sync.return_value = None
        with patch.object(fila, "session_scope", fake_session_scope):
            # Nao deve levantar
            fila.on_pipeline_failure(99, "algum erro")


# -----------------------------------------------------------------------------
# processar_fila_task
# -----------------------------------------------------------------------------


class TestProcessarFilaTask:
    """Cobre a task ``processar_fila_task``."""

    def test_nao_faz_nada_com_livro_em_producao(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with patch.object(fila, "tem_livro_em_producao", return_value=True) as tp:
            with patch.object(fila, "proximo_livro_a_processar") as pa:
                fila.processar_fila_task()
        tp.assert_called_once()
        pa.assert_not_called()

    def test_fila_vazia_noop(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with patch.object(fila, "tem_livro_em_producao", return_value=False):
            with patch.object(fila, "proximo_livro_a_processar", return_value=None):
                with patch.object(fila, "_agendar_pipeline") as agendar:
                    fila.processar_fila_task()
        agendar.assert_not_called()

    def test_agenda_proximo_livro(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with patch.object(fila, "tem_livro_em_producao", return_value=False):
            with patch.object(fila, "proximo_livro_a_processar", return_value=42):
                with patch.object(fila, "_agendar_pipeline") as agendar:
                    fila.processar_fila_task()
        agendar.assert_called_once_with(42)


# -----------------------------------------------------------------------------
# Signals do Celery
# -----------------------------------------------------------------------------


class TestSignals:
    """Cobre os handlers de signal do Celery."""

    def test_task_success_handler_chama_callback(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with patch.object(fila, "on_pipeline_success") as cb:
            fila.task_success_handler(result={"livro_id": 7, "status": "ok"})
        cb.assert_called_once_with(7)

    def test_task_success_handler_sem_livro_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with patch.object(fila, "on_pipeline_success") as cb:
            fila.task_success_handler(result={"status": "ok"})
        cb.assert_not_called()

    def test_task_success_handler_result_invalido(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with patch.object(fila, "on_pipeline_success") as cb:
            fila.task_success_handler(result="nao e dict")
        cb.assert_not_called()

    def test_task_failure_handler_extrai_livro_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sender = MagicMock()
        sender.request.args = [13]
        with patch.object(fila, "on_pipeline_failure") as cb:
            fila.task_failure_handler(
                sender=sender, exception=RuntimeError("falhou")
            )
        cb.assert_called_once()
        args, _ = cb.call_args
        assert args[0] == 13
        assert "falhou" in args[1]

    def test_task_failure_handler_sem_args(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sender = MagicMock()
        sender.request.args = []
        with patch.object(fila, "on_pipeline_failure") as cb:
            fila.task_failure_handler(sender=sender, exception=Exception("x"))
        cb.assert_not_called()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


class TestHelpers:
    """Cobre helpers internos."""

    def test_livro_para_dict_campos_obrigatorios(self) -> None:
        from datetime import datetime

        livro = _make_livro(cd=1, posicao=2)
        livro.dt_inicio = datetime(2026, 1, 1, 12, 0, 0)
        livro.dt_conclusao = datetime(2026, 1, 2, 12, 0, 0)
        resultado = fila._livro_para_dict(livro)
        assert resultado["cd_sequencial"] == 1
        assert resultado["tx_titulo"] == "Livro 1"
        assert resultado["autor"] == "Autor 1"
        assert resultado["estado_pipeline"] == EstadoPipeline.AGUARDANDO.value
        assert resultado["fila_posicao"] == 2
        assert resultado["fila_pausado"] == "N"
        assert resultado["dt_inicio"] == "2026-01-01T12:00:00"
        assert resultado["dt_conclusao"] == "2026-01-02T12:00:00"

    def test_livro_para_dict_datas_nulas(self) -> None:
        livro = _make_livro(cd=2, posicao=3)
        resultado = fila._livro_para_dict(livro)
        assert resultado["dt_inicio"] is None
        assert resultado["dt_conclusao"] is None

    def test_agendar_pipeline_sem_modulo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Se ``tasks.pipeline`` nao puder ser importado, apenas loga."""
        # Garante que import falhe
        monkeypatch.delitem(sys.modules, "tasks.pipeline", raising=False)
        # Injeta um loader que levanta ImportError ao tentar carregar pipeline
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else None
        if original_import is not None:
            def fake_import(name, *args, **kwargs):
                if name == "tasks.pipeline" or name.startswith("tasks.pipeline"):
                    raise ImportError("simulado")
                return original_import(name, *args, **kwargs)
            monkeypatch.setattr("builtins.__import__", fake_import)
        # Nao deve levantar
        fila._agendar_pipeline(1)

    def test_modulo_importa_sem_erro(self) -> None:
        """Garante que o modulo pode ser importado."""
        modulo = importlib.import_module("tasks.fila")
        assert hasattr(modulo, "enfileirar_livro")
        assert hasattr(modulo, "remover_da_fila")
        assert hasattr(modulo, "reordenar_fila")
        assert hasattr(modulo, "pausar_livro")
        assert hasattr(modulo, "retomar_livro")
        assert hasattr(modulo, "listar_fila")
        assert hasattr(modulo, "proximo_livro_a_processar")
        assert hasattr(modulo, "tem_livro_em_producao")
        assert hasattr(modulo, "processar_fila_task")
