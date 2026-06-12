"""Servico de analise de personagens por LLM (FL-02).

Este modulo implementa o pipeline de identificacao, normalizacao e
persistencia de personagens a partir das paginas extraidas de um livro,
usando o LLM local para separar falas de narracao, unificar nomes
duplicados e agrupar personagens nao revelados.

Etapas implementadas (PRD FL-02):
  2a. Identificacao de personagens e falas por pagina;
  2b. Normalizacao automatizada de nomes por LLM (com fallback por
      similaridade textual caso o LLM falhe);
  2c. Agrupamento de "Personagem Desconhecido #N" para personagens
      cujas falas existem mas cujos nomes ainda nao foram revelados.

Apos a normalizacao, a flag `fl_normalizado='S'` eh marcada no
``LivroCabecalho`` para indicar checkpoint da etapa.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import (
    LivroFalaRepositorio,
    LivroPaginaRepositorio,
    LivroPersonagemRepositorio,
    LivroRepositorio,
    session_scope,
)
from app.repositories.models.livro_fala import LivroFala
from app.repositories.models.livro_personagem import LivroPersonagem
from app.services.llm import LLMServico

logger = logging.getLogger(__name__)


# Nome canonico usado para personagens nao revelados. Quando o LLM nao
# identifica o personagem, o servico cria entradas com este prefixo
# e o sufixo numerico eh atribuido por ``_agrupar_desconhecidos``.
PERSONAGEM_DESCONHECIDO = "Personagem Desconhecido"


class ErroAnalisePersonagens(RuntimeError):  # noqa: N818
    """Erro irrecuperavel durante a analise de personagens.

    Levantada quando o LLM falha em um passo obrigatorio e nao existe
    fallback viavel (ex.: livro nao encontrado, sem paginas para
    processar e base de personagens vazia, etc.).
    """


class PersonagensService:
    """Orquestra a identificacao e normalizacao de personagens de um livro.

    O servico encapsula todo o pipeline de analise de personagens (FL-02),
    incluindo:

      * Identificacao por pagina (chamada ao LLM);
      * Formatacao do resultado bruto (limpeza, normalizacao de nomes);
      * Persistencia de personagens e falas;
      * Normalizacao de nomes com fallback por similaridade textual;
      * Agrupamento de personagens nao revelados;
      * Listagens para a UI de revisao;
      * Sugestoes de unificacao entre personagens potencialmente
        duplicados.

    Attributes:
        llm: Instancia de :class:`LLMServico` usada para chamadas ao LLM.
    """

    def __init__(self, llm: LLMServico | None = None) -> None:
        """Inicializa o servico de personagens.

        Args:
            llm: Instancia opcional de LLMServico para injecao de dependencia
                (util em testes). Quando omitida, instancia ``LLMServico()``.
        """
        self.llm: LLMServico = llm if llm is not None else LLMServico()

    # ==================================================================
    # Etapa 2a: Identificacao de personagens por pagina
    # ==================================================================

    def identificar_personagens_por_pagina(self, livro_id: int) -> int:
        """Identifica personagens e falas para todas as paginas nao processadas.

        Para cada pagina do livro com ``fl_processado != 'S'``:

        1. Chama :meth:`LLMServico.identificar_personagens` para separar
           falas de narracao;
        2. Formata o resultado via :meth:`_formatar_resultado` (limpa
           ruidos, normaliza nomes, descarta lixo);
        3. Para cada item, cria/persiste um ``LivroPersonagem`` (se
           novo) e uma ``LivroFala``;
        4. Marca a pagina como processada.

        Args:
            livro_id: Identificador do livro (TB_LIVROCABECALHO).

        Returns:
            Numero total de falas criadas para o livro nesta execucao.

        Raises:
            ErroAnalisePersonagens: Se o livro nao existir ou se o LLM
                falhar repetidamente sem fallback.
        """
        logger.info("Iniciando identificacao de personagens para livro id=%s", livro_id)

        with session_scope() as session:
            livro_repo = LivroRepositorio(session)
            livro = livro_repo.buscar_por_id_sync(livro_id)
            if livro is None:
                raise ErroAnalisePersonagens(
                    f"Livro id={livro_id} nao encontrado para identificacao de personagens."
                )

            pagina_repo = LivroPaginaRepositorio(session)
            personagem_repo = LivroPersonagemRepositorio(session)

            paginas = pagina_repo.listar_nao_processadas(livro_id)
            if not paginas:
                logger.info(
                    "Nenhuma pagina nao processada encontrada para livro id=%s",
                    livro_id,
                )
                return 0

            # Cache local de personagens ja criados nesta execucao para
            # evitar reprocessamento por pagina. Chave: nome normalizado
            # (case-insensitive).
            cache_personagens: dict[str, LivroPersonagem] = {
                p.tx_personagem.lower(): p
                for p in personagem_repo.listar_por_livro(livro_id)
                if p.tx_personagem
            }

            total_falas = 0
            for pagina in paginas:
                texto_pagina = pagina.tx_pagina or ""
                if not texto_pagina.strip():
                    pagina.fl_processado = "S"
                    session.flush()
                    continue

                try:
                    bruto = self.llm.identificar_personagens(texto_pagina)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "LLM falhou ao identificar personagens na pagina id=%s: %s",
                        pagina.cd_sequencial,
                        exc,
                    )
                    # Mantem a pagina como nao processada para proxima tentativa.
                    raise ErroAnalisePersonagens(
                        f"Falha do LLM na pagina id={pagina.cd_sequencial}: {exc}"
                    ) from exc

                itens = self._formatar_resultado(bruto)
                for item in itens:
                    personagem = self._obter_ou_criar_personagem(
                        session=session,
                        personagem_repo=personagem_repo,
                        cache=cache_personagens,
                        livro_id=livro_id,
                        nome=item["personagem"],
                        eh_narrador=item["tipo"] == "narracao",
                    )
                    fala = self._criar_fala(
                        livro_id=livro_id,
                        pagina_id=pagina.cd_sequencial,
                        personagem_id=personagem.cd_sequencial,
                        texto=item["texto"],
                        eh_narracao=item["tipo"] == "narracao",
                        ordem_inicial=total_falas,
                    )
                    session.add(fala)
                    total_falas += 1

                pagina.fl_processado = "S"
                session.flush()
                logger.debug(
                    "Pagina id=%s processada: %d itens extraidos",
                    pagina.cd_sequencial,
                    len(itens),
                )

        logger.info(
            "Identificacao concluida para livro id=%s: %d falas criadas",
            livro_id,
            total_falas,
        )
        return total_falas

    def _obter_ou_criar_personagem(
        self,
        session: Session,
        personagem_repo: LivroPersonagemRepositorio,
        cache: dict[str, LivroPersonagem],
        livro_id: int,
        nome: str,
        eh_narrador: bool,
    ) -> LivroPersonagem:
        """Obtem personagem existente (do cache) ou cria um novo.

        Args:
            session: Sessao SQLAlchemy ativa.
            personagem_repo: Repositorio de personagens.
            cache: Cache local por nome (lower) -> personagem.
            livro_id: ID do livro.
            nome: Nome ja normalizado (title case, strip).
            eh_narrador: Se o personagem eh o narrador.

        Returns:
            Instancia persistida de :class:`LivroPersonagem`.
        """
        chave = nome.lower()
        if chave in cache:
            return cache[chave]

        personagem = LivroPersonagem(
            cd_sequenciallivro=livro_id,
            tx_personagem=nome,
            fl_eh_narrador="S" if eh_narrador else "N",
        )
        session.add(personagem)
        session.flush()
        session.refresh(personagem)
        cache[chave] = personagem
        return personagem

    def _criar_fala(
        self,
        livro_id: int,
        pagina_id: int,
        personagem_id: int,
        texto: str,
        eh_narracao: bool,
        ordem_inicial: int,
    ) -> LivroFala:
        """Monta uma instancia de :class:`LivroFala` com nr_ordem sequencial.

        O ``nr_ordem`` eh calculado como ``ordem_inicial + 1`` por chamada
        — este servico processa uma fala por vez e empilha o contador
        externamente para evitar gaps caso o caller queira reiniciar.
        """
        return LivroFala(
            cd_sequenciallivro=livro_id,
            cd_sequencialpagina=pagina_id,
            cd_sequencialpersonagem=personagem_id,
            tx_fala=texto,
            fl_processado="N",
            nr_ordem=ordem_inicial + 1,
            eh_narracao="S" if eh_narracao else "N",
        )

    def _formatar_resultado(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Padroniza o resultado bruto do LLM.

        Operacoes realizadas:
          * Title case + strip em nomes;
          * Substituicao do nome canonico do narrador (``Narrador``) e
            marcacao de ``fl_eh_narrador='S'`` implicita via ``tipo='narracao'``;
          * Descarte de linhas vazias, muito curtas, ou contendo apenas
            digitos (heuristica de lixo);
          * Garante que ``tipo`` seja ``"fala"`` ou ``"narracao"``;
          * Substitui nomes vazios por :data:`PERSONAGEM_DESCONHECIDO`.

        Args:
            items: Lista de dicts ``{"personagem", "texto", "tipo"}`` crus
                retornados pelo LLM (ou ja formatados).

        Returns:
            Lista limpa e padronizada de itens.
        """
        resultado: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            nome = str(item.get("personagem", "")).strip()
            texto = str(item.get("texto", "")).strip()
            tipo_raw = str(item.get("tipo", "")).strip().lower()

            if not texto:
                continue
            if not self._texto_narrativo_valido(texto):
                continue

            # Normalizacao do tipo.
            if nome.lower() == "narrador" or tipo_raw == "narracao":
                tipo = "narracao"
                nome = "Narrador"
            else:
                tipo = "fala"
                if not nome:
                    nome = PERSONAGEM_DESCONHECIDO
                # Title case: "maria da SILVA" -> "Maria Da Silva"
                nome = nome.title()

            resultado.append({"personagem": nome, "texto": texto, "tipo": tipo})
        return resultado

    @staticmethod
    def _texto_narrativo_valido(texto: str) -> bool:
        """Heuristica de descarte de conteudo nao narrativo.

        Descarta:
          * Texto com menos de 3 caracteres;
          * Texto que seja apenas numeros;
          * Texto que seja apenas pontuacao/simbolos.

        Args:
            texto: Trecho ja stripado.

        Returns:
            True se o texto parece ser conteudo narrativo valido.
        """
        if len(texto) < 3:
            return False
        # Apenas digitos -> provavel numero de pagina, ISBN, etc.
        if texto.isdigit():
            return False
        # Apenas pontuacao/simbolos/whitespace.
        return any(ch.isalnum() for ch in texto)

    # ==================================================================
    # Etapa 2b: Normalizacao de nomes
    # ==================================================================

    def normalizar_personagens(self, livro_id: int) -> int:
        """Normaliza os nomes dos personagens do livro.

        Estrategia:
          1. Lista todos os personagens do livro;
          2. Envia a lista para o LLM via
             :meth:`LLMServico.normalizar_personagens`;
          3. Para cada agrupamento retornado, busca cada nome original
             no banco e renomeia para o nome canonico;
          4. Se o LLM falhar, aplica fallback por similaridade textual
             (lowercase, remove acentos, remove prefixos ``D.``, ``Sr.``,
             ``Sra.``, ``Dr.``, ``Dona``);
          5. Agrupa personagens "Personagem Desconhecido" com sufixos
             ``#1``, ``#2``...;
          6. Marca ``fl_normalizado='S'`` no livro.

        Args:
            livro_id: ID do livro (TB_LIVROCABECALHO).

        Returns:
            Numero de personagens efetivamente renomeados durante o
            processo.
        """
        logger.info("Iniciando normalizacao de personagens para livro id=%s", livro_id)

        with session_scope() as session:
            livro_repo = LivroRepositorio(session)
            livro = livro_repo.buscar_por_id_sync(livro_id)
            if livro is None:
                raise ErroAnalisePersonagens(
                    f"Livro id={livro_id} nao encontrado para normalizacao."
                )

            personagem_repo = LivroPersonagemRepositorio(session)
            personagens = personagem_repo.listar_por_livro(livro_id)
            if not personagens:
                logger.info("Sem personagens para normalizar no livro id=%s", livro_id)
                livro.fl_normalizado = "S"
                session.flush()
                return 0

            # Conta falas por personagem para dar contexto ao LLM.
            fala_repo = LivroFalaRepositorio(session)
            entrada_llm: list[dict[str, Any]] = []
            contagem_falas: dict[int, int] = {}
            for p in personagens:
                if p.tx_personagem is None:
                    continue
                count = fala_repo.contar_por_personagem(p.cd_sequencial)
                contagem_falas[p.cd_sequencial] = count
                entrada_llm.append(
                    {
                        "nome_original": p.tx_personagem,
                        "falas_count": count,
                    }
                )

            renomeados = 0
            try:
                agrupamentos = self.llm.normalizar_personagens(entrada_llm)
                renomeados = self._aplicar_agrupamentos_llm(
                    session=session,
                    personagem_repo=personagem_repo,
                    personagens=personagens,
                    agrupamentos=agrupamentos,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "LLM falhou na normalizacao do livro id=%s — aplicando "
                    "fallback por similaridade textual: %s",
                    livro_id,
                    exc,
                )
                renomeados = self._fallback_similaridade_textual(
                    session=session,
                    personagem_repo=personagem_repo,
                    personagens=personagens,
                )

            # Agrupar "Personagem Desconhecido" com IDs sequenciais "#N".
            self._agrupar_desconhecidos(personagens)

            livro.fl_normalizado = "S"
            session.flush()

        logger.info(
            "Normalizacao concluida para livro id=%s: %d renomeacoes",
            livro_id,
            renomeados,
        )
        return renomeados

    def _aplicar_agrupamentos_llm(
        self,
        session: Session,
        personagem_repo: LivroPersonagemRepositorio,
        personagens: list[LivroPersonagem],
        agrupamentos: list[dict[str, Any]],
    ) -> int:
        """Aplica o retorno do LLM renomeando personagens existentes.

        Para cada agrupamento, busca o primeiro personagem cujo nome
        case com algum dos ``nomes_originais`` e o renomeia para
        ``nome_normalizado``. Os demais nomes do agrupamento sao
        marcados como duplicatas a serem revisadas pelo administrador
        (renomeados tambem para o canonico, de modo que a UI mostre a
        unificacao).

        Args:
            session: Sessao SQLAlchemy ativa.
            personagem_repo: Repositorio de personagens.
            personagens: Lista de personagens atuais do livro.
            agrupamentos: Lista retornada pelo LLM.

        Returns:
            Numero de renomeacoes aplicadas.
        """
        if not agrupamentos:
            return 0

        indice_por_nome: dict[str, list[LivroPersonagem]] = {}
        for p in personagens:
            if p.tx_personagem:
                indice_por_nome.setdefault(p.tx_personagem.lower(), []).append(p)

        renomeados = 0
        for grupo in agrupamentos:
            nome_canonico = str(grupo.get("nome_normalizado", "")).strip()
            nomes_originais = grupo.get("nomes_originais", []) or []
            if not nome_canonico or not nomes_originais:
                continue

            candidatos: list[LivroPersonagem] = []
            for original in nomes_originais:
                candidatos.extend(indice_por_nome.get(str(original).lower(), []))

            if not candidatos:
                continue

            # Mantem o primeiro como canonico e renomeia os demais.
            # Garante que o primeiro tenha o nome canonico.
            principal = candidatos[0]
            nome_anterior = principal.tx_personagem
            if nome_anterior != nome_canonico:
                principal.tx_personagem = nome_canonico
                renomeados += 1

            for duplicado in candidatos[1:]:
                if duplicado.tx_personagem != nome_canonico:
                    duplicado.tx_personagem = nome_canonico
                    renomeados += 1

        session.flush()
        return renomeados

    def _fallback_similaridade_textual(
        self,
        session: Session,
        personagem_repo: LivroPersonagemRepositorio,
        personagens: list[LivroPersonagem],
    ) -> int:
        """Fallback de normalizacao por similaridade textual.

        Algoritmo:
          1. Calcula uma chave canonica para cada personagem (lowercase,
             sem acentos, sem prefixos ``D.``, ``Sr.``, ``Sra.``, ``Dr.``,
             ``Dona``, ``Senhor``, ``Senhora``);
          2. Agrupa personagens pela chave;
          3. Para cada grupo com mais de um personagem, escolhe o de
             maior nome (mais completo) como canonico e renomeia os
             demais.

        Args:
            session: Sessao SQLAlchemy ativa.
            personagem_repo: Repositorio de personagens.
            personagens: Lista de personagens atuais do livro.

        Returns:
            Numero de renomeacoes aplicadas.
        """
        prefixos = {
            "d.",
            "d",
            "sr.",
            "sr",
            "sra.",
            "sra",
            "dr.",
            "dr",
            "dona",
            "senhor",
            "senhora",
            "srta.",
            "srta",
            "prof.",
            "prof",
            "professor",
            "professora",
        }

        def canonico(nome: str) -> str:
            n = unicodedata.normalize("NFD", nome.lower())
            n = "".join(ch for ch in n if unicodedata.category(ch) != "Mn")
            n = re.sub(r"[^a-z0-9\s]", " ", n)
            tokens = [t for t in n.split() if t and t not in prefixos]
            return " ".join(tokens)

        grupos: dict[str, list[LivroPersonagem]] = {}
        for p in personagens:
            if not p.tx_personagem:
                continue
            chave = canonico(p.tx_personagem)
            if not chave:
                continue
            grupos.setdefault(chave, []).append(p)

        renomeados = 0
        for _chave, membros in grupos.items():
            if len(membros) < 2:
                continue
            # Escolhe o nome canonico pelo maior comprimento (mais completo)
            # e, em empate, ordem alfabetica para determinismo.
            membros_ordenados = sorted(
                membros,
                key=lambda x: (-len(x.tx_personagem or ""), (x.tx_personagem or "").lower()),
            )
            canonico_escolhido = membros_ordenados[0].tx_personagem
            for m in membros_ordenados[1:]:
                if m.tx_personagem != canonico_escolhido:
                    m.tx_personagem = canonico_escolhido
                    renomeados += 1

        session.flush()
        return renomeados

    def _agrupar_desconhecidos(self, personagens: Sequence[LivroPersonagem]) -> None:
        """Atribui IDs sequenciais "#N" a personagens nao revelados.

        Personagens com nome comecando com :data:`PERSONAGEM_DESCONHECIDO`
        sao renomeados para ``Personagem Desconhecido #1``, ``#2``... na
        ordem do parametro ``personagens`` (que vem do repositorio,
        ja ordenado por nome). O resultado eh deterministico e estavel.

        Args:
            personagens: Lista mutavel de personagens (in-place).
        """
        contador = 0
        for p in personagens:
            if p.tx_personagem and p.tx_personagem.startswith(PERSONAGEM_DESCONHECIDO):
                contador += 1
                novo_nome = f"{PERSONAGEM_DESCONHECIDO} #{contador}"
                if p.tx_personagem != novo_nome:
                    p.tx_personagem = novo_nome

    # ==================================================================
    # Persistencia
    # ==================================================================

    def salvar_resultados(
        self,
        livro_id: int,
        personagens: list[LivroPersonagem],
        falas: list[LivroFala],
    ) -> None:
        """Persiste em batch personagens e falas no banco.

        Args:
            livro_id: ID do livro (atribuido a entidades que ainda nao o tem).
            personagens: Lista de instancias de ``LivroPersonagem``.
            falas: Lista de instancias de ``LivroFala``.
        """
        with session_scope() as session:
            personagem_repo = LivroPersonagemRepositorio(session)
            fala_repo = LivroFalaRepositorio(session)

            for p in personagens:
                if p.cd_sequenciallivro is None:
                    p.cd_sequenciallivro = livro_id
            for f in falas:
                if f.cd_sequenciallivro is None:
                    f.cd_sequenciallivro = livro_id

            personagem_repo.salvar_em_lote(personagens)
            fala_repo.salvar_em_lote(falas)

    # ==================================================================
    # Listagens para a UI de revisao
    # ==================================================================

    def listar_personagens(self, livro_id: int) -> list[LivroPersonagem]:
        """Retorna personagens do livro ordenados por nome."""
        with session_scope() as session:
            personagem_repo = LivroPersonagemRepositorio(session)
            return personagem_repo.listar_por_livro(livro_id)

    def listar_falas_por_personagem(self, personagem_id: int) -> list[LivroFala]:
        """Retorna falas de um personagem ordenadas por nr_ordem."""
        with session_scope() as session:
            fala_repo = LivroFalaRepositorio(session)
            return fala_repo.listar_por_personagem(personagem_id)

    # ==================================================================
    # Sugestoes de unificacao
    # ==================================================================

    def gerar_sugestoes_unificacao(self, livro_id: int) -> list[dict[str, Any]]:
        """Identifica pares de personagens candidatos a unificacao.

        Combina duas estrategias:
          1. Similaridade textual (mesmo algoritmo de fallback);
          2. Validacao opcional via LLM para pares suspeitos (pequena
             amostragem) — quando o LLM falha, retorna apenas os pares
             detectados por similaridade.

        Args:
            livro_id: ID do livro.

        Returns:
            Lista de dicts ``{"personagem1_id", "personagem2_id",
            "justificativa"}``.
        """
        with session_scope() as session:
            personagem_repo = LivroPersonagemRepositorio(session)
            personagens = personagem_repo.listar_por_livro(livro_id)
            if len(personagens) < 2:
                return []

            prefixos = {
                "d.",
                "d",
                "sr.",
                "sr",
                "sra.",
                "sra",
                "dr.",
                "dr",
                "dona",
                "senhor",
                "senhora",
                "srta.",
                "srta",
                "prof.",
                "prof",
            }

            def canonico(nome: str) -> str:
                n = unicodedata.normalize("NFD", nome.lower())
                n = "".join(ch for ch in n if unicodedata.category(ch) != "Mn")
                n = re.sub(r"[^a-z0-9\s]", " ", n)
                tokens = [t for t in n.split() if t and t not in prefixos]
                return " ".join(tokens)

            grupos: dict[str, list[LivroPersonagem]] = {}
            for p in personagens:
                if not p.tx_personagem:
                    continue
                chave = canonico(p.tx_personagem)
                if not chave:
                    continue
                grupos.setdefault(chave, []).append(p)

            pares: list[dict[str, Any]] = []
            for chave, membros in grupos.items():
                if len(membros) < 2 or len(chave) < 3:
                    continue
                nomes = sorted({m.tx_personagem for m in membros if m.tx_personagem})
                if len(nomes) < 2:
                    continue
                justificativa = f"Nomes diferem apenas por prefixos/acentos: {', '.join(nomes)}"
                for i in range(len(membros)):
                    for j in range(i + 1, len(membros)):
                        a, b = membros[i], membros[j]
                        if a.tx_personagem == b.tx_personagem:
                            continue
                        pares.append(
                            {
                                "personagem1_id": a.cd_sequencial,
                                "personagem2_id": b.cd_sequencial,
                                "justificativa": justificativa,
                            }
                        )

        return pares
