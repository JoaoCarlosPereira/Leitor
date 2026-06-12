"""Servico de LLM — wrapper sobre o SDK OpenAI para o LLM local (Qwen3.6-35B).

Este modulo encapsula todas as chamadas ao LLM local executado em
``http://192.168.2.112:8000/v1/``. Fornece:

* Retry automatico com backoff exponencial (2s, 4s, 8s) para erros transientes
  (timeout, conexao, HTTP 5xx);
* Metodos de alto nivel para identificar personagens vs. narracao,
  normalizar nomes de personagens e inferir emocao/prosodia/paralinguistica;
* Prompt helpers que produzem saidas TTS-ready (texto em PT-BR, sem
  numeros por extenso nao escritos, sem ruidos de formatacao).
"""

from __future__ import annotations

import time
from typing import Any

import openai
from openai import OpenAI

from app.config import get_settings


# Erros transientes que justificam retry.
# - APITimeoutError: requisicao estourou o timeout.
# - APIConnectionError: falha de rede/servidor inacessivel.
# - InternalServerError: erros 5xx retornados pelo LLM.
_ERROS_RETRY: tuple[type[BaseException], ...] = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)

# Backoff exponencial: 2s, 4s, 8s (em segundos).
_BACKOFF_SEGUNDOS: tuple[int, ...] = (2, 4, 8)


class LLMErroError(RuntimeError):
    """Erro irrecuperavel ao chamar o LLM apos esgotar as tentativas de retry.

    Levantada quando o servico de LLM continua retornando erros transientes
    (timeout, conexao, 5xx) apos ``max_retries`` tentativas consecutivas.
    """


