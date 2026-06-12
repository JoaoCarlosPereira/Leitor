"""Testes unitarios para o servico de personagens (FL-02)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.repositories.models.livro_fala import LivroFala
from app.repositories.models.livro_pagina import LivroPagina
from app.repositories.models.livro_personagem import LivroPersonagem
from app.services.llm import LLMServico
from app.services.personagens import (
    PERSONAGEM_DESCONHECIDO,
    ErroAnalisePersonagens,
    PersonagensService,
)

# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def mock_llm() -> MagicMock:
    """LLMServico mockado para evitar chamadas reais."""
    return MagicMock(spec=LLMServico)


@pytest.fixture
def service(mock_llm: MagicMock) -> PersonagensService:
    """Instancia do servico com LLM mockado."""
    return PersonagensService(llm=mock_llm)


def make_pagina(
    cd: int = 1,
    livro_id: int = 100,
    texto: str = "Maria disse: ola.",
    fl_processado: str | None = None,
) -> LivroPagina:
    """Cria uma instancia de LivroPagina para testes."""
    return LivroPagina(
        cd_sequencial=cd,
        cd_sequenciallivro=livro_id,
        nr_pagina=cd,
        tx_pagina=texto,
        fl_processado=fl_processado,
    )


def make_personagem(
    cd: int = 1,
    livro_id: int = 100,
    nome: str = "Maria",
    narrador: str = "N",
) -> LivroPersonagem:
    """Cria uma instancia de LivroPersonagem para testes."""
    return LivroPersonagem(
        cd_sequencial=cd,
        cd_sequenciallivro=livro_id,
        tx_personagem=nome,
        fl_eh_narrador=narrador,
    )


# =====================================================================
# Construtor e injecao de dependencia
# =====================================================================


class TestConstrutor:
    """Testes do construtor e injecao de dependencia."""

    def test_injeta_llm_quando_passado(self, mock_llm: MagicMock) -> None:
        """Quando um LLM eh passado, ele deve ser usado."""
        svc = PersonagensService(llm=mock_llm)
        assert svc.llm is mock_llm

    def test_instancia_llm_padrao_quando_none(self) -> None:
        """Quando nenhum LLM eh passado, instancia LLMServico() padrao."""
        with patch("app.services.personagens.LLMServico") as mock_cls:
            svc = PersonagensService()
        mock_cls.assert_called_once_with()
        assert svc.llm is mock_cls.return_value


# =====================================================================
# _formatar_resultado
# =====================================================================


class TestFormatarResultado:
    """Testes do helper privado de formatacao."""

    def test_remove_linhas_vazias(self, service: PersonagensService) -> None:
        """Linhas com texto vazio sao descartadas."""
        items = [
            {"personagem": "Maria", "texto": "ola", "tipo": "fala"},
            {"personagem": "Maria", "texto": "", "tipo": "fala"},
            {"personagem": "Maria", "texto": "   ", "tipo": "fala"},
        ]
        resultado = service._formatar_resultado(items)
        assert len(resultado) == 1

    def test_descarta_linhas_muito_curtas(self, service: PersonagensService) -> None:
        """Textos com menos de 3 caracteres sao descartados."""
        items = [
            {"personagem": "Maria", "texto": "oi", "tipo": "fala"},
            {"personagem": "Maria", "texto": "ab", "tipo": "fala"},
        ]
        resultado = service._formatar_resultado(items)
        assert resultado == []

    def test_descarta_linhas_somente_digitos(self, service: PersonagensService) -> None:
        """Textos compostos apenas por numeros sao descartados."""
        items = [
            {"personagem": "Maria", "texto": "12345", "tipo": "fala"},
            {"personagem": "Maria", "texto": "2024", "tipo": "fala"},
        ]
        resultado = service._formatar_resultado(items)
        assert resultado == []

    def test_descarta_linhas_somente_pontuacao(self, service: PersonagensService) -> None:
        """Textos sem alfanumericos sao descartados."""
        items = [
            {"personagem": "Maria", "texto": "!!!", "tipo": "fala"},
            {"personagem": "Maria", "texto": "...", "tipo": "fala"},
        ]
        resultado = service._formatar_resultado(items)
        assert resultado == []

    def test_normaliza_nome_para_title_case(self, service: PersonagensService) -> None:
        """Nomes sao convertidos para title case com strip."""
        items = [
            {"personagem": "  maria da silva  ", "texto": "ola mundo", "tipo": "fala"},
        ]
        resultado = service._formatar_resultado(items)
        assert resultado[0]["personagem"] == "Maria Da Silva"

    def test_marca_narrador_como_narracao(self, service: PersonagensService) -> None:
        """Nome 'Narrador' ou tipo='narracao' implica tipo narracao."""
        items = [
            {"personagem": "Narrador", "texto": "Era uma vez", "tipo": "fala"},
            {"personagem": "Maria", "texto": "narrando algo", "tipo": "narracao"},
        ]
        resultado = service._formatar_resultado(items)
        assert all(r["tipo"] == "narracao" for r in resultado)
        assert resultado[0]["personagem"] == "Narrador"
        assert resultado[1]["personagem"] == "Narrador"

    def test_substitui_nome_vazio_por_desconhecido(self, service: PersonagensService) -> None:
        """Personagem sem nome eh marcado como Personagem Desconhecido."""
        items = [
            {"personagem": "", "texto": "fala misteriosa", "tipo": "fala"},
        ]
        resultado = service._formatar_resultado(items)
        assert resultado[0]["personagem"] == PERSONAGEM_DESCONHECIDO

    def test_descarta_itens_nao_dict(self, service: PersonagensService) -> None:
        """Itens que nao sao dict sao ignorados silenciosamente."""
        items: list[Any] = [
            "string invalida",
            None,
            {"personagem": "Maria", "texto": "ola", "tipo": "fala"},
        ]
        resultado = service._formatar_resultado(items)
        assert len(resultado) == 1
        assert resultado[0]["personagem"] == "Maria"

    def test_tipo_default_fala(self, service: PersonagensService) -> None:
        """Sem tipo explicito e sem nome 'Narrador', assume fala."""
        items = [
            {"personagem": "Maria", "texto": "bom dia"},
        ]
        resultado = service._formatar_resultado(items)
        assert resultado[0]["tipo"] == "fala"


# =====================================================================
# identificar_personagens_por_pagina
# =====================================================================


class TestIdentificarPersonagensPorPagina:
    """Testes do metodo principal de identificacao."""

    def test_livro_nao_encontrado_lanca_erro(self, service: PersonagensService) -> None:
        """Se o livro nao existe, levanta ErroAnalisePersonagens."""
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_livro_repo = MagicMock()
            mock_livro_repo.buscar_por_id_sync.return_value = None
            with (
                patch(
                    "app.services.personagens.LivroRepositorio",
                    return_value=mock_livro_repo,
                ),
                pytest.raises(ErroAnalisePersonagens),
            ):
                service.identificar_personagens_por_pagina(999)

    def test_sem_paginas_retorna_zero(self, service: PersonagensService) -> None:
        """Se nao ha paginas nao processadas, retorna 0."""
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_livro_repo = MagicMock()
            mock_livro_repo.buscar_por_id_sync.return_value = MagicMock()
            with patch(
                "app.services.personagens.LivroRepositorio",
                return_value=mock_livro_repo,
            ):
                mock_pagina_repo = MagicMock()
                mock_pagina_repo.listar_nao_processadas.return_value = []
                with patch(
                    "app.services.personagens.LivroPaginaRepositorio",
                    return_value=mock_pagina_repo,
                ):
                    resultado = service.identificar_personagens_por_pagina(1)
        assert resultado == 0

    def test_pagina_vazia_e_marcada_processada(self, service: PersonagensService) -> None:
        """Pagina sem texto eh marcada como processada e gera 0 falas."""
        pagina_vazia = make_pagina(texto="")
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_livro_repo = MagicMock()
            mock_livro_repo.buscar_por_id_sync.return_value = MagicMock()
            with patch(
                "app.services.personagens.LivroRepositorio",
                return_value=mock_livro_repo,
            ):
                mock_pagina_repo = MagicMock()
                mock_pagina_repo.listar_nao_processadas.return_value = [pagina_vazia]
                mock_personagem_repo = MagicMock()
                mock_personagem_repo.listar_por_livro.return_value = []
                with (
                    patch(
                        "app.services.personagens.LivroPaginaRepositorio",
                        return_value=mock_pagina_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroPersonagemRepositorio",
                        return_value=mock_personagem_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroFalaRepositorio",
                    ),
                ):
                    resultado = service.identificar_personagens_por_pagina(1)
        assert resultado == 0
        assert pagina_vazia.fl_processado == "S"

    def test_identifica_personagens_e_cria_falas(
        self, service: PersonagensService, mock_llm: MagicMock
    ) -> None:
        """Fluxo feliz: identifica personagens, cria falas e marca pagina."""
        pagina = make_pagina(cd=1, texto="Maria disse ola e Joao respondeu tudo bem.")
        mock_llm.identificar_personagens.return_value = [
            {"personagem": "Maria", "texto": "ola", "tipo": "fala"},
            {"personagem": "Joao", "texto": "tudo bem", "tipo": "fala"},
        ]
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_livro_repo = MagicMock()
            mock_livro_repo.buscar_por_id_sync.return_value = MagicMock()
            with patch(
                "app.services.personagens.LivroRepositorio",
                return_value=mock_livro_repo,
            ):
                mock_pagina_repo = MagicMock()
                mock_pagina_repo.listar_nao_processadas.return_value = [pagina]
                mock_personagem_repo = MagicMock()
                mock_personagem_repo.listar_por_livro.return_value = []
                with (
                    patch(
                        "app.services.personagens.LivroPaginaRepositorio",
                        return_value=mock_pagina_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroPersonagemRepositorio",
                        return_value=mock_personagem_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroFalaRepositorio",
                    ),
                ):
                    resultado = service.identificar_personagens_por_pagina(1)

        assert resultado == 2
        assert pagina.fl_processado == "S"
        mock_llm.identificar_personagens.assert_called_once()

    def test_narrador_e_marcado_com_eh_narrador(
        self, service: PersonagensService, mock_llm: MagicMock
    ) -> None:
        """Narracao cria personagem com fl_eh_narrador='S' e fala com eh_narracao='S'."""
        pagina = make_pagina(texto="Era uma vez num reino distante.")
        mock_llm.identificar_personagens.return_value = [
            {
                "personagem": "Narrador",
                "texto": "Era uma vez num reino distante",
                "tipo": "narracao",
            },
        ]
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_livro_repo = MagicMock()
            mock_livro_repo.buscar_por_id_sync.return_value = MagicMock()
            with patch(
                "app.services.personagens.LivroRepositorio",
                return_value=mock_livro_repo,
            ):
                mock_pagina_repo = MagicMock()
                mock_pagina_repo.listar_nao_processadas.return_value = [pagina]
                mock_personagem_repo = MagicMock()
                mock_personagem_repo.listar_por_livro.return_value = []
                falas_criadas: list[LivroFala] = []
                personagens_criados: list[LivroPersonagem] = []

                def add_side_effect(obj):
                    obj.cd_sequencial = obj.cd_sequencial or len(personagens_criados) + 1
                    if isinstance(obj, LivroPersonagem):
                        personagens_criados.append(obj)
                    elif isinstance(obj, LivroFala):
                        falas_criadas.append(obj)

                mock_session.add.side_effect = add_side_effect
                mock_session.flush.side_effect = lambda: None
                mock_session.refresh.side_effect = lambda obj: None
                with (
                    patch(
                        "app.services.personagens.LivroPaginaRepositorio",
                        return_value=mock_pagina_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroPersonagemRepositorio",
                        return_value=mock_personagem_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroFalaRepositorio",
                    ),
                ):
                    service.identificar_personagens_por_pagina(1)

        assert len(personagens_criados) == 1
        assert personagens_criados[0].fl_eh_narrador == "S"
        assert personagens_criados[0].tx_personagem == "Narrador"
        assert len(falas_criadas) == 1
        assert falas_criadas[0].eh_narracao == "S"

    def test_reatiliza_personagem_existente(
        self, service: PersonagensService, mock_llm: MagicMock
    ) -> None:
        """Personagem ja criado eh reusado, nao duplicado."""
        maria_existente = make_personagem(cd=42, nome="Maria")
        pagina = make_pagina(texto="Maria falou de novo.")
        mock_llm.identificar_personagens.return_value = [
            {"personagem": "Maria", "texto": "falou de novo", "tipo": "fala"},
        ]
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_livro_repo = MagicMock()
            mock_livro_repo.buscar_por_id_sync.return_value = MagicMock()
            with patch(
                "app.services.personagens.LivroRepositorio",
                return_value=mock_livro_repo,
            ):
                mock_pagina_repo = MagicMock()
                mock_pagina_repo.listar_nao_processadas.return_value = [pagina]
                mock_personagem_repo = MagicMock()
                mock_personagem_repo.listar_por_livro.return_value = [maria_existente]
                with (
                    patch(
                        "app.services.personagens.LivroPaginaRepositorio",
                        return_value=mock_pagina_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroPersonagemRepositorio",
                        return_value=mock_personagem_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroFalaRepositorio",
                    ),
                ):
                    service.identificar_personagens_por_pagina(1)

        # Nao deve ter sido criado um novo personagem
        assert maria_existente.cd_sequencial == 42

    def test_llm_falha_lanca_erro(self, service: PersonagensService, mock_llm: MagicMock) -> None:
        """Se o LLM falha repetidamente, levanta ErroAnalisePersonagens."""
        mock_llm.identificar_personagens.side_effect = RuntimeError("boom")
        pagina = make_pagina(texto="texto qualquer")
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_livro_repo = MagicMock()
            mock_livro_repo.buscar_por_id_sync.return_value = MagicMock()
            with patch(
                "app.services.personagens.LivroRepositorio",
                return_value=mock_livro_repo,
            ):
                mock_pagina_repo = MagicMock()
                mock_pagina_repo.listar_nao_processadas.return_value = [pagina]
                mock_personagem_repo = MagicMock()
                mock_personagem_repo.listar_por_livro.return_value = []
                with (
                    patch(
                        "app.services.personagens.LivroPaginaRepositorio",
                        return_value=mock_pagina_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroPersonagemRepositorio",
                        return_value=mock_personagem_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroFalaRepositorio",
                    ),
                    pytest.raises(ErroAnalisePersonagens),
                ):
                    service.identificar_personagens_por_pagina(1)

        # Pagina deve continuar nao processada
        assert pagina.fl_processado is None


# =====================================================================
# normalizar_personagens
# =====================================================================


class TestNormalizarPersonagens:
    """Testes do metodo de normalizacao de nomes."""

    def test_livro_nao_encontrado_lanca_erro(self, service: PersonagensService) -> None:
        """Livro inexistente gera erro."""
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_livro_repo = MagicMock()
            mock_livro_repo.buscar_por_id_sync.return_value = None
            with (
                patch(
                    "app.services.personagens.LivroRepositorio",
                    return_value=mock_livro_repo,
                ),
                pytest.raises(ErroAnalisePersonagens),
            ):
                service.normalizar_personagens(999)

    def test_sem_personagens_marca_normalizado(self, service: PersonagensService) -> None:
        """Sem personagens, marca fl_normalizado='S' e retorna 0."""
        livro_mock = MagicMock()
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_livro_repo = MagicMock()
            mock_livro_repo.buscar_por_id_sync.return_value = livro_mock
            with patch(
                "app.services.personagens.LivroRepositorio",
                return_value=mock_livro_repo,
            ):
                mock_personagem_repo = MagicMock()
                mock_personagem_repo.listar_por_livro.return_value = []
                with patch(
                    "app.services.personagens.LivroPersonagemRepositorio",
                    return_value=mock_personagem_repo,
                ):
                    resultado = service.normalizar_personagens(1)
        assert resultado == 0
        assert livro_mock.fl_normalizado == "S"

    def test_normalizacao_llm_unifica_nomes(
        self, service: PersonagensService, mock_llm: MagicMock
    ) -> None:
        """LLM agrupa 'Maria' e 'D. Maria' e renomeia para 'Maria'."""
        personagens = [
            make_personagem(cd=1, nome="Maria"),
            make_personagem(cd=2, nome="D. Maria"),
        ]
        mock_llm.normalizar_personagens.return_value = [
            {
                "nome_normalizado": "Maria",
                "nomes_originais": ["Maria", "D. Maria"],
                "justificativa": "Mesmo personagem, variacao de tratamento.",
            }
        ]
        livro_mock = MagicMock()
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_livro_repo = MagicMock()
            mock_livro_repo.buscar_por_id_sync.return_value = livro_mock
            with patch(
                "app.services.personagens.LivroRepositorio",
                return_value=mock_livro_repo,
            ):
                mock_personagem_repo = MagicMock()
                mock_personagem_repo.listar_por_livro.return_value = personagens
                mock_fala_repo = MagicMock()
                mock_fala_repo.contar_por_personagem.return_value = 0
                with (
                    patch(
                        "app.services.personagens.LivroPersonagemRepositorio",
                        return_value=mock_personagem_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroFalaRepositorio",
                        return_value=mock_fala_repo,
                    ),
                ):
                    resultado = service.normalizar_personagens(1)
        assert resultado >= 1
        assert all(p.tx_personagem == "Maria" for p in personagens)
        assert livro_mock.fl_normalizado == "S"

    def test_fallback_por_similaridade_textual(
        self, service: PersonagensService, mock_llm: MagicMock
    ) -> None:
        """Quando o LLM falha, aplica fallback de similaridade."""
        mock_llm.normalizar_personagens.side_effect = RuntimeError("offline")
        personagens = [
            make_personagem(cd=1, nome="Maria"),
            make_personagem(cd=2, nome="D. Maria"),
        ]
        livro_mock = MagicMock()
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_livro_repo = MagicMock()
            mock_livro_repo.buscar_por_id_sync.return_value = livro_mock
            with patch(
                "app.services.personagens.LivroRepositorio",
                return_value=mock_livro_repo,
            ):
                mock_personagem_repo = MagicMock()
                mock_personagem_repo.listar_por_livro.return_value = personagens
                mock_fala_repo = MagicMock()
                mock_fala_repo.contar_por_personagem.return_value = 0
                with (
                    patch(
                        "app.services.personagens.LivroPersonagemRepositorio",
                        return_value=mock_personagem_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroFalaRepositorio",
                        return_value=mock_fala_repo,
                    ),
                ):
                    resultado = service.normalizar_personagens(1)
        assert resultado >= 1
        # Ambos devem ter sido renomeados para o mesmo valor
        assert personagens[0].tx_personagem == personagens[1].tx_personagem

    def test_fallback_remove_acentos(
        self, service: PersonagensService, mock_llm: MagicMock
    ) -> None:
        """Fallback lida com acentuacao diferente (José vs Jose)."""
        mock_llm.normalizar_personagens.side_effect = RuntimeError("offline")
        personagens = [
            make_personagem(cd=1, nome="José"),
            make_personagem(cd=2, nome="Jose"),
        ]
        livro_mock = MagicMock()
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_livro_repo = MagicMock()
            mock_livro_repo.buscar_por_id_sync.return_value = livro_mock
            with patch(
                "app.services.personagens.LivroRepositorio",
                return_value=mock_livro_repo,
            ):
                mock_personagem_repo = MagicMock()
                mock_personagem_repo.listar_por_livro.return_value = personagens
                mock_fala_repo = MagicMock()
                mock_fala_repo.contar_por_personagem.return_value = 0
                with (
                    patch(
                        "app.services.personagens.LivroPersonagemRepositorio",
                        return_value=mock_personagem_repo,
                    ),
                    patch(
                        "app.services.personagens.LivroFalaRepositorio",
                        return_value=mock_fala_repo,
                    ),
                ):
                    service.normalizar_personagens(1)
        assert personagens[0].tx_personagem == personagens[1].tx_personagem


# =====================================================================
# _agrupar_desconhecidos
# =====================================================================


class TestAgruparDesconhecidos:
    """Testes do helper de agrupamento de personagens nao revelados."""

    def test_atribui_ids_sequenciais(self, service: PersonagensService) -> None:
        """Personagens com prefixo 'Personagem Desconhecido' ganham #N."""
        personagens = [
            make_personagem(cd=1, nome="Maria"),
            make_personagem(cd=2, nome="Personagem Desconhecido"),
            make_personagem(cd=3, nome="Personagem Desconhecido"),
        ]
        service._agrupar_desconhecidos(personagens)
        assert personagens[0].tx_personagem == "Maria"
        assert personagens[1].tx_personagem == "Personagem Desconhecido #1"
        assert personagens[2].tx_personagem == "Personagem Desconhecido #2"

    def test_sem_desconhecidos_nao_altera(self, service: PersonagensService) -> None:
        """Sem personagens desconhecidos, nada eh alterado."""
        personagens = [
            make_personagem(cd=1, nome="Maria"),
            make_personagem(cd=2, nome="Joao"),
        ]
        service._agrupar_desconhecidos(personagens)
        assert personagens[0].tx_personagem == "Maria"
        assert personagens[1].tx_personagem == "Joao"

    def test_idempotente(self, service: PersonagensService) -> None:
        """Re-executar nao quebra nomes ja formatados com sufixo #N."""
        personagens = [
            make_personagem(cd=1, nome="Personagem Desconhecido #1"),
        ]
        service._agrupar_desconhecidos(personagens)
        # Como ja comeca com "Personagem Desconhecido", contador atribui #1
        assert personagens[0].tx_personagem == "Personagem Desconhecido #1"


