"""Testes unitarios para o orquestrador de pipeline (tasks/pipeline.py).

Estes testes cobrem:

* Construcao do orquestrador com injecao de dependencia;
* Verificacao de pausa e persistencia do estado;
* Checkpoint por etapa (mapeamento etapa -> estado + progresso);
* Marcacao de erro em caso de falha;
* Execucao completa de ``executar_pipeline`` com servicos mocados;
* Retomada inteligente de pipeline (``retomar_pipeline``);
* Tarefa Celery ``executar_pipeline_task`` (pausa, retry, sucesso).

O acesso ao banco e mockado em TODAS as secoes para que o teste
seja deterministico e nao dependa de PostgreSQL. A funcao
``fake_session_scope`` (``tests/conftest.py`` ou definida
localmente) injeta uma sessao falsa em todos os pontos onde
``session_scope`` e usado.
"""

from __future__ import annotations

import base64
import logging
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import pytest

from app.repositories.models.livro_cabecalho import EstadoPipeline, LivroCabecalho
from app.repositories.models.livro_fala import LivroFala
from app.repositories.models.livro_pagina import LivroPagina
from app.repositories.models.livro_personagem import LivroPersonagem
from tasks.exceptions import (
    LivroNaoEncontradoError,
    PipelineError,
    PipelineErroError,
    PipelinePausadoError,
)
from tasks.pipeline import (
    PipelineOrquestrador,
    TOTAL_ETAPAS,
    executar_pipeline_task,
)


# -----------------------------------------------------------------------------
# Fixtures e helpers
# -----------------------------------------------------------------------------


class _FakeSession:
    """Sessao falsa minima usada como contexto."""

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        return False


@contextmanager
def fake_session_scope(session: Any | None = None) -> Iterator[Any]:
    """Substituto de ``session_scope`` que entrega uma sessao mockada.

    A maioria dos metodos do orquestrador abre um ``with
    session_scope() as session:`` no inicio. Em testes, queremos
    fornecer uma sessao controlada. Este helper abstrai o
    ``__enter__``/``__exit__`` e o ``commit``/``rollback``.
    """
    sessao = session if session is not None else _FakeSession()
    try:
        yield sessao
        # O session_scope real faz commit; replicamos para que os mocks
        # do tipo ``MagicMock`` possam ser observados.
        if hasattr(sessao, "commit"):
            sessao.commit()
    except Exception:
        if hasattr(sessao, "rollback"):
            sessao.rollback()
        raise


@pytest.fixture
def mock_sessao() -> MagicMock:
    """Sessao mockada de uso geral."""
    sessao = MagicMock()
    sessao.__enter__ = MagicMock(return_value=sessao)
    sessao.__exit__ = MagicMock(return_value=False)
    return sessao


def _make_livro(
    livro_id: int = 1,
    *,
    estado: str = "aguardando",
    fila_pausado: str | None = "N",
    progresso_atual: int = 0,
    progresso_total: int = TOTAL_ETAPAS,
    fl_lido: str | None = "N",
    fl_normalizado: str | None = "N",
    fl_narrador: str | None = "N",
    fl_produzido: str | None = "N",
    caminho_pdf: str | None = "/tmp/livro.pdf",
    caminho_audio_final: str | None = None,
) -> MagicMock:
    """Constroi um mock de ``LivroCabecalho`` com valores padrao."""
    livro = MagicMock(spec=LivroCabecalho)
    livro.cd_sequencial = livro_id
    livro.tx_titulo = "Livro Teste"
    livro.estado_pipeline = estado
    livro.fila_pausado = fila_pausado
    livro.progresso_atual = progresso_atual
    livro.progresso_total = progresso_total
    livro.fl_lido = fl_lido
    livro.fl_normalizado = fl_normalizado
    livro.fl_narrador = fl_narrador
    livro.fl_produzido = fl_produzido
    livro.caminho_pdf = caminho_pdf
    livro.caminho_audio_final = caminho_audio_final
    livro.erro_mensagem = None
    return livro


def _make_personagem(
    personagem_id: int = 10,
    *,
    cd_voz: int | None = 100,
    tx_genero: str | None = "Male",
    tx_idade: str | None = "Adult",
    tx_personagem: str = "Joao",
    tx_voz_referencia_path: str | None = None,
    tx_voz_origem: str | None = "catalogo",
    fl_voz_aprovada: str | None = "N",
) -> MagicMock:
    """Constroi um mock de ``LivroPersonagem``."""
    p = MagicMock(spec=LivroPersonagem)
    p.cd_sequencial = personagem_id
    p.cd_sequenciallivro = 1
    p.tx_personagem = tx_personagem
    p.tx_genero = tx_genero
    p.tx_idade = tx_idade
    p.cd_voz = cd_voz
    p.tx_voz_referencia_path = tx_voz_referencia_path
    p.tx_voz_origem = tx_voz_origem
    p.fl_voz_aprovada = fl_voz_aprovada
    p.falas = []
    return p