class LLMServico:
    """Abstracao da API do LLM local (OpenAI-compatible).

    Parametros do construtor sao todos opcionais — quando omitidos, o servico
    le as configuracoes de :func:`app.config.get_settings`.

    Atributos:
        base_url (str): URL do endpoint OpenAI-compatible do LLM.
        api_key (str): Token de autenticacao (no setup local, ``"local"``).
        model (str): Nome do modelo (ex.: ``"qwen3.6-35b"``).
        timeout (int): Timeout em segundos por requisicao.
        max_retries (int): Numero maximo de tentativas (inclui a primeira).
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        settings = get_settings()

        self.base_url: str = base_url or settings.llm_base_url
        self.api_key: str = api_key or settings.llm_api_key
        self.model: str = model or settings.llm_model
        self.timeout: int = timeout if timeout is not None else settings.llm_timeout
        self.max_retries: int = (
            max_retries if max_retries is not None else settings.llm_max_retries
        )

        self._client: OpenAI = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
            max_retries=0,  # controle de retry eh feito manualmente em _chamar_llm
        )

    # ------------------------------------------------------------------ #
    # Wrapper de baixo nivel (retry + backoff)
    # ------------------------------------------------------------------ #

    def _chamar_llm(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> str:
        """Executa uma chamada de chat completion com retry exponencial.

        Args:
            messages: Lista de mensagens no formato OpenAI
                (``[{"role": ..., "content": ...}, ...]``).
            max_tokens: Numero maximo de tokens a serem gerados.
            temperature: Temperatura de amostragem (0.1 padrao = quase
                deterministico, util para tarefas estruturadas).

        Returns:
            Conteudo textual da primeira escolha (``choices[0].message.content``).

        Raises:
            LLMErroError: Apos esgotar ``max_retries`` tentativas em erros
                transientes, ou imediatamente para erros nao-transientes.
        """
        ultima_excecao: BaseException | None = None

        for tentativa in range(1, self.max_retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                conteudo = response.choices[0].message.content
                if conteudo is None:
                    raise LLMErroError("LLM retornou resposta sem conteudo (content=None).")
                return conteudo
            except _ERROS_RETRY as exc:
                ultima_excecao = exc
                if tentativa >= self.max_retries:
                    break
                espera = _BACKOFF_SEGUNDOS[
                    min(tentativa - 1, len(_BACKOFF_SEGUNDOS) - 1)
                ]
                time.sleep(espera)
            except openai.APIError as exc:
                # Demais erros da API sao levantados sem retry — sao
                # considerados problemas estruturais (autenticacao, payload
                # invalido, etc.) e nao se beneficiam de re-tentar.
                raise LLMErroError(
                    f"Erro nao-transiente do LLM: {exc.__class__.__name__}: {exc}"
                ) from exc

        # Esgotaram-se as tentativas — levantar LLMErroError com detalhes.
        assert ultima_excecao is not None  # sempre preenchido no caminho de retry
        raise LLMErroError(
            f"Falha ao chamar LLM apos {self.max_retries} tentativas: "
            f"{ultima_excecao.__class__.__name__}: {ultima_excecao}"
        ) from ultima_excecao

    # ------------------------------------------------------------------ #
    # Wrapper publico generico
    # ------------------------------------------------------------------ #

    def chamar_llm(
        self,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> str:
        """Wrapper generico para chamada ao LLM com um unico prompt de usuario.

        Args:
            prompt: Texto a ser enviado como mensagem do usuario.
            max_tokens: Numero maximo de tokens na resposta.
            temperature: Temperatura de amostragem.

        Returns:
            Texto retornado pelo LLM.
        """
        messages = [{"role": "user", "content": prompt}]
        return self._chamar_llm(messages, max_tokens=max_tokens, temperature=temperature)

    # ------------------------------------------------------------------ #
    # Identificacao de personagens vs. narracao (FL-02)
    # ------------------------------------------------------------------ #

    def identificar_personagens(self, texto_pagina: str) -> list[dict[str, str]]:
        """Envia o texto de uma pagina para o LLM separar falas de narracao.

        O LLM deve responder em formato estruturado ``nome|texto`` (uma
        entrada por linha). Linhas malformadas sao descartadas pelo parser.

        Args:
            texto_pagina: Texto bruto extraido de uma pagina do PDF.

        Returns:
            Lista de dicionarios com as chaves ``personagem`` (str),
            ``texto`` (str) e ``tipo`` (``"fala"`` ou ``"narracao"``).
        """
        prompt = self._montar_prompt_identificacao(texto_pagina)
        resposta = self.chamar_llm(prompt)
        return self._parsear_resposta(resposta)

    def _montar_prompt_identificacao(self, texto_pagina: str) -> str:
        """Constroi o prompt para o LLM separar falas de narracao."""
        return (
            "Voce eh um assistente especializado em processar textos de livros "
            "para producao de audiolivros.\n\n"
            "TAREFA:\n"
            "Receba o texto de uma pagina de um livro em portugues e SEPARE "
            "estritamente as FALAS de personagens da NARRACAO do autor.\n\n"
            "REGRAS OBRIGATORIAS:\n"
            "1. Para cada fala, escreva uma linha no formato EXATO: "
            "`nome|texto` (separados por um unico pipe `|`).\n"
            "2. Para cada trecho de narracao, escreva uma linha: "
            "`Narrador|texto`.\n"
            "3. Converta QUALQUER numero para extenso (ex: '1995' -> 'mil "
            "novecentos e noventa e cinco'; '1o' -> 'primeiro') para que o "
            "TTS leia corretamente.\n"
            "4. Remova qualquer conteudo nao-narrativo residual (numeros de "
            "pagina, marcadores de capitulo, notas de rodape, URLs).\n"
            "5. Adapte/traduzir o texto para Portugues Brasileiro natural e "
            "TTS-ready (sem siglas, sem abreviacoes).\n"
            "6. Use o nome proprio do personagem sempre que possivel. Se nao "
            "for possivel identificar, use `Personagem Desconhecido`.\n"
            "7. NAO inclua comentarios, cabecalhos, marcadores ou texto "
            "adicional — APENAS as linhas no formato `nome|texto`, uma por "
            "linha, na ordem em que aparecem no texto original.\n\n"
            f"TEXTO DA PAGINA:\n```\n{texto_pagina}\n```\n"
        )

    # ------------------------------------------------------------------ #
    # Normalizacao de nomes de personagens (FL-02 Etapa 2b)
    # ------------------------------------------------------------------ #

    def normalizar_personagens(
        self, lista_personagens: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Solicita ao LLM a unificacao de nomes equivalentes de personagens.

        Args:
            lista_personagens: Lista de dicionarios ``{"nome_original": str,
                "falas_count": int}`` representando os personagens crus
                identificados na etapa anterior.

        Returns:
            Lista de dicionarios com:
              * ``nome_normalizado`` (str) — nome canonico escolhido;
              * ``nomes_originais`` (list[str]) — variantes que foram unificadas;
              * ``justificativa`` (str) — explicacao em PT-BR da consolidacao.
        """
        prompt = self._montar_prompt_normalizacao(lista_personagens)
        resposta = self.chamar_llm(prompt)
        return self._parsear_resposta_normalizacao(resposta)

    def _montar_prompt_normalizacao(
        self, lista_personagens: list[dict[str, Any]]
    ) -> str:
        """Constroi o prompt para o LLM unificar nomes equivalentes."""
        # Serializa a lista de personagens em formato tabular simples e
        # estavel — o LLM recebe contexto claro sem depender de JSON exato.
        linhas = ["nome_original|falas_count"]
        for p in lista_personagens:
            nome = str(p.get("nome_original", "")).strip()
            count = int(p.get("falas_count", 0))
            linhas.append(f"{nome}|{count}")
        tabela = "\n".join(linhas)

        return (
            "Voce eh um assistente editorial especializado em identificar "
            "personagens de livros e unificar variacoes de nome.\n\n"
            "TAREFA:\n"
            "A partir da lista de nomes abaixo (cada um com a contagem de "
            "falas), agrupe os nomes que se referem ao MESMO personagem "
            "(ex: 'Maria' = 'D. Maria' = 'Maria, a Protagonista').\n\n"
            "FORMATO DE RESPOSTA (OBRIGATORIO):\n"
            "Responda EXCLUSIVAMENTE com linhas no formato "
            "`nome_normalizado|nome1;nome2;nome3|justificativa`, uma "
            "agrupamento por linha.\n"
            "- O primeiro campo eh o nome canonico sugerido (use o mais "
            "completo e natural em PT-BR).\n"
            "- O segundo campo lista TODOS os nomes originais unificados, "
            "separados por `;`.\n"
            "- O terceiro campo eh uma justificativa curta em PT-BR (max "
            "120 caracteres) explicando a unificacao.\n"
            "Se um nome nao tem equivalente, liste-o sozinho.\n"
            "NAO inclua texto adicional fora desse formato.\n\n"
            f"PERSONAGENS BRUTOS:\n```\n{tabela}\n```\n"
        )

    # ------------------------------------------------------------------ #
    # Inferencia de emocao, prosodia e paralinguistica (FL-04)
    # ------------------------------------------------------------------ #

    def inferir_emocao(
        self, texto_fala: str, contexto: str = ""
    ) -> dict[str, str]:
        """Infere instrucoes de emocao/prosodia/paralinguistica para uma fala.

        Args:
            texto_fala: Texto da fala a ser analisada.
            contexto: Texto opcional com contexto narrativo anterior
                (aumenta a precisao da inferencia).

        Returns:
            Dicionario com tres chaves:
              * ``emocao`` (str) — ex.: "fale de forma alegre e saltitante";
              * ``prosodia`` (str) — ex.: "fale bem devagar, fazendo pausas
                dramaticas";
              * ``paralinguistica`` (str) — ex.: "[sigh]", "Ah...".
        """
        prompt = self._montar_prompt_emocao(texto_fala, contexto)
        resposta = self.chamar_llm(prompt)
        return self._parsear_resposta_emocao(resposta)

    def _montar_prompt_emocao(self, texto_fala: str, contexto: str = "") -> str:
        """Constroi o prompt para inferir emocao/prosodia/paralinguistica."""
        bloco_contexto = (
            f"CONTEXTO NARRATIVO ANTERIOR:\n```\n{contexto}\n```\n\n"
            if contexto
            else ""
        )
        return (
            "Voce eh um diretor de audio especializado em controlar o TTS "
            "Qwen3-TTS via instrucoes em linguagem natural (campo `instruct`).\n\n"
            "TAREFA:\n"
            "Analise a fala abaixo e produza tres instrucoes:\n"
            "1. emocao: tom emocional geral (ex: 'fale de forma alegre e "
            "saltitante', 'fale com raiva contida', 'fale com tristeza "
            "profunda').\n"
            "2. prosodia: ritmo, pausas e enfase (ex: 'fale bem devagar, "
            "fazendo pausas dramaticas', 'fale rapido, sem respirar').\n"
            "3. paralinguistica: marcadores para o TTS produzir efeitos "
            "paraverbais (ex: '[sigh]', 'Ah...', '[whisper]', 'Hm...').\n\n"
            "FORMATO DE RESPOSTA (OBRIGATORIO):\n"
            "Responda EXCLUSIVAMENTE com tres linhas:\n"
            "EMOCAO: <texto em PT-BR>\n"
            "PROSODIA: <texto em PT-BR>\n"
            "PARALINGUISTICA: <texto em PT-BR ou vazio>\n"
            "Se nao houver marcadores paraverbais relevantes, escreva "
            "PARALINGUISTICA: (vazio).\n\n"
            f"{bloco_contexto}"
            f"FALA:\n```\n{texto_fala}\n```\n"
        )

    # ------------------------------------------------------------------ #
    # Parsers de resposta do LLM
    # ------------------------------------------------------------------ #

    def _parsear_resposta(self, resposta_llm: str) -> list[dict[str, str]]:
        """Parser generico no formato ``nome|texto``.

        Usado por ``identificar_personagens``. Descarta silenciosamente
        linhas malformadas (sem pipe ou com campos vazios invalidos).

        Args:
            resposta_llm: Conteudo retornado pelo LLM.

        Returns:
            Lista de dicionarios ``{"personagem", "texto", "tipo"}``.
        """
        resultado: list[dict[str, str]] = []
        if not resposta_llm:
            return resultado

        for linha in resposta_llm.splitlines():
            linha = linha.strip()
            if not linha or "|" not in linha:
                continue
            # Pega apenas o primeiro pipe para evitar problemas com
            # textos que contenham o caractere `|` no conteudo.
            nome, _, texto = linha.partition("|")
            nome = nome.strip()
            texto = texto.strip()
            if not texto:
                # Linha sem conteudo narrativo eh inuteil.
                continue
            if not nome:
                nome = "Personagem Desconhecido"
            tipo = "narracao" if nome.lower() == "narrador" else "fala"
            resultado.append(
                {"personagem": nome, "texto": texto, "tipo": tipo}
            )
        return resultado

    def _parsear_resposta_normalizacao(
        self, resposta_llm: str
    ) -> list[dict[str, Any]]:
        """Parser do retorno de ``normalizar_personagens``.

        Espera linhas ``nome_normalizado|nome1;nome2|justificativa``.
        """
        resultado: list[dict[str, Any]] = []
        if not resposta_llm:
            return resultado

        for linha in resposta_llm.splitlines():
            linha = linha.strip()
            if not linha or linha.lower().startswith("nome_normalizado"):
                # Pula cabecalhos e linhas vazias.
                continue
            partes = linha.split("|")
            if len(partes) < 2:
                continue
            nome_norm = partes[0].strip()
            nomes_originais_raw = partes[1].strip()
            justificativa = partes[2].strip() if len(partes) >= 3 else ""
            if not nome_norm or not nomes_originais_raw:
                continue
            nomes_originais = [
                n.strip() for n in nomes_originais_raw.split(";") if n.strip()
            ]
            if not nomes_originais:
                continue
            # Garante que o nome normalizado esteja na lista de originais.
            if nome_norm not in nomes_originais:
                nomes_originais.insert(0, nome_norm)
            resultado.append(
                {
                    "nome_normalizado": nome_norm,
                    "nomes_originais": nomes_originais,
                    "justificativa": justificativa,
                }
            )
        return resultado

    def _parsear_resposta_emocao(self, resposta_llm: str) -> dict[str, str]:
        """Parser do retorno de ``inferir_emocao``.

        Espera tres linhas ``EMOCAO:``, ``PROSODIA:`` e ``PARALINGUISTICA:``.
        """
        resultado: dict[str, str] = {
            "emocao": "",
            "prosodia": "",
            "paralinguistica": "",
        }
        if not resposta_llm:
            return resultado

        for linha in resposta_llm.splitlines():
            linha = linha.strip()
            if not linha or ":" not in linha:
                continue
            chave, _, valor = linha.partition(":")
            chave = chave.strip().upper()
            valor = valor.strip()
            if chave == "EMOCAO":
                resultado["emocao"] = valor
            elif chave == "PROSODIA":
                resultado["prosodia"] = valor
            elif chave == "PARALINGUISTICA":
                # Tratamento especial para o marcador "(vazio)" e parenteses.
                if valor and valor.lower() not in {"(vazio)", "vazio", "-"}:
                    resultado["paralinguistica"] = valor
        return resultado
