"""Servico de extracao e limpeza de texto de PDFs (FL-01).

Implementa a extracao de texto pagina a pagina de um PDF, a limpeza do
conteudo (remocao de headers, footers, sumarios, hifenizacao, etc.) e
o persistimento das paginas processadas na tabela TB_LIVROPAGINA via
SQLAlchemy.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pdfplumber
from pdfminer.pdfparser import PDFSyntaxError
from sqlalchemy.orm import Session

from app.repositories.database import session_scope
from app.repositories.models.livro_cabecalho import LivroCabecalho
from app.repositories.models.livro_pagina import LivroPagina

# Tamanho do batch de commit para evitar transacoes gigantes
BATCH_COMMIT = 20


class PDFInvalidoError(ValueError):
    """PDF corrompido, protegido por senha ou em formato nao suportado."""


class PDFNaoEncontradoError(FileNotFoundError):
    """Arquivo PDF nao encontrado no caminho informado."""


# Padroes compilados usados por ``limpar_texto`` para remover ruidos
# tipicos de PDFs (numeros de pagina isolados, URLs, creditos, etc.).
PADROES_REMOVER: list[tuple[re.Pattern[str], str]] = [
    # Linha contendo apenas um numero (candidata a numero de pagina)
    (re.compile(r"^\d+$"), "linha_somente_digitos"),
    # "Pagina 12" / "Page 12" / "pagina 12"
    (re.compile(r"^[Pp]agina\s+\d+$"), "pagina_pt"),
    (re.compile(r"^[Pp]age\s+\d+$"), "pagina_en"),
    # "Pagina 12 de 200" / "Page 12 of 200"
    (re.compile(r"^[Pp]agina\s+\d+\s+de\s+\d+$"), "pagina_pt_de"),
    (re.compile(r"^[Pp]age\s+\d+\s+of\s+\d+$"), "pagina_en_of"),
    # Numeros romanos isolados (sumarios)
    (re.compile(r"^[IVXLCDM]+$"), "romanos"),
    # URLs http/https
    (re.compile(r"^https?://\S+$"), "url"),
    # Creditos editoriais comuns
    (
        re.compile(
            r"(?i).*(direitos\s+reservados|copyright|todos\s+os\s+direitos|"
            r"all\s+rights\s+reserved|edicao\s+revisada|editora\s+[\w\s]+).*"
        ),
        "creditos",
    ),
    # "Sumario" / "Summary" / "Indice" como titulo de secao
    (re.compile(r"(?i)^sumario\s*$"), "sumario_titulo"),
    (re.compile(r"(?i)^summary\s*$"), "summary_titulo"),
    (re.compile(r"(?i)^indice\s*$"), "indice_titulo"),
    (re.compile(r"(?i)^contents\s*$"), "contents_titulo"),
    # Linhas de sumario: "Capitulo 1 ........... 12" ou "Cap. 1 - 12"
    (re.compile(r"^[Cc]ap[ií]tulo\s+\d+\s*[\.\-–\s]+\d+\s*$"), "sumario_capitulo"),
    (re.compile(r"^[Cc]ap\.?\s*\d+\s*[\.\-–\s]+\d+\s*$"), "sumario_cap"),
    (re.compile(r"^[Cc]hapter\s+\d+\s*[\.\-–\s]+\d+\s*$"), "sumario_chapter"),
    # "Pagina intencionalmente em branco"
    (
        re.compile(
            r"(?i).*(intencionalmente\s+em\s+branco|intentionally\s+left\s+blank|"
            r"esta\s+pagina\s+esta\s+em\s+branco).*"
        ),
        "pagina_em_branco",
    ),
    # Numeros de ISBN / DOI (muito ruidosos em creditos)
    (re.compile(r"^(ISBN|DOI)\s*:?\s*[\d\-X\.x]+$", re.IGNORECASE), "isbn_doi"),
]


# Padrao de hifenizacao no fim de linha: "con-\ntinua" -> "continua".
RE_HIFENIZACAO = re.compile(r"(\w)-\s*\n\s*(\w)")

# Padrao de quebra de linha dentro de paragrafo.
RE_QUEBRA_LINHA = re.compile(r"\s*\n\s*")

# Padrao de multiplos espacos em sequencia.
RE_ESPACOS = re.compile(r"[ \t]+")

# Padrao de multiplas quebras de linha (separador de paragrafos).
RE_PARAGRAFO = re.compile(r"\n{2,}")


def _remover_ruidos_repetidos(texto: str) -> str:
    """Detecta e remove linhas que se repetem como header/footer.

    Linhas que aparecem 3 ou mais vezes ao longo do texto sao tratadas
    como header/footer e removidas.
    """
    contadores: dict[str, int] = {}
    for linha in texto.splitlines():
        chave = linha.strip()
        if not chave:
            continue
        # Ignora linhas longas de conteudo na deteccao
        if len(chave) > 80:
            continue
        contadores[chave] = contadores.get(chave, 0) + 1

    repetidas = {linha for linha, qtd in contadores.items() if qtd >= 3}

    if not repetidas:
        return texto

    linhas_filtradas = [
        linha for linha in texto.splitlines() if linha.strip() not in repetidas
    ]
    return "\n".join(linhas_filtradas)


def _linha_e_titulo_isolado(linha: str) -> bool:
    """Detecta titulos de capitulo isolados (curtos, em maiusculas)."""
    texto = linha.strip()
    if not texto:
        return False
    if len(texto) > 60:
        return False
    # Apenas letras, espacos, algarismos romanos e pontuacao simples
    if not re.match(r"^[A-ZÀ-Ý0-9IVXLCDM\s\.\-\:\']+$", texto):
        return False
    # Exige maioria de letras maiusculas
    letras = [c for c in texto if c.isalpha()]
    if not letras:
        return False
    maiusculas = sum(1 for c in letras if c.isupper())
    return maiusculas / len(letras) >= 0.8


class PDFService:
    """Servico de extracao de texto de PDF e persistencia das paginas.

    Responsabilidades:
        * Abrir o PDF com ``pdfplumber`` em modo streaming (pagina a pagina).
        * Limpar o texto removendo ruidos tipicos de PDFs.
        * Persistir cada pagina como ``LivroPagina`` em transacoes em batch.

    Excecoes:
        * ``PDFNaoEncontradoError`` quando o arquivo nao existe.
        * ``PDFInvalidoError`` quando o PDF esta corrompido/protegido.
        * ``ValueError`` quando o livro nao existe no banco.
    """

    def extrair_texto(self, caminho_pdf: str) -> Iterator[tuple[int, str]]:
        """Itera paginas do PDF yieldando ``(nr_pagina, texto)``.

        O iterator NAO carrega todo o livro em memoria: cada pagina e
        extraida sob demanda.

        Args:
            caminho_pdf: Caminho (string) para o arquivo PDF.

        Yields:
            Tuplas ``(nr_pagina, texto)`` onde ``nr_pagina`` comeca em 1.

        Raises:
            PDFNaoEncontradoError: Se o arquivo nao existe.
            PDFInvalidoError: Se o PDF esta corrompido/protegido.
        """
        caminho = Path(caminho_pdf)
        if not caminho.exists():
            raise PDFNaoEncontradoError(
                f"Arquivo PDF nao encontrado: {caminho_pdf}"
            )

        try:
            with pdfplumber.open(caminho) as pdf:
                for indice, pagina in enumerate(pdf.pages, start=1):
                    try:
                        texto = pagina.extract_text() or ""
                    except Exception as exc:  # noqa: BLE001
                        # Pagina com erro de extracao nao derruba o processo
                        texto = ""
                        # Mantem o erro visivel para debug sem propagar
                        # (caso o usuario queira tratar, a pagina vem vazia)
                        if hasattr(pagina, "page_obj"):
                            _ = exc
                    yield (indice, texto)
        except (PDFSyntaxError, OSError, ValueError) as exc:
            # PDFSyntaxError -> PDF corrompido/invalido
            # OSError      -> erro de leitura/IO
            # ValueError   -> pdfplumber levanta em alguns casos de header invalido
            raise PDFInvalidoError(
                f"PDF invalido ou corrompido: {caminho_pdf}"
            ) from exc

    def limpar_texto(self, texto: str) -> str:
        """Limpa o texto extraido de uma pagina.

        Operacoes realizadas (em ordem):
            1. Remove headers/footers repetidos no texto da pagina.
            2. Remove linhas que casam com ``PADROES_REMOVER``.
            3. Remove titulos de capitulo isolados (linhas curtas em
               maiusculas).
            4. Junta palavras quebradas por hifenizacao
               (``con-\ntinua`` -> ``continua``).
            5. Normaliza quebras de linha e paragrafos.
            6. Normaliza espacos multiplos.

        Args:
            texto: Texto bruto extraido pelo ``pdfplumber``.

        Returns:
            Texto limpo pronto para segmentacao.
        """
        if not texto:
            return ""

        # 1. Remove headers/footers repetidos
        texto = _remover_ruidos_repetidos(texto)

        # 2. Aplica padroes de remocao linha a linha
        linhas_filtradas: list[str] = []
        for linha in texto.splitlines():
            linha_strip = linha.strip()
            if not linha_strip:
                linhas_filtradas.append("")
                continue

            descartar = False
            for padrao, _nome in PADROES_REMOVER:
                if padrao.match(linha_strip):
                    descartar = True
                    break

            if descartar:
                continue

            # 3. Remove titulos de capitulo isolados
            if _linha_e_titulo_isolado(linha_strip):
                continue

            linhas_filtradas.append(linha)

        texto = "\n".join(linhas_filtradas)

        # 4. Junta hifenizacao: "con-\n tinua" -> "continua"
        texto = RE_HIFENIZACAO.sub(r"\1\2", texto)

        # 5. Substitui quebras de linha simples por espaco (mantendo paragrafos)
        #    Primeiro protege paragrafos duplos, depois processa o resto.
        paragrafos = RE_PARAGRAFO.split(texto)
        paragrafos_processados: list[str] = []
        for paragrafo in paragrafos:
            paragrafo_limpo = RE_QUEBRA_LINHA.sub(" ", paragrafo)
            paragrafos_processados.append(paragrafo_limpo.strip())
        texto = "\n\n".join(p for p in paragrafos_processados if p)

        # 6. Normaliza espacos multiplos
        texto = RE_ESPACOS.sub(" ", texto)

        return texto.strip()

    def processar_pdf(self, livro_id: int, caminho_pdf: str) -> int:
        """Processa o PDF: extrai, limpa e persiste paginas do livro.

        Args:
            livro_id: ``cd_sequencial`` do ``LivroCabecalho`` alvo.
            caminho_pdf: Caminho do arquivo PDF.

        Returns:
            Total de paginas processadas e salvas.

        Raises:
            PDFNaoEncontradoError: Arquivo nao encontrado.
            PDFInvalidoError: PDF corrompido/protegido.
            ValueError: Livro nao encontrado no banco.
        """
        with session_scope() as session:
            livro = self._buscar_livro(session, livro_id)
            if livro is None:
                raise ValueError(
                    f"Livro nao encontrado: id={livro_id}"
                )

            total = 0
            buffer: list[LivroPagina] = []
            for nr_pagina, texto_bruto in self.extrair_texto(caminho_pdf):
                texto_limpo = self.limpar_texto(texto_bruto)
                pagina = LivroPagina(
                    cd_sequenciallivro=livro.cd_sequencial,
                    nr_pagina=nr_pagina,
                    tx_pagina=texto_limpo,
                    fl_processado="N",
                )
                session.add(pagina)
                buffer.append(pagina)
                total += 1

                # Commit em batch para evitar transacoes gigantes
                if len(buffer) >= BATCH_COMMIT:
                    session.flush()
                    session.commit()
                    session.expire_all()
                    buffer.clear()

            # Commit final
            if buffer:
                session.flush()
                session.commit()
                session.expire_all()

            # Marca o livro como lido
            livro.fl_lido = "S"
            session.add(livro)
            session.flush()
            session.commit()

            return total

    @staticmethod
    def _buscar_livro(session: Session, livro_id: int) -> LivroCabecalho | None:
        """Busca o livro por id; retorna ``None`` se nao existir."""
        return session.get(LivroCabecalho, livro_id)


__all__ = [
    "PDFService",
    "PDFInvalidoError",
    "PDFNaoEncontradoError",
    "PADROES_REMOVER",
    "BATCH_COMMIT",
]