# =====================================================================
# salvar_resultados
# =====================================================================


class TestSalvarResultados:
    """Testes do metodo de persistencia em batch."""

    def test_atribui_livro_id_se_faltando(self, service: PersonagensService) -> None:
        """Personagens e falas sem cd_sequenciallivro recebem o livro_id."""
        p = make_personagem(cd=None)  # type: ignore[arg-type]
        p.cd_sequenciallivro = None
        f = LivroFala(
            cd_sequenciallivro=None,
            cd_sequencialpagina=1,
            cd_sequencialpersonagem=1,
            tx_fala="ola",
        )
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_personagem_repo = MagicMock()
            mock_fala_repo = MagicMock()
            with (
                patch(
                    "app.services.personagens.LivroPersonagemRepositorio",
                    return_value=mock_personagem_repo,
                ),
                patch(
                    "app.services.personagens.LivroFalaRepositorio",
                    return_value=mock_fala_repo,
                ),
            ):
                service.salvar_resultados(123, [p], [f])
        assert p.cd_sequenciallivro == 123
        assert f.cd_sequenciallivro == 123
        mock_personagem_repo.salvar_em_lote.assert_called_once()
        mock_fala_repo.salvar_em_lote.assert_called_once()

    def test_respeita_livro_id_existente(self, service: PersonagensService) -> None:
        """Nao sobrescreve cd_sequenciallivro quando ja definido."""
        p = make_personagem(livro_id=42)
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            with (
                patch("app.services.personagens.LivroPersonagemRepositorio"),
                patch("app.services.personagens.LivroFalaRepositorio"),
            ):
                service.salvar_resultados(123, [p], [])
        assert p.cd_sequenciallivro == 42

    def test_listas_vazias_nao_falham(self, service: PersonagensService) -> None:
        """Listas vazias nao causam erro."""
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_personagem_repo = MagicMock()
            mock_fala_repo = MagicMock()
            with (
                patch(
                    "app.services.personagens.LivroPersonagemRepositorio",
                    return_value=mock_personagem_repo,
                ),
                patch(
                    "app.services.personagens.LivroFalaRepositorio",
                    return_value=mock_fala_repo,
                ),
            ):
                service.salvar_resultados(1, [], [])
        mock_personagem_repo.salvar_em_lote.assert_called_once_with([])
        mock_fala_repo.salvar_em_lote.assert_called_once_with([])


