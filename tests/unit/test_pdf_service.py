"""Testes unitarios para o servico de extracao de PDF."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pdfminer.pdfparser import PDFSyntaxError

from app.repositories.models.livro_cabecalho import LivroCabecalho
from app.repositories.models.livro_pagina import LivroPagina
from app.services.pdf import (
    BATCH_COMMIT,
    PADROES_REMOVER,
    PDFInvalidoError,
    PDFNaoEncontradoError,
    PDFService,
)

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


class FakePage:
    """Pequeno stub de pagina de pdfplumber."""

    def __init__(self, texto: str | None) -> None:
        self._texto = texto

    def extract_text(self) -> str | None:
        return self._texto


class FakePDF:
    """Stub de contexto ``pdfplumber.open``."""

    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages

    def __enter__(self) -> FakePDF:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        return None


@contextmanager
def fake_session_scope(session: MagicMock) -> Iterator[MagicMock]:
    """Substituto de ``session_scope`` que entrega a sessao mockada."""
    yield session


@pytest.fixture
def pdf_service() -> PDFService:
    return PDFService()


# -----------------------------------------------------------------------------
# Testes de limpar_texto
# -----------------------------------------------------------------------------


class TestLimparTexto:
    """Cobre as regras de limpeza de texto."""

    def test_texto_vazio_retorna_vazio(self, pdf_service: PDFService) -> None:
        assert pdf_service.limpar_texto("") == ""

    def test_texto_none_retorna_vazio(self, pdf_service: PDFService) -> None:
        # type: ignore[arg-type]
        assert pdf_service.limpar_texto(None) == ""

    def test_remove_linha_somente_digitos(self, pdf_service: PDFService) -> None:
        texto = "Era uma vez\n42\num rei muito velho."
        resultado = pdf_service.limpar_texto(texto)
        assert "42" not in resultado.split("\n\n")[-1].splitlines()
        assert "Era uma vez" in resultado
        assert "um rei muito velho." in resultado

    def test_remove_pagina_pt(self, pdf_service: PDFService) -> None:
        texto = "Texto qualquer.\nPagina 12\nMais texto."
        resultado = pdf_service.limpar_texto(texto)
        assert "Pagina 12" not in resultado
        assert "Texto qualquer." in resultado
        assert "Mais texto." in resultado

    def test_remove_pagina_en(self, pdf_service: PDFService) -> None:
        texto = "Texto qualquer.\nPage 12\nMais texto."
        resultado = pdf_service.limpar_texto(texto)
        assert "Page 12" not in resultado

    def test_remove_url(self, pdf_service: PDFService) -> None:
        texto = "Link util:\nhttps://exemplo.com/livro\nFim."
        resultado = pdf_service.limpar_texto(texto)
        assert "https://exemplo.com/livro" not in resultado
        assert "Link util:" in resultado

    def test_remove_creditos(self, pdf_service: PDFService) -> None:
        texto = "Inicio\nDireitos Reservados\nContinua"
        resultado = pdf_service.limpar_texto(texto)
        assert "Direitos Reservados" not in resultado
        assert "Inicio" in resultado
        assert "Continua" in resultado

    def test_remove_copyright(self, pdf_service: PDFService) -> None:
        texto = "Texto A\nCopyright 2020 by Editor XYZ\nTexto B"
        resultado = pdf_service.limpar_texto(texto)
        assert "Copyright" not in resultado

    def test_remove_sumario_capitulo(self, pdf_service: PDFService) -> None:
        texto = "Sumario\nCapitulo 1 ......... 12\nConteudo do capitulo aqui"
        resultado = pdf_service.limpar_texto(texto)
        assert "Capitulo 1" not in resultado
        assert "Conteudo do capitulo aqui" in resultado

    def test_remove_sumario_chapter(self, pdf_service: PDFService) -> None:
        texto = "Contents\nChapter 3 ........ 45\nTexto do livro"
        resultado = pdf_service.limpar_texto(texto)
        assert "Chapter 3" not in resultado

    def test_remove_pagina_em_branco(self, pdf_service: PDFService) -> None:
        texto = "Anterior\nPagina intencionalmente em branco\nProximo"
        resultado = pdf_service.limpar_texto(texto)
        assert "intencionalmente em branco" not in resultado

    def test_remove_isbn(self, pdf_service: PDFService) -> None:
        texto = "Dados do livro\nISBN: 978-85-1234-567-8\nTexto principal"
        resultado = pdf_service.limpar_texto(texto)
        assert "ISBN" not in resultado
        assert "Texto principal" in resultado

    def test_remove_titulo_capitulo_isolado(self, pdf_service: PDFService) -> None:
        texto = "CAPITULO I\nEra uma vez em um reino distante."
        resultado = pdf_service.limpar_texto(texto)
        assert "CAPITULO I" not in resultado
        assert "Era uma vez em um reino distante." in resultado

    def test_preserva_titulos_longos(self, pdf_service: PDFService) -> None:
        # Titulo "longo demais" (>60 chars) NAO deve ser removido
        titulo_longo = (
            "ESTE EH UM TITULO EXTREMAMENTE LONGO QUE NAO DEVERIA SER "
            "CONSIDERADO UM TITULO DE CAPITULO ISOLADO"
        )
        texto = f"{titulo_longo}\nCorpo do texto depois."
        resultado = pdf_service.limpar_texto(texto)
        assert titulo_longo in resultado

    def test_junta_hifenizacao(self, pdf_service: PDFService) -> None:
        texto = "O rei estava con-\ntinua na sala do trono."
        resultado = pdf_service.limpar_texto(texto)
        assert "continua" in resultado
        assert "con-" not in resultado
        assert "\n" not in resultado.split("sala")[0]

    def test_normaliza_espacos_multiplos(self, pdf_service: PDFService) -> None:
        texto = "Texto  com    varios     espacos."
        resultado = pdf_service.limpar_texto(texto)
        assert "  " not in resultado
        assert "Texto com varios espacos." in resultado

    def test_remove_header_repetido(self, pdf_service: PDFService) -> None:
        # Header aparece 3+ vezes -> removido
        texto = (
            "NOME DO LIVRO - AUTOR\n"
            "Primeiro paragrafo.\n"
            "\n"
            "NOME DO LIVRO - AUTOR\n"
            "Segundo paragrafo.\n"
            "\n"
            "NOME DO LIVRO - AUTOR\n"
            "Terceiro paragrafo.\n"
        )
        resultado = pdf_service.limpar_texto(texto)
        # Header repetido some
        assert "NOME DO LIVRO - AUTOR" not in resultado
        # Conteudo permanece
        assert "Primeiro paragrafo." in resultado
        assert "Segundo paragrafo." in resultado
        assert "Terceiro paragrafo." in resultado

    def test_preserva_header_curto_unico(self, pdf_service: PDFService) -> None:
        # Header aparece apenas 1 vez -> permanece
        # Usa frase mista (nao all-caps) para nao cair no filtro de titulo
        texto = "Cabecalho do livro\nTexto principal do capitulo."
        resultado = pdf_service.limpar_texto(texto)
        assert "Cabecalho do livro" in resultado
        assert "Texto principal do capitulo." in resultado

    def test_limpa_texto_completo(self, pdf_service: PDFService) -> None:
        texto = (
            "CAPITULO I\n"
            "\n"
            "Era uma vez um rei con-\n"
            "verso.\n"
            "\n"
            "12\n"
            "https://exemplo.com\n"
            "Continuacao da historia."
        )
        resultado = pdf_service.limpar_texto(texto)
        assert "converso" in resultado
        assert "CAPITULO I" not in resultado
        assert "https://exemplo.com" not in resultado
        # Numero 12 isolado eh removido
        assert "12\n" not in resultado
        assert resultado.endswith("Continuacao da historia.")


# -----------------------------------------------------------------------------
# Testes de extrair_texto
# -----------------------------------------------------------------------------


class TestExtrairTexto:
    """Cobre a extracao pagina a pagina do PDF."""

    def test_arquivo_nao_existente(self, pdf_service: PDFService, tmp_path: Path) -> None:
        caminho_inexistente = tmp_path / "nao_existe.pdf"
        with pytest.raises(PDFNaoEncontradoError):
            list(pdf_service.extrair_texto(str(caminho_inexistente)))

    def test_extracao_normal(self, pdf_service: PDFService, tmp_path: Path) -> None:
        arquivo = tmp_path / "ok.pdf"
        arquivo.write_bytes(b"%PDF-1.4 fake content")

        paginas = [
            FakePage("Texto da pagina 1"),
            FakePage("Texto da pagina 2"),
            FakePage(None),  # pagina sem texto
        ]

        with patch("app.services.pdf.pdfplumber.open", return_value=FakePDF(paginas)):
            resultado = list(pdf_service.extrair_texto(str(arquivo)))

        assert resultado == [
            (1, "Texto da pagina 1"),
            (2, "Texto da pagina 2"),
            (3, ""),
        ]

    def test_pdf_sintaxe_invalida(
        self, pdf_service: PDFService, tmp_path: Path
    ) -> None:
        arquivo = tmp_path / "quebrado.pdf"
        arquivo.write_bytes(b"not a pdf")

        with patch(
            "app.services.pdf.pdfplumber.open",
            side_effect=PDFSyntaxError("cabecalho invalido"),
        ):
            with pytest.raises(PDFInvalidoError) as exc:
                list(pdf_service.extrair_texto(str(arquivo)))
            assert str(arquivo) in str(exc.value)

    def test_pdf_oserror(
        self, pdf_service: PDFService, tmp_path: Path
    ) -> None:
        arquivo = tmp_path / "ilegivel.pdf"
        arquivo.write_bytes(b"%PDF-1.4")

        with patch(
            "app.services.pdf.pdfplumber.open",
            side_effect=OSError("arquivo corrompido"),
        ), pytest.raises(PDFInvalidoError):
            list(pdf_service.extrair_texto(str(arquivo)))

    def test_pdf_valueerror(
        self, pdf_service: PDFService, tmp_path: Path
    ) -> None:
        arquivo = tmp_path / "invalido.pdf"
        arquivo.write_bytes(b"%PDF-1.4")

        with patch(
            "app.services.pdf.pdfplumber.open",
            side_effect=ValueError("header invalido"),
        ), pytest.raises(PDFInvalidoError):
            list(pdf_service.extrair_texto(str(arquivo)))

    def test_iterator_nao_carrega_tudo(self, pdf_service: PDFService, tmp_path: Path) -> None:
        """Garante que ``extrair_texto`` retorna um iterator lazy."""
        arquivo = tmp_path / "lazy.pdf"
        arquivo.write_bytes(b"%PDF-1.4")

        with patch(
            "app.services.pdf.pdfplumber.open",
            return_value=FakePDF([FakePage(f"pag {i}") for i in range(1, 4)]),
        ):
            it = pdf_service.extrair_texto(str(arquivo))
            assert iter(it) is it
            primeira = next(it)
            assert primeira == (1, "pag 1")


# -----------------------------------------------------------------------------
# Testes de processar_pdf
# -----------------------------------------------------------------------------


class TestProcessarPDF:
    """Cobre o fluxo completo: extracao, limpeza, persistencia."""

    def test_livro_nao_encontrado(
        self, pdf_service: PDFService, tmp_path: Path
    ) -> None:
        arquivo = tmp_path / "ok.pdf"
        arquivo.write_bytes(b"%PDF-1.4")

        sessao = MagicMock()
        sessao.get.return_value = None
        sessao.__enter__ = MagicMock(return_value=sessao)
        sessao.__exit__ = MagicMock(return_value=False)

        with patch(
            "app.services.pdf.session_scope",
            lambda: fake_session_scope(sessao),
        ):
            with pytest.raises(ValueError) as exc:
                pdf_service.processar_pdf(999, str(arquivo))
            assert "999" in str(exc.value)

    def test_processamento_completo(
        self, pdf_service: PDFService, tmp_path: Path
    ) -> None:
        arquivo = tmp_path / "completo.pdf"
        arquivo.write_bytes(b"%PDF-1.4")

        # Mock do livro
        livro_mock = MagicMock(spec=LivroCabecalho)
        livro_mock.cd_sequencial = 7
        livro_mock.fl_lido = "N"

        # Sessao mockada
        sessao = MagicMock()
        sessao.get.return_value = livro_mock
        sessao.__enter__ = MagicMock(return_value=sessao)
        sessao.__exit__ = MagicMock(return_value=False)

        # Páginas brutas com conteúdo que precisa limpeza
        paginas_pdf = [
            FakePage("CAPITULO I\nEra uma vez um rei."),
            FakePage("Texto da pagina dois\n12\nMais texto."),
            FakePage(""),
        ]

        with patch(
            "app.services.pdf.pdfplumber.open",
            return_value=FakePDF(paginas_pdf),
        ), patch(
            "app.services.pdf.session_scope",
            lambda: fake_session_scope(sessao),
        ):
            total = pdf_service.processar_pdf(7, str(arquivo))

        # Retornou a contagem certa
        assert total == 3

        # Livro marcado como lido
        assert livro_mock.fl_lido == "S"

        # 3 paginas foram adicionadas
        assert sessao.add.call_count >= 3
        chamadas_pagina = [
            call
            for call in sessao.add.call_args_list
            if len(call.args) > 0 and isinstance(call.args[0], LivroPagina)
        ]
        assert len(chamadas_pagina) == 3

        # Os textos foram limpos
        tx_paginas = [c.args[0].tx_pagina for c in chamadas_pagina]
        assert "CAPITULO I" not in tx_paginas[0]
        assert "Era uma vez um rei." in tx_paginas[0]
        assert "12" not in tx_paginas[1].split("\n") if tx_paginas[1] else True

        # nr_pagina sequencial
        for i, call in enumerate(chamadas_pagina, start=1):
            assert call.args[0].nr_pagina == i
            assert call.args[0].cd_sequenciallivro == 7
            assert call.args[0].fl_processado == "N"

        # Commit foi chamado pelo menos 1 vez (batch final + flush do livro)
        assert sessao.commit.called

    def test_batch_commit_com_muitas_paginas(
        self, pdf_service: PDFService, tmp_path: Path
    ) -> None:
        """Garante que para N paginas ha commits intermediarios."""
        arquivo = tmp_path / "grande.pdf"
        arquivo.write_bytes(b"%PDF-1.4")

        livro_mock = MagicMock(spec=LivroCabecalho)
        livro_mock.cd_sequencial = 1
        livro_mock.fl_lido = "N"

        sessao = MagicMock()
        sessao.get.return_value = livro_mock
        sessao.__enter__ = MagicMock(return_value=sessao)
        sessao.__exit__ = MagicMock(return_value=False)

        # 45 paginas -> 2 commits intermediarios (20, 40) + final
        total_paginas = BATCH_COMMIT * 2 + 5
        paginas_pdf = [FakePage(f"conteudo pagina {i}") for i in range(1, total_paginas + 1)]

        with patch(
            "app.services.pdf.pdfplumber.open",
            return_value=FakePDF(paginas_pdf),
        ), patch(
            "app.services.pdf.session_scope",
            lambda: fake_session_scope(sessao),
        ):
            total = pdf_service.processar_pdf(1, str(arquivo))

        assert total == total_paginas
        # Pelo menos 2 commits intermediarios + final do livro
        assert sessao.commit.call_count >= 2

    def test_pdf_invalido_propagado(
        self, pdf_service: PDFService, tmp_path: Path
    ) -> None:
        arquivo = tmp_path / "ruim.pdf"
        arquivo.write_bytes(b"garbage")

        sessao = MagicMock()
        sessao.__enter__ = MagicMock(return_value=sessao)
        sessao.__exit__ = MagicMock(return_value=False)

        with patch(
            "app.services.pdf.pdfplumber.open",
            side_effect=PDFSyntaxError("erro"),
        ), patch(
            "app.services.pdf.session_scope",
            lambda: fake_session_scope(sessao),
        ), pytest.raises(PDFInvalidoError):
            pdf_service.processar_pdf(1, str(arquivo))

    def test_pdf_inexistente_propagado(
        self, pdf_service: PDFService, tmp_path: Path
    ) -> None:
        sessao = MagicMock()
        with patch(
            "app.services.pdf.session_scope",
            lambda: fake_session_scope(sessao),
        ), pytest.raises(PDFNaoEncontradoError):
            pdf_service.processar_pdf(1, str(tmp_path / "fantasma.pdf"))


# -----------------------------------------------------------------------------
# Testes de padroes e constantes
# -----------------------------------------------------------------------------


class TestConstantes:
    def test_padroes_remover_contem_padroes_basicos(self) -> None:
        nomes = {n for _, n in PADROES_REMOVER}
        assert "linha_somente_digitos" in nomes
        assert "pagina_pt" in nomes
        assert "url" in nomes
        assert "sumario_capitulo" in nomes

    def test_batch_commit_positivo(self) -> None:
        assert BATCH_COMMIT > 0
        assert isinstance(BATCH_COMMIT, int)


class TestExcecoes:
    def test_pdf_invalido_eh_value_error(self) -> None:
        assert issubclass(PDFInvalidoError, ValueError)

    def test_pdf_nao_encontrado_eh_file_not_found(self) -> None:
        assert issubclass(PDFNaoEncontradoError, FileNotFoundError)