def _make_fala(
    fala_id: int = 1000,
    *,
    cd_sequencialpersonagem: int = 10,
    nr_ordem: int | None = 1,
    nr_chunk: int | None = None,
    caminho_audio_chunk: str | None = None,
    fl_processado: str | None = "N",
    tx_fala: str = "Ola mundo",
) -> MagicMock:
    """Constroi um mock de ``LivroFala``."""
    fala = MagicMock(spec=LivroFala)
    fala.cd_sequencial = fala_id
    fala.cd_sequenciallivro = 1
    fala.cd_sequencialpagina = 1
    fala.cd_sequencialpersonagem = cd_sequencialpersonagem
    fala.nr_ordem = nr_ordem
    fala.nr_chunk = nr_chunk
    fala.caminho_audio_chunk = caminho_audio_chunk
    fala.fl_processado = fl_processado
    fala.tx_fala = tx_fala
    fala.tx_instrucao_emocao = None
    fala.tx_instrucao_prosodia = None
    fala.tx_instrucao_paralinguistica = None
    fala.eh_narracao = "N"
    return fala


# -----------------------------------------------------------------------------
# Testes de excecoes
# -----------------------------------------------------------------------------


class TestExcecoes:
    """Garante a hierarquia das excecoes do pipeline."""

    def test_pipeline_error_e_base(self) -> None:
        assert issubclass(PipelineError, Exception)

    def test_pipeline_pausado_herda_de_pipeline_error(self) -> None:
        assert issubclass(PipelinePausadoError, PipelineError)

    def test_pipeline_erro_herda_de_pipeline_error(self) -> None:
        assert issubclass(PipelineErroError, PipelineError)

    def test_livro_nao_encontrado_herda_de_pipeline_error(self) -> None:
        assert issubclass(LivroNaoEncontradoError, PipelineError)

    def test_excecoes_podem_ser_instanciadas(self) -> None:
        assert PipelineError("x")
        assert PipelinePausadoError("pausa")
        assert PipelineErroError("erro")
        assert LivroNaoEncontradoError("nao achei")

    def test_captura_generica_via_base(self) -> None:
        with pytest.raises(PipelineError):
            raise PipelinePausadoError("p")
        with pytest.raises(PipelineError):
            raise PipelineErroError("e")
        with pytest.raises(PipelineError):
            raise LivroNaoEncontradoError("x")


# -----------------------------------------------------------------------------
# Testes do construtor
# -----------------------------------------------------------------------------


class TestConstrutor:
    """Garante que o construtor instancia servicos padrao."""

    def test_injecao_de_dependencia(self) -> None:
        llm = MagicMock(name="llm")
        tts = MagicMock(name="tts")
        pdf = MagicMock(name="pdf")
        personagens = MagicMock(name="personagens")
        catalogacao = MagicMock(name="catalogacao")

        orq = PipelineOrquestrador(
            llm=llm,
            tts=tts,
            pdf=pdf,
            personagens=personagens,
            catalogacao=catalogacao,
        )
        assert orq.llm is llm
        assert orq.tts is tts
        assert orq.pdf is pdf
        assert orq.personagens is personagens
        assert orq.catalogacao is catalogacao

    def test_total_etapas_constante(self) -> None:
        assert TOTAL_ETAPAS == 7

    def test_instancia_servicos_padrao_quando_none(
        self, mock_sessao: MagicMock
    ) -> None:
        """Sem injecao, o construtor cria instancias de servicos.

        Como ``TTSServico`` faz conexao HTTP, o orquestrador usa
        um stub leve quando a classe nao pode ser instanciada sem
        o servidor. Aqui validamos apenas que pdf/llm/personagens
        foram criados.
        """
        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador()
        # PDFService concreto
        assert orq.pdf is not None
        assert hasattr(orq.pdf, "processar_pdf")
        # LLMServico concreto (pode ser None se o construtor falhar,
        # mas neste caso o openai SDK esta instalado, entao cria)
        assert orq.llm is not None
        # Personagens (stub do orquestrador) sempre presente
        assert orq.personagens is not None
        # Catalogacao: depende do dataset existir ou nao
        # (pode ser None, mas o atributo existe no objeto)
        assert hasattr(orq, "catalogacao")


# -----------------------------------------------------------------------------
# Testes de _verificar_pausa
# -----------------------------------------------------------------------------