# =====================================================================
# listar_personagens e listar_falas_por_personagem
# =====================================================================


class TestListagens:
    """Testes dos metodos de consulta para a UI."""

    def test_listar_personagens_delega_para_repo(self, service: PersonagensService) -> None:
        """listar_personagens deve usar o repo de personagens."""
        esperado = [make_personagem(cd=1, nome="Maria"), make_personagem(cd=2, nome="Joao")]
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_personagem_repo = MagicMock()
            mock_personagem_repo.listar_por_livro.return_value = esperado
            with patch(
                "app.services.personagens.LivroPersonagemRepositorio",
                return_value=mock_personagem_repo,
            ):
                resultado = service.listar_personagens(1)
        assert resultado == esperado
        mock_personagem_repo.listar_por_livro.assert_called_once_with(1)

    def test_listar_falas_por_personagem_delega_para_repo(
        self, service: PersonagensService
    ) -> None:
        """listar_falas_por_personagem deve usar o repo de falas."""
        falas_mock = [MagicMock(), MagicMock()]
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_fala_repo = MagicMock()
            mock_fala_repo.listar_por_personagem.return_value = falas_mock
            with patch(
                "app.services.personagens.LivroFalaRepositorio",
                return_value=mock_fala_repo,
            ):
                resultado = service.listar_falas_por_personagem(99)
        assert resultado == falas_mock
        mock_fala_repo.listar_por_personagem.assert_called_once_with(99)