class TestVerificarPausa:
    """Cobre a verificacao de pausa entre etapas."""

    def test_nao_pausado_segue_normalmente(
        self, mock_sessao: MagicMock, livro_mock: MagicMock
    ) -> None:
        livro = _make_livro(fila_pausado="N", estado="aguardando")
        # Configura a busca do repositorio para retornar o livro
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = livro

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            # Nao deve levantar
            orq._verificar_pausa(1)

    def test_pausado_pela_flag_fila_pausado_levanta_excecao(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro(fila_pausado="S", estado="extracao")
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = livro

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            with pytest.raises(PipelinePausadoError):
                orq._verificar_pausa(1)

    def test_pausado_pelo_estado_levanta_excecao(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro(fila_pausado="N", estado="pausado")
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = livro

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            with pytest.raises(PipelinePausadoError):
                orq._verificar_pausa(1)

    def test_livro_nao_encontrado_levanta_excecao(
        self, mock_sessao: MagicMock
    ) -> None:
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = None

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            with pytest.raises(LivroNaoEncontradoError):
                orq._verificar_pausa(999)

    def test_pausado_atualiza_estado_se_necessario(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro(fila_pausado="S", estado="extracao")
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = livro

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            with pytest.raises(PipelinePausadoError):
                orq._verificar_pausa(1)
        # Como o estado ja era 'extracao' (diferente de 'pausado'),
        # o orquestrador deve ter atualizado para 'pausado'.
        assert livro.estado_pipeline == EstadoPipeline.PAUSADO.value


# -----------------------------------------------------------------------------
# Testes de _salvar_checkpoint
# -----------------------------------------------------------------------------


class TestSalvarCheckpoint:
    """Cobre a persistencia de progresso apos cada etapa."""

    @pytest.mark.parametrize(
        "etapa,estado_esperado,progresso_esperado",
        [
            ("extraer_texto", "extracao", 1),
            ("identificar_personagens", "personagens", 2),
            ("normalizar_personagens", "personagens", 3),
            ("definir_vozes", "vozes", 4),
            ("inferir_emocoes", "producao", 5),
            ("gerar_audio", "producao", 6),
            ("juncar_audio", "concluido", TOTAL_ETAPAS),
        ],
    )
    def test_checkpoint_por_etapa(
        self,
        mock_sessao: MagicMock,
        etapa: str,
        estado_esperado: str,
        progresso_esperado: int,
    ) -> None:
        livro = _make_livro()
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = livro

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            orq._salvar_checkpoint(1, etapa)

        assert livro.estado_pipeline == estado_esperado
        assert livro.progresso_atual == progresso_esperado
        assert livro.progresso_total == TOTAL_ETAPAS


# -----------------------------------------------------------------------------
# Testes de _atualizar_erro
# -----------------------------------------------------------------------------


class TestAtualizarErro:
    """Cobre a marcacao de estado de erro no banco."""

    def test_atualizar_erro_persiste_mensagem(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro()
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = livro

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            orq._atualizar_erro(1, "PDF corrompido")

        assert livro.estado_pipeline == EstadoPipeline.ERRO.value
        assert livro.erro_mensagem == "PDF corrompido"


# -----------------------------------------------------------------------------
# Testes de etapas individuais (com servicos mocados)
# -----------------------------------------------------------------------------


class TestEtapaExtracao:
    """Testa a etapa 1 (extracao de texto)."""

    def test_extraer_texto_chama_pdf_service(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro(caminho_pdf="/tmp/foo.pdf")
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = livro

        pdf_mock = MagicMock()
        pdf_mock.processar_pdf.return_value = 10

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=pdf_mock,
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            orq._extraer_texto(1)

        pdf_mock.processar_pdf.assert_called_once_with(1, "/tmp/foo.pdf")
        assert livro.estado_pipeline == EstadoPipeline.EXTRACAO.value

    def test_extraer_texto_sem_caminho_pdf_falha(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro(caminho_pdf=None)
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = livro

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            with pytest.raises(PipelineErroError):
                orq._extraer_texto(1)


class TestEtapaIdentificarPersonagens:
    """Testa a etapa 2 (identificacao de personagens)."""

    def test_chama_servico_quando_disponivel(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro()
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = livro

        personagens = MagicMock()
        personagens.identificar_personagens_por_pagina.return_value = 5

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=personagens, catalogacao=MagicMock(),
            )
            orq._identificar_personagens(1)

        personagens.identificar_personagens_por_pagina.assert_called_once_with(1)
        assert livro.estado_pipeline == EstadoPipeline.PERSONAGENS.value

    def test_nao_falha_se_metodo_ausente(
        self, mock_sessao: MagicMock
    ) -> None:
        """Se o servico nao implementar o metodo, a etapa nao quebra."""
        livro = _make_livro()
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = livro

        personagens = MagicMock(spec=[])  # sem metodos

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=personagens, catalogacao=MagicMock(),
            )
            # Nao deve levantar
            orq._identificar_personagens(1)


class TestEtapaDefinirVozes:
    """Testa a etapa 4 (definicao de vozes)."""

    def test_atribui_sugestao_para_personagem_sem_voz(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro()
        personagem = _make_personagem(cd_voz=None, tx_genero="Male", tx_idade="Adult")
        # A primeira chamada (no _buscar_livro_ou_erro) retorna o livro,
        # a segunda (no listar_por_livro) deve retornar a lista
        # de personagens. Implementamos via ``side_effect``.
        from sqlalchemy import select
        from app.repositories.models.livro_cabecalho import LivroCabecalho
        from app.repositories.models.livro_personagem import LivroPersonagem

        def fake_execute(stmt: Any) -> Any:
            resultado = MagicMock()
            # Detecta a query: para LivroCabecalho retorna o livro,
            # para LivroPersonagem retorna a lista
            if stmt.column_descriptions[0]["type"] is LivroCabecalho:
                resultado.scalar_one_or_none.return_value = livro
            elif stmt.column_descriptions[0]["type"] is LivroPersonagem:
                resultado.scalars.return_value.all.return_value = [personagem]
            else:
                resultado.scalars.return_value.all.return_value = [personagem]
                resultado.scalar_one_or_none.return_value = None
            return resultado

        mock_sessao.execute.side_effect = fake_execute

        sugestoes_mock = MagicMock()
        # Quando o orquestrador chama
        # ``catalogacao.sugerir_vozes_por_personagem(...)`` retornamos
        # um objeto com ``id`` igual ao nome do dataset.
        voz_info = SimpleNamespace(id="VOICE_001", categoria="Homens/Adulto")
        sugestoes_mock.sugerir_vozes_por_personagem.return_value = [voz_info]

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=sugestoes_mock,
            )
            orq._definir_vozes(1)

        assert personagem.cd_voz is not None
        assert personagem.cd_voz == abs(hash("VOICE_001")) % (2**31)

    def test_pula_personagem_ja_com_voz(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro()
        personagem = _make_personagem(cd_voz=200)

        def fake_execute(stmt: Any) -> Any:
            resultado = MagicMock()
            from app.repositories.models.livro_cabecalho import LivroCabecalho
            from app.repositories.models.livro_personagem import LivroPersonagem
            if stmt.column_descriptions[0]["type"] is LivroCabecalho:
                resultado.scalar_one_or_none.return_value = livro
            elif stmt.column_descriptions[0]["type"] is LivroPersonagem:
                resultado.scalars.return_value.all.return_value = [personagem]
            else:
                resultado.scalars.return_value.all.return_value = [personagem]
            return resultado

        mock_sessao.execute.side_effect = fake_execute

        catalogacao = MagicMock()
        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=catalogacao,
            )
            orq._definir_vozes(1)

        # Catalogacao NAO foi chamada (ja tinha voz)
        catalogacao.sugerir_vozes_por_personagem.assert_not_called()


class TestEtapaInferirEmocoes:
    """Testa a etapa 5 (inferir emocoes)."""

    def test_inferir_emocao_para_cada_fala(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro()
        fala = _make_fala(cd_sequencialpersonagem=10)

        def fake_execute(stmt: Any) -> Any:
            from app.repositories.models.livro_cabecalho import LivroCabecalho
            from app.repositories.models.livro_fala import LivroFala
            resultado = MagicMock()
            if stmt.column_descriptions[0]["type"] is LivroCabecalho:
                resultado.scalar_one_or_none.return_value = livro
            elif stmt.column_descriptions[0]["type"] is LivroFala:
                resultado.scalars.return_value.all.return_value = [fala]
            else:
                resultado.scalar_one_or_none.return_value = None
            return resultado

        mock_sessao.execute.side_effect = fake_execute

        llm_mock = MagicMock()
        llm_mock.inferir_emocao.return_value = {
            "emocao": "alegre",
            "prosodia": "rapido",
            "paralinguistica": "",
        }

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=llm_mock, tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            orq._inferir_emocoes(1)

        llm_mock.inferir_emocao.assert_called_once_with("Ola mundo")
        assert fala.tx_instrucao_emocao == "alegre"
        assert fala.tx_instrucao_prosodia == "rapido"

    def test_etapa_sem_falas_nao_falha(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro()

        def fake_execute(stmt: Any) -> Any:
            from app.repositories.models.livro_cabecalho import LivroCabecalho
            from app.repositories.models.livro_fala import LivroFala
            resultado = MagicMock()
            if stmt.column_descriptions[0]["type"] is LivroCabecalho:
                resultado.scalar_one_or_none.return_value = livro
            elif stmt.column_descriptions[0]["type"] is LivroFala:
                resultado.scalars.return_value.all.return_value = []
            else:
                resultado.scalar_one_or_none.return_value = None
            return resultado

        mock_sessao.execute.side_effect = fake_execute

        llm_mock = MagicMock()
        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=llm_mock, tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            orq._inferir_emocoes(1)

        llm_mock.inferir_emocao.assert_not_called()


class TestEtapaGerarAudio:
    """Testa a etapa 6 (geracao de audio)."""

    def test_gerar_audio_chama_tts_para_personagem_com_voz(
        self, mock_sessao: MagicMock, tmp_path: Path
    ) -> None:
        livro = _make_livro()
        personagem = _make_personagem(cd_voz=100)
        fala = _make_fala(cd_sequencialpersonagem=10, tx_fala="Texto simples")

        # Cria arquivo de audio fake para a referencia
        ref_audio = tmp_path / "ref.wav"
        ref_audio.write_bytes(b"FAKE_WAV")

        personagem.tx_voz_referencia_path = str(ref_audio)

        def fake_execute(stmt: Any) -> Any:
            from app.repositories.models.livro_cabecalho import LivroCabecalho
            from app.repositories.models.livro_fala import LivroFala
            from app.repositories.models.livro_personagem import LivroPersonagem
            resultado = MagicMock()
            if stmt.column_descriptions[0]["type"] is LivroCabecalho:
                resultado.scalar_one_or_none.return_value = livro
            elif stmt.column_descriptions[0]["type"] is LivroPersonagem:
                resultado.scalars.return_value.all.return_value = [personagem]
            elif stmt.column_descriptions[0]["type"] is LivroFala:
                resultado.scalars.return_value.all.return_value = [fala]
            else:
                resultado.scalar_one_or_none.return_value = None
            return resultado

        mock_sessao.execute.side_effect = fake_execute

        tts_mock = MagicMock()
        tts_mock._dividir_em_chunks.side_effect = lambda texto: [texto]
        tts_mock.gerar_audio_lote.return_value = [b"WAV_BYTES_1"]

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            with patch(
                "tasks.pipeline.get_settings"
            ) as settings_mock:
                settings_mock.return_value.audio_output_path = tmp_path
                orq = PipelineOrquestrador(
                    llm=MagicMock(), tts=tts_mock, pdf=MagicMock(),
                    personagens=MagicMock(), catalogacao=MagicMock(),
                )
                orq._gerar_audio(1)

        tts_mock.gerar_audio_lote.assert_called_once()
        assert fala.fl_processado == "S"
        assert fala.caminho_audio_chunk is not None

    def test_gerar_audio_sem_personagens_com_voz_pula(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro()

        def fake_execute(stmt: Any) -> Any:
            from app.repositories.models.livro_cabecalho import LivroCabecalho
            from app.repositories.models.livro_personagem import LivroPersonagem
            resultado = MagicMock()
            if stmt.column_descriptions[0]["type"] is LivroCabecalho:
                resultado.scalar_one_or_none.return_value = livro
            elif stmt.column_descriptions[0]["type"] is LivroPersonagem:
                resultado.scalars.return_value.all.return_value = []
            else:
                resultado.scalar_one_or_none.return_value = None
            return resultado

        mock_sessao.execute.side_effect = fake_execute

        tts_mock = MagicMock()
        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=tts_mock, pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            orq._gerar_audio(1)

        tts_mock.gerar_audio_lote.assert_not_called()


class TestEtapaJuncarAudio:
    """Testa a etapa 7 (juncao de audio)."""

    def test_juncao_sem_chunks_falha(
        self, mock_sessao: MagicMock, tmp_path: Path
    ) -> None:
        livro = _make_livro()

        def fake_execute(stmt: Any) -> Any:
            from app.repositories.models.livro_cabecalho import LivroCabecalho
            from app.repositories.models.livro_fala import LivroFala
            resultado = MagicMock()
            if stmt.column_descriptions[0]["type"] is LivroCabecalho:
                resultado.scalar_one_or_none.return_value = livro
            elif stmt.column_descriptions[0]["type"] is LivroFala:
                resultado.scalars.return_value.all.return_value = []
            else:
                resultado.scalar_one_or_none.return_value = None
            return resultado

        mock_sessao.execute.side_effect = fake_execute

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            with patch(
                "tasks.pipeline.get_settings"
            ) as settings_mock:
                settings_mock.return_value.audio_output_path = tmp_path
                orq = PipelineOrquestrador(
                    llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                    personagens=MagicMock(), catalogacao=MagicMock(),
                )
                with pytest.raises(PipelineErroError):
                    orq._juncar_audio(1)

    def test_juncao_com_chunks_marca_concluido(
        self, mock_sessao: MagicMock, tmp_path: Path
    ) -> None:
        livro = _make_livro()
        # Cria um chunk de audio no disco para a juncao ler
        chunk1 = tmp_path / "livro_1" / "fala_1_chunk_1.wav"
        chunk1.parent.mkdir(parents=True, exist_ok=True)
        chunk1.write_bytes(b"FAKE_WAV_CONTENT")
        fala = _make_fala(
            1, nr_ordem=1, nr_chunk=1,
            caminho_audio_chunk=str(chunk1), fl_processado="S",
        )

        def fake_execute(stmt: Any) -> Any:
            from app.repositories.models.livro_cabecalho import LivroCabecalho
            from app.repositories.models.livro_fala import LivroFala
            resultado = MagicMock()
            if stmt.column_descriptions[0]["type"] is LivroCabecalho:
                resultado.scalar_one_or_none.return_value = livro
            elif stmt.column_descriptions[0]["type"] is LivroFala:
                resultado.scalars.return_value.all.return_value = [fala]
            else:
                resultado.scalar_one_or_none.return_value = None
            return resultado

        mock_sessao.execute.side_effect = fake_execute

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            with patch(
                "tasks.pipeline.get_settings"
            ) as settings_mock:
                settings_mock.return_value.audio_output_path = tmp_path
                # Garante que soundfile e numpy nao estejam disponiveis
                # para cair no caminho de fallback (copia simples).
                with patch.dict(
                    "sys.modules",
                    {
                        "soundfile": None,
                        "numpy": None,
                    },
                ):
                    orq = PipelineOrquestrador(
                        llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                        personagens=MagicMock(), catalogacao=MagicMock(),
                    )
                    orq._juncar_audio(1)

        assert livro.estado_pipeline == EstadoPipeline.CONCLUIDO.value
        assert livro.fl_produzido == "S"
        assert livro.caminho_audio_final is not None


# -----------------------------------------------------------------------------
# Testes de executar_pipeline (fluxo completo)
# -----------------------------------------------------------------------------


class TestExecutarPipeline:
    """Cobre a execucao completa do pipeline."""

    def test_executa_todas_as_etapas_em_ordem(
        self, mock_sessao: MagicMock, tmp_path
    ) -> None:
        livro = _make_livro()
        # Cria um arquivo WAV fake para a juncao encontrar
        chunk = tmp_path / "chunk1.wav"
        chunk.write_bytes(b"RIFF" + b"\x00" * 36)
        fala_mock = MagicMock()
        fala_mock.caminho_audio_chunk = str(chunk)
        fala_mock.nr_ordem = 1
        fala_mock.nr_chunk = 1

        # Cada etapa abre sua propria session_scope. Configuramos
        # o mock para sempre retornar o livro quando uma query
        # pelo cabecalho for executada.
        def fake_execute(stmt: Any) -> Any:
            from app.repositories.models.livro_cabecalho import LivroCabecalho
            from app.repositories.models.livro_fala import LivroFala
            from app.repositories.models.livro_pagina import LivroPagina
            from app.repositories.models.livro_personagem import LivroPersonagem
            resultado = MagicMock()
            tipo = stmt.column_descriptions[0]["type"]
            if tipo is LivroCabecalho:
                resultado.scalar_one_or_none.return_value = livro
            elif tipo is LivroPersonagem:
                resultado.scalars.return_value.all.return_value = []
            elif tipo is LivroFala:
                # Juncao busca falas com caminho_audio_chunk
                resultado.scalars.return_value.all.return_value = [fala_mock]
            elif tipo is LivroPagina:
                resultado.scalars.return_value.all.return_value = []
            else:
                resultado.scalar_one_or_none.return_value = None
                resultado.scalars.return_value.all.return_value = []
            return resultado

        mock_sessao.execute.side_effect = fake_execute

        pdf_mock = MagicMock()
        pdf_mock.processar_pdf.return_value = 1
        personagens_mock = MagicMock()
        personagens_mock.identificar_personagens_por_pagina.return_value = 1
        personagens_mock.normalizar_personagens.return_value = 1
        catalogacao_mock = MagicMock()
        catalogacao_mock.sugerir_vozes_por_personagem.return_value = []
        llm_mock = MagicMock()
        llm_mock.inferir_emocao.return_value = {
            "emocao": "", "prosodia": "", "paralinguistica": "",
        }
        tts_mock = MagicMock()
        tts_mock._dividir_em_chunks.side_effect = lambda texto: [texto]
        tts_mock.gerar_audio_lote.return_value = []

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            with patch(
                "tasks.pipeline.get_settings"
            ) as settings_mock:
                settings_mock.return_value.audio_output_path = tmp_path
                orq = PipelineOrquestrador(
                    llm=llm_mock, tts=tts_mock, pdf=pdf_mock,
                    personagens=personagens_mock,
                    catalogacao=catalogacao_mock,
                )
                orq.executar_pipeline(1)

        # Validar que cada servico foi chamado pelo menos uma vez
        pdf_mock.processar_pdf.assert_called_once()
        personagens_mock.identificar_personagens_por_pagina.assert_called_once()
        personagens_mock.normalizar_personagens.assert_called_once()
        catalogacao_mock.sugerir_vozes_por_personagem.assert_not_called()  # sem personagens
        tts_mock.gerar_audio_lote.assert_not_called()  # sem personagens com voz

    def test_erro_em_etapa_marca_estado_e_relanca(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro()
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = livro

        pdf_mock = MagicMock()
        pdf_mock.processar_pdf.side_effect = RuntimeError("PDF corrompido")

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=pdf_mock,
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            with pytest.raises(RuntimeError, match="PDF corrompido"):
                orq.executar_pipeline(1)

        # Estado deve ter sido marcado como erro
        assert livro.estado_pipeline == EstadoPipeline.ERRO.value
        assert livro.erro_mensagem is not None
        assert "PDF corrompido" in livro.erro_mensagem

    def test_pausa_durante_execucao_aborta_sem_marcar_erro(
        self, mock_sessao: MagicMock
    ) -> None:
        """Se o livro for pausado, o orquestrador aborta sem marcar erro."""
        # Primeira chamada: livro "nao pausado"; segunda: ja pausado
        livro1 = _make_livro(fila_pausado="N", estado="aguardando")
        livro2 = _make_livro(fila_pausado="S", estado="pausado")

        def fake_execute(stmt: Any) -> Any:
            from app.repositories.models.livro_cabecalho import LivroCabecalho
            resultado = MagicMock()
            if stmt.column_descriptions[0]["type"] is LivroCabecalho:
                # Alterna o livro retornado para simular pausa durante o loop
                if not getattr(fake_execute, "chamado", False):
                    fake_execute.chamado = True
                    resultado.scalar_one_or_none.return_value = livro1
                else:
                    resultado.scalar_one_or_none.return_value = livro2
            else:
                resultado.scalar_one_or_none.return_value = None
                resultado.scalars.return_value.all.return_value = []
            return resultado

        mock_sessao.execute.side_effect = fake_execute

        pdf_mock = MagicMock()
        # Apos a extracao (que eh a primeira etapa), o _verificar_pausa
        # da proxima etapa vai detectar a pausa
        def side_effect_pdf(*args: Any, **kwargs: Any) -> int:
            # Apos processar_pdf, mudamos o estado de fila_pausado
            # do livro1 para simular pausa
            livro1.fila_pausado = "S"
            return 5

        pdf_mock.processar_pdf.side_effect = side_effect_pdf

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=pdf_mock,
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            with pytest.raises(PipelinePausadoError):
                orq.executar_pipeline(1)

        # Como o orquestrador ja gravou o checkpoint da etapa 1,
        # o estado do livro NAO deve ser "erro" (foi uma pausa).
        assert livro1.estado_pipeline != EstadoPipeline.ERRO.value

    def test_livro_nao_encontrado_levanta_excecao_sem_marcar_erro(
        self, mock_sessao: MagicMock
    ) -> None:
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = None

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            with pytest.raises(LivroNaoEncontradoError):
                orq.executar_pipeline(999)


# -----------------------------------------------------------------------------
# Testes de retomar_pipeline
# -----------------------------------------------------------------------------


class TestRetomarPipeline:
    """Cobre a retomada de pipeline a partir de checkpoints."""

    def test_livro_ja_concluido_nao_executa_nada(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro(
            estado="concluido", fl_produzido="S", progresso_atual=7
        )
        mock_sessao.execute.return_value.scalar_one_or_none.return_value = livro

        pdf_mock = MagicMock()
        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            orq = PipelineOrquestrador(
                llm=MagicMock(), tts=MagicMock(), pdf=pdf_mock,
                personagens=MagicMock(), catalogacao=MagicMock(),
            )
            orq.retomar_pipeline(1)

        pdf_mock.processar_pdf.assert_not_called()

    def test_retomada_pula_etapas_ja_concluidas(
        self, mock_sessao: MagicMock, tmp_path
    ) -> None:
        livro = _make_livro(
            estado="producao",
            fl_lido="S",  # etapa 1 ja feita
            fl_normalizado="S",  # etapas 2 e 3 ja feitas
            fl_narrador="N",
            fl_produzido="N",
            progresso_atual=2,
        )
        # Cria chunk fake para juncao nao falhar
        chunk = tmp_path / "chunk1.wav"
        chunk.write_bytes(b"RIFF" + b"\x00" * 36)
        fala_mock = MagicMock()
        fala_mock.caminho_audio_chunk = str(chunk)
        fala_mock.nr_ordem = 1
        fala_mock.nr_chunk = 1

        def fake_execute(stmt: Any) -> Any:
            from app.repositories.models.livro_cabecalho import LivroCabecalho
            from app.repositories.models.livro_fala import LivroFala
            from app.repositories.models.livro_personagem import LivroPersonagem
            resultado = MagicMock()
            tipo = stmt.column_descriptions[0]["type"]
            if tipo is LivroCabecalho:
                resultado.scalar_one_or_none.return_value = livro
            elif tipo is LivroPersonagem:
                resultado.scalars.return_value.all.return_value = []
            elif tipo is LivroFala:
                resultado.scalars.return_value.all.return_value = [fala_mock]
            else:
                resultado.scalar_one_or_none.return_value = None
                resultado.scalars.return_value.all.return_value = []
            return resultado

        mock_sessao.execute.side_effect = fake_execute

        pdf_mock = MagicMock()
        catalogacao_mock = MagicMock()
        catalogacao_mock.sugerir_vozes_por_personagem.return_value = []
        llm_mock = MagicMock()
        tts_mock = MagicMock()
        tts_mock._dividir_em_chunks.side_effect = lambda texto: [texto]
        tts_mock.gerar_audio_lote.return_value = []

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            with patch(
                "tasks.pipeline.get_settings"
            ) as settings_mock:
                settings_mock.return_value.audio_output_path = tmp_path
                orq = PipelineOrquestrador(
                    llm=llm_mock, tts=tts_mock, pdf=pdf_mock,
                    personagens=MagicMock(), catalogacao=catalogacao_mock,
                )
                orq.retomar_pipeline(1)

        # Extracao NAO deve rodar (fl_lido='S')
        pdf_mock.processar_pdf.assert_not_called()
        # Catalogacao NAO deve rodar (sem personagens)
        catalogacao_mock.sugerir_vozes_por_personagem.assert_not_called()


# -----------------------------------------------------------------------------
# Testes da tarefa Celery
# -----------------------------------------------------------------------------


class TestTarefaCelery:
    """Cobre ``executar_pipeline_task`` e ``retomar_pipeline_task``."""

    def test_sucesso_retorna_status_concluido(
        self, mock_sessao: MagicMock
    ) -> None:
        livro = _make_livro()

        def fake_execute(stmt: Any) -> Any:
            from app.repositories.models.livro_cabecalho import LivroCabecalho
            from app.repositories.models.livro_fala import LivroFala
            from app.repositories.models.livro_personagem import LivroPersonagem
            from app.repositories.models.livro_pagina import LivroPagina
            resultado = MagicMock()
            tipo = stmt.column_descriptions[0]["type"]
            if tipo is LivroCabecalho:
                resultado.scalar_one_or_none.return_value = livro
            elif tipo in (LivroPersonagem, LivroFala, LivroPagina):
                resultado.scalars.return_value.all.return_value = []
            else:
                resultado.scalar_one_or_none.return_value = None
                resultado.scalars.return_value.all.return_value = []
            return resultado

        mock_sessao.execute.side_effect = fake_execute

        # Mocka o PipelineOrquestrador inteiro para nao depender dos servicos
        mock_pipeline = MagicMock()
        mock_pipeline.executar_pipeline.return_value = None

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            with patch(
                "tasks.pipeline.get_settings"
            ) as settings_mock:
                settings_mock.return_value.audio_output_path = Path("/tmp/fake")
                with patch("tasks.pipeline.PipelineOrquestrador", return_value=mock_pipeline):
                    resultado = executar_pipeline_task.run(livro_id=1)
        assert resultado == {"livro_id": 1, "status": "concluido"}

    def test_pausa_retorna_status_pausado_sem_retry(
        self, mock_sessao: MagicMock
    ) -> None:
        from tasks.exceptions import PipelinePausadoError

        # Pipeline mockado que levanta PipelinePausadoError
        mock_pipeline = MagicMock()
        mock_pipeline.executar_pipeline.side_effect = PipelinePausadoError("pausado")

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            with patch("tasks.pipeline.PipelineOrquestrador", return_value=mock_pipeline):
                resultado = executar_pipeline_task.run(livro_id=1)

        assert resultado["status"] == "pausado"
        assert resultado["livro_id"] == 1

    def test_erro_generico_chama_retry(
        self, mock_sessao: MagicMock
    ) -> None:
        """Erros nao-pausa disparam o retry do Celery."""
        # Pipeline mockado que levanta erro generico
        mock_pipeline = MagicMock()
        mock_pipeline.executar_pipeline.side_effect = RuntimeError("falha grave")

        # Cria self mock com retry que levanta RuntimeError
        self_mock = MagicMock()
        self_mock.retry.side_effect = RuntimeError("retry_called")

        with patch(
            "tasks.pipeline.session_scope",
            lambda: fake_session_scope(mock_sessao),
        ):
            with patch("tasks.pipeline.PipelineOrquestrador", return_value=mock_pipeline):
                # Chama diretamente a task passando self_mock posicional e livro_id=1
                with pytest.raises(RuntimeError, match="retry_called"):
                    executar_pipeline_task.__wrapped__.__func__(self_mock, 1)

        # O retry foi chamado com countdown=60
        self_mock.retry.assert_called_once()
        kwargs = self_mock.retry.call_args.kwargs
        assert kwargs.get("countdown") == 60


# -----------------------------------------------------------------------------
# Testes de _concatenar_wavs (fallback sem numpy/soundfile)
# -----------------------------------------------------------------------------


class TestConcatenarWavs:
    """Cobre a juncao de WAVs em diferentes cenarios."""

    def test_sem_numpy_soundfile_faz_copia_simples(
        self, tmp_path: Path
    ) -> None:
        chunk = tmp_path / "chunk1.wav"
        chunk.write_bytes(b"FAKE")
        destino = tmp_path / "final.wav"

        fala = _make_fala(caminho_audio_chunk=str(chunk))
        orq = PipelineOrquestrador(
            llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
            personagens=MagicMock(), catalogacao=MagicMock(),
        )

        with patch.dict("sys.modules", {"soundfile": None, "numpy": None}):
            orq._concatenar_wavs([fala], destino)

        assert destino.exists()
        assert destino.read_bytes() == b"FAKE"

    def test_sem_chunks_validos_nao_falha(
        self, tmp_path: Path
    ) -> None:
        """Se nenhuma fala tem audio, nao ha erro e o destino nao e criado."""
        destino = tmp_path / "final.wav"
        orq = PipelineOrquestrador(
            llm=MagicMock(), tts=MagicMock(), pdf=MagicMock(),
            personagens=MagicMock(), catalogacao=MagicMock(),
        )

        with patch.dict("sys.modules", {"soundfile": None, "numpy": None}):
            orq._concatenar_wavs([], destino)

        # Sem chunks, o destino NAO deve existir (nada a copiar)
        assert not destino.exists()


# -----------------------------------------------------------------------------
# Testes de helpers internos
# -----------------------------------------------------------------------------


class TestHashVozParaInt:
    """Cobre a conversao de id textual para inteiro."""

    def test_hash_estavel(self) -> None:
        from tasks.pipeline import _hash_voz_para_int

        id1 = _hash_voz_para_int("VOICE_001")
        id2 = _hash_voz_para_int("VOICE_001")
        assert id1 == id2
        assert isinstance(id1, int)
        assert id1 >= 0

    def test_hash_diferente_para_ids_diferentes(self) -> None:
        from tasks.pipeline import _hash_voz_para_int

        id1 = _hash_voz_para_int("VOICE_001")
        id2 = _hash_voz_para_int("VOICE_002")
        assert id1 != id2

    def test_hash_dentro_limite_31bits(self) -> None:
        from tasks.pipeline import _hash_voz_para_int

        for i in range(100):
            v = _hash_voz_para_int(f"VOICE_{i:04d}")
            assert 0 <= v < 2**31


# -----------------------------------------------------------------------------
# Fixture parametrica auxiliar
# -----------------------------------------------------------------------------


@pytest.fixture
def livro_mock() -> MagicMock:
    """Mock de livro para testes que nao customizam."""
    return _make_livro()