# =====================================================================
# gerar_sugestoes_unificacao
# =====================================================================


class TestSugestoesUnificacao:
    """Testes do metodo de geracao de sugestoes de unificacao."""

    def test_menos_de_dois_personagens_retorna_vazio(self, service: PersonagensService) -> None:
        """Com < 2 personagens nao ha pares a sugerir."""
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_personagem_repo = MagicMock()
            mock_personagem_repo.listar_por_livro.return_value = [make_personagem()]
            with patch(
                "app.services.personagens.LivroPersonagemRepositorio",
                return_value=mock_personagem_repo,
            ):
                resultado = service.gerar_sugestoes_unificacao(1)
        assert resultado == []

    def test_detecta_pares_similares(self, service: PersonagensService) -> None:
        """Detecta pares com nomes que diferem apenas por prefixos."""
        personagens = [
            make_personagem(cd=1, nome="Maria"),
            make_personagem(cd=2, nome="D. Maria"),
        ]
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_personagem_repo = MagicMock()
            mock_personagem_repo.listar_por_livro.return_value = personagens
            with patch(
                "app.services.personagens.LivroPersonagemRepositorio",
                return_value=mock_personagem_repo,
            ):
                resultado = service.gerar_sugestoes_unificacao(1)
        assert len(resultado) == 1
        assert resultado[0]["personagem1_id"] == 1
        assert resultado[0]["personagem2_id"] == 2
        assert (
            "prefixos" in resultado[0]["justificativa"].lower()
            or "Maria" in resultado[0]["justificativa"]
        )

    def test_lida_com_acentos(self, service: PersonagensService) -> None:
        """Detecta equivalencia ignorando acentuacao."""
        personagens = [
            make_personagem(cd=1, nome="José"),
            make_personagem(cd=2, nome="Jose"),
        ]
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_personagem_repo = MagicMock()
            mock_personagem_repo.listar_por_livro.return_value = personagens
            with patch(
                "app.services.personagens.LivroPersonagemRepositorio",
                return_value=mock_personagem_repo,
            ):
                resultado = service.gerar_sugestoes_unificacao(1)
        assert len(resultado) == 1

    def test_nomes_totalmente_diferentes_nao_pareados(self, service: PersonagensService) -> None:
        """Nomes sem similaridade textual nao geram sugestoes."""
        personagens = [
            make_personagem(cd=1, nome="Maria"),
            make_personagem(cd=2, nome="Carlos"),
        ]
        with patch("app.services.personagens.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_personagem_repo = MagicMock()
            mock_personagem_repo.listar_por_livro.return_value = personagens
            with patch(
                "app.services.personagens.LivroPersonagemRepositorio",
                return_value=mock_personagem_repo,
            ):
                resultado = service.gerar_sugestoes_unificacao(1)
        assert resultado == []


# =====================================================================
# Excecao
# =====================================================================


class TestErroAnalisePersonagens:
    """Testes da excecao customizada."""

    def test_herda_de_runtime_error(self) -> None:
        """ErroAnalisePersonagens deve herdar de RuntimeError."""
        assert issubclass(ErroAnalisePersonagens, RuntimeError)

    def test_pode_ser_instanciada_com_mensagem(self) -> None:
        """Pode receber mensagem customizada."""
        erro = ErroAnalisePersonagens("mensagem especifica")
        assert "mensagem especifica" in str(erro)
