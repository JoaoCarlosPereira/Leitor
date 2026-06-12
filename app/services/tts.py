"""Servico de comunicacao com o TTS local (Qwen3-TTS) via HTTP REST.

Este servico implementa a interface descrita na TechSpec do Leitor para
integracao com o Qwen3-TTS. Suporta:

- Voice Cloning (3s de audio de referencia + ref_text) via ``/v1/audio/clone``.
- Controle de emocao, prosodia e paralinguistica via parametro ``instruct``.
- Reuso de ``voice_clone_prompt`` (cache) via ``/v1/audio/clone/prompt``.
- Sintese com voz predefinida (CustomVoice) via ``/v1/audio/speech``.
- Voice Design via ``/v1/audio/design``.
- Retry com backoff exponencial (3 tentativas: 2s, 4s, 8s).
- Timeout de 120 segundos por chamada HTTP.
- Divisao inteligente de textos longos em chunks respeitando pontuacao.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


# Comprimento maximo (em caracteres) de um chunk individual de texto.
MAX_CHARS_PADRAO = 500

# Pontuacoes usadas como pontos de quebra preferenciais na divisao de chunks.
# A ordem importa: a funcao tenta quebrar primeiro nas primeiras pontuacoes
# (frases finalizadas com ., !, ?) e depois em virgula/ponto-e-virgula/dois-pontos.
PONTUACOES_FORTES = (".", "!", "?")
PONTUACOES_FRACAS = (",", ";", ":")

# Backoff exponencial: 2s, 4s, 8s. O indice da tentativa comeca em 0.
_BACKOFF_SEGUNDOS: tuple[int, ...] = (2, 4, 8)

# Tamanho em bytes de uma resposta WAV "vazia" usada em testes.
_WAV_HEADER_LEN = 44


class TTSErroError(RuntimeError):
    """Erro generico do servico de TTS apos esgotar as tentativas de retry."""


class TTSChunkMuitoLongoError(ValueError):
    """Texto excede o limite maximo de caracteres para um unico chunk."""


@dataclass
class InstrucaoAudio:
    """Controle de interpretacao para o TTS via ``instruct`` do Qwen3-TTS.

    O Qwen3-TTS usa o parametro ``instruct`` em linguagem natural (NAO tags
    como ``<EMOTION>``) para ajustar emocao, prosodia e paralinguistica.

    Attributes:
        emocao: Descricao da emocao (ex.: "fale de forma alegre e saltitante").
        prosodia: Descricao do ritmo e entonacao (ex.: "fale bem devagar").
        paralinguistica: Indicacoes no input (ex.: "[sigh]", "Ah...").
    """

    emocao: str = ""
    prosodia: str = ""
    paralinguistica: str = ""

    def to_instruct(self) -> str:
        """Combina os campos em uma unica string para o parametro ``instruct``.

        Os campos preenchidos sao concatenados com um espaco simples. Campos
        vazios sao ignorados, retornando string vazia quando nenhum campo
        foi definido (a ausencia da string sinaliza ao TTS que nao ha
        instrucoes customizadas para esta fala).
        """
        partes = (self.emocao, self.prosodia, self.paralinguistica)
        return " ".join(p.strip() for p in partes if p and p.strip())


@dataclass
class _EntradaPromptCache:
    """Entrada interna do cache de ``voice_clone_prompt``.

    Attributes:
        prompt: Conteudo bruto do prompt retornado pelo servidor.
        chave: Hash usado como chave no dicionario de cache.
    """

    prompt: str = ""
    chave: str = ""


class TTSServico:
    """Abstracao da API HTTP do Qwen3-TTS local.

    Endpoints utilizados:
      - ``POST /v1/audio/clone``         — clonagem de voz via referencia base64.
      - ``POST /v1/audio/clone/prompt``  — cria ``voice_clone_prompt`` reutilizavel.
      - ``POST /v1/audio/speech``        — sintese com voz predefinida.
      - ``POST /v1/audio/design``        — geracao por descricao da voz alvo.

    O cliente HTTP e criado com ``httpx.AsyncClient`` e reutilizado durante
    todo o ciclo de vida do servico. Chame ``close()`` (ou use o servico
    como async context manager) para liberar recursos.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Inicializa o servico lendo defaults de ``get_settings()``.

        Args:
            base_url: URL base do servidor TTS (ex.: ``http://host:8881``).
                Quando ``None``, usa ``Settings.tts_base_url``.
            timeout: Timeout HTTP em segundos para cada requisicao.
                Quando ``None``, usa ``Settings.tts_timeout``.
            max_retries: Numero maximo de tentativas antes de levantar erro.
                Quando ``None``, usa ``Settings.tts_max_retries``.
            settings: Instancia opcional de ``Settings`` (util para testes).
        """
        if settings is None:
            settings = get_settings()

        self._settings = settings
        self._base_url = (base_url or settings.tts_base_url).rstrip("/")
        self._timeout = float(timeout if timeout is not None else settings.tts_timeout)
        self._max_retries = max(
            1,
            int(max_retries if max_retries is not None else settings.tts_max_retries),
        )

        self._client: httpx.AsyncClient = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout)
        )
        # Cache em memoria de voice_clone_prompt por chave de referencia.
        self._prompt_cache: dict[str, str] = {}

        # Pre-compila expressoes regulares usadas por ``_dividir_em_chunks``.
        self._re_quebra_forte = re.compile(rf"[{re.escape(''.join(PONTUACOES_FORTES))}]")
        self._re_quebra_fraca = re.compile(rf"[{re.escape(''.join(PONTUACOES_FRACAS))}]")

    # ------------------------------------------------------------------
    # Operacoes de alto nivel
    # ------------------------------------------------------------------

    async def gerar_audio(
        self,
        texto: str,
        ref_audio_base64: str,
        ref_text: str = "",
        instrucao: InstrucaoAudio | None = None,
        linguagem: str = "Portuguese",
        use_prompt_cache: bool = True,
    ) -> bytes:
        """Gera audio a partir de texto e referencia de voz (voice cloning).

        Args:
            texto: Texto que a voz clonada deve falar.
            ref_audio_base64: Audio de referencia em Base64.
            ref_text: Texto exato do audio de referencia — melhora a precisao
                do ``instruct`` no modelo Base.
            instrucao: Controle de emocao, prosodia e paralinguistica.
            linguagem: Idioma (padrao: ``"Portuguese"``; ``"Auto"`` tambem
                e suportado pelo Qwen3-TTS).
            use_prompt_cache: Se ``True``, reusa ``voice_clone_prompt`` em
                cache para o par ``(ref_audio_base64, ref_text)``.

        Returns:
            bytes: Conteudo do arquivo WAV gerado.

        Raises:
            TTSErroError: Apos esgotar todas as tentativas de retry.
            ValueError: Quando ``texto`` esta vazio ou ``ref_audio_base64`` nao
                foi informado.
        """
        if not texto:
            raise ValueError("O texto para geracao de audio nao pode ser vazio.")
        if not ref_audio_base64:
            raise ValueError("O audio de referencia (ref_audio_base64) e obrigatorio.")

        payload: dict[str, object] = {
            "input": texto,
            "ref_audio": ref_audio_base64,
            "language": linguagem,
            "response_format": "wav",
        }
        if ref_text:
            payload["ref_text"] = ref_text
        if instrucao is not None:
            instruct = instrucao.to_instruct()
            if instruct:
                payload["instruct"] = instruct

        # Reuso de prompt: se ja temos o prompt para esta referencia, envia
        # como ``voice_clone_prompt`` para o servidor evitar recomputo.
        if use_prompt_cache:
            chave = self._chave_prompt(ref_audio_base64, ref_text)
            prompt = self._prompt_cache.get(chave)
            if prompt:
                payload["voice_clone_prompt"] = prompt

        endpoint = f"{self._base_url}/v1/audio/clone"
        conteudo = await self._gerar_com_retry(endpoint, payload)

        # Apos sucesso, tenta capturar e armazenar o prompt retornado pelos
        # servidores que oferecem este comportamento. Em resposta WAV binaria
        # nao ha como extrair JSON; o metodo ``criar_prompt_reutilizavel``
        # deve ser usado para popular o cache de forma explicita.
        return conteudo

    async def criar_prompt_reutilizavel(
        self,
        ref_audio_base64: str,
        ref_text: str,
    ) -> str:
        """Cria um ``voice_clone_prompt`` reutilizavel para a referencia dada.

        O prompt retornado pode ser cacheado internamente e enviado nas
        proximas chamadas de ``gerar_audio`` (com ``use_prompt_cache=True``)
        para evitar que o servidor recompute as features da referencia.

        Args:
            ref_audio_base64: Audio de referencia em Base64.
            ref_text: Texto exato do audio de referencia.

        Returns:
            str: Conteudo do ``voice_clone_prompt`` retornado pelo servidor.

        Raises:
            TTSErroError: Quando o servidor falha em todas as tentativas.
            ValueError: Quando algum parametro obrigatorio nao e informado.
        """
        if not ref_audio_base64:
            raise ValueError("O audio de referencia (ref_audio_base64) e obrigatorio.")

        endpoint = f"{self._base_url}/v1/audio/clone/prompt"
        payload = {
            "ref_audio": ref_audio_base64,
            "ref_text": ref_text,
        }

        # Esta chamada sempre retorna JSON (nao WAV) — usamos o cliente
        # diretamente para conseguir parsear o corpo da resposta.
        resposta = await self._client.post(endpoint, json=payload)
        if resposta.status_code >= 400:
            # Tenta extrair mensagem util do corpo antes de levantar.
            detalhe = self._extrair_detalhe_erro(resposta)
            raise TTSErroError(
                f"Falha ao criar voice_clone_prompt: HTTP {resposta.status_code} - {detalhe}"
            )
        dados = resposta.json()
        prompt = dados.get("voice_clone_prompt", "")
        if prompt:
            self._prompt_cache[self._chave_prompt(ref_audio_base64, ref_text)] = prompt
        return prompt

    # ------------------------------------------------------------------
    # Operacoes em lote
    # ------------------------------------------------------------------

    async def gerar_audio_lote(
        self,
        falas: list[dict],
        referencia_voz: dict,
    ) -> list[bytes]:
        """Gera audio para uma lista de falas de um mesmo personagem.

        Cada fala e dividida em chunks respeitando pontuacao e, em seguida,
        ``gerar_audio`` e chamado para cada chunk. Os resultados sao
        retornados em uma lista plana na mesma ordem dos chunks gerados.

        Args:
            falas: Lista de dicionarios com a forma::

                [
                    {"texto": str, "instrucao": InstrucaoAudio | None, "lang": str},
                    ...
                ]

            referencia_voz: Dicionario ``{"ref_audio_base64": str, "ref_text": str}``.

        Returns:
            list[bytes]: Lista de bytes WAV (um por chunk) na ordem em que
            foram processados.

        Raises:
            ValueError: Quando a lista de falas esta vazia ou a referencia e invalida.
        """
        if not falas:
            raise ValueError("A lista de falas nao pode ser vazia.")
        ref_audio = referencia_voz.get("ref_audio_base64")
        ref_text = referencia_voz.get("ref_text", "")
        if not ref_audio:
            raise ValueError("referencia_voz precisa conter 'ref_audio_base64'.")

        audios: list[bytes] = []
        total_chunks = sum(len(self._dividir_em_chunks(str(f.get("texto", "")))) for f in falas)
        processados = 0
        for idx, fala in enumerate(falas, start=1):
            texto = str(fala.get("texto", ""))
            instrucao = fala.get("instrucao")
            lang = fala.get("lang", "Portuguese")

            if not isinstance(instrucao, InstrucaoAudio) and instrucao is not None:
                raise ValueError(
                    f"Fala #{idx}: 'instrucao' deve ser InstrucaoAudio ou None."
                )

            chunks = self._dividir_em_chunks(texto)
            for n_chunk, chunk in enumerate(chunks, start=1):
                logger.info(
                    "gerar_audio_lote: fala %d/%d chunk %d/%d (%d chars)",
                    idx,
                    len(falas),
                    n_chunk,
                    len(chunks),
                    len(chunk),
                )
                audio = await self.gerar_audio(
                    texto=chunk,
                    ref_audio_base64=ref_audio,
                    ref_text=ref_text,
                    instrucao=instrucao,
                    linguagem=lang,
                )
                audios.append(audio)
                processados += 1
                if total_chunks:
                    logger.info(
                        "Progresso TTS: %d/%d chunks (%.1f%%)",
                        processados,
                        total_chunks,
                        100 * processados / total_chunks,
                    )
        return audios

    # ------------------------------------------------------------------
    # Voice Design
    # ------------------------------------------------------------------

    async def gerar_voz_design(
        self,
        descricao: str,
        texto_referencia: str = "Esta e uma amostra da voz.",
        linguagem: str = "Portuguese",
    ) -> bytes:
        """Gera um audio de referencia a partir de uma descricao da voz alvo.

        Utiliza o modelo Voice Design do Qwen3-TTS para produzir um WAV
        curto a partir de uma descricao em linguagem natural (ex.:
        ``"voz masculina adulta, grave e solene"``).

        Args:
            descricao: Descricao da voz desejada em linguagem natural.
            texto_referencia: Texto que sera falado no audio gerado.
            linguagem: Idioma da sintese.

        Returns:
            bytes: WAV com a amostra de voz.

        Raises:
            TTSErroError: Apos esgotar todas as tentativas.
        """
        if not descricao:
            raise ValueError("A descricao da voz nao pode ser vazia.")
        endpoint = f"{self._base_url}/v1/audio/design"
        payload = {
            "input": texto_referencia,
            "instruct": descricao,
            "language": linguagem,
            "response_format": "wav",
        }
        return await self._gerar_com_retry(endpoint, payload)

    # ------------------------------------------------------------------
    # Helpers sincronos
    # ------------------------------------------------------------------

    def normalizar_wav_para_base64(self, caminho_wav: str) -> str:
        """Le um arquivo WAV do disco e retorna seu conteudo em Base64.

        Args:
            caminho_wav: Caminho do arquivo ``.wav`` no disco.

        Returns:
            str: Conteudo do arquivo codificado em Base64.

        Raises:
            FileNotFoundError: Quando o arquivo nao existe.
        """
        caminho = Path(caminho_wav)
        if not caminho.is_file():
            raise FileNotFoundError(f"Arquivo WAV nao encontrado: {caminho_wav}")
        dados = caminho.read_bytes()
        return base64.b64encode(dados).decode("ascii")

    def _chave_prompt(self, ref_audio_base64: str, ref_text: str) -> str:
        """Monta a chave usada no cache de ``voice_clone_prompt``.

        Usa apenas um prefixo do base64 para evitar manter dados
        sensiveis em memoria e para manter a chave curta.
        """
        prefixo = ref_audio_base64[:32] if ref_audio_base64 else ""
        return f"{prefixo}:{ref_text}"

    def _dividir_em_chunks(self, texto: str, max_chars: int = MAX_CHARS_PADRAO) -> list[str]:
        """Divide ``texto`` em chunks respeitando pontuacao e ``max_chars``.

        A estrategia:
          1. Tenta quebrar primeiro em pontuacoes fortes (``.``, ``!``, ``?``).
          2. Caso nao seja possivel, tenta virgula, ponto-e-virgula ou
             dois-pontos.
          3. Como ultimo recurso, quebra no ultimo espaco em branco dentro
             de ``max_chars``.
          4. Cada pedaco gerado e maior que zero (chunks vazios sao
             descartados) e tem no maximo ``max_chars`` caracteres.

        Args:
            texto: Texto completo a ser dividido.
            max_chars: Tamanho maximo de cada chunk.

        Returns:
            list[str]: Lista de chunks na ordem de aparicao no texto.

        Raises:
            TTSChunkMuitoLongoError: Se o texto e menor que ``max_chars`` mas
                nao possui nenhum ponto de quebra viavel (caso degenerado).
        """
        texto = (texto or "").strip()
        if not texto:
            return []
        if max_chars <= 0:
            raise ValueError("max_chars deve ser maior que zero.")
        if len(texto) <= max_chars:
            return [texto]

        chunks: list[str] = []
        restante = texto
        while len(restante) > max_chars:
            pedaco = restante[:max_chars]
            corte = self._encontrar_ponto_corte(pedaco)
            if corte <= 0:
                # Sem pontuacao no pedaco: tenta o ultimo espaco em branco.
                espaco = pedaco.rfind(" ")
                if espaco > 0:
                    corte = espaco + 1  # inclui o espaco no chunk atual
                else:
                    # Texto sem espacos em branco: quebra forcado no limite.
                    corte = max_chars

            chunk = restante[:corte].strip()
            if chunk:
                chunks.append(chunk)
            restante = restante[corte:].lstrip()

        if restante:
            chunks.append(restante.strip())

        if not chunks:
            # Caso degenerado: algo nao foi possivel dividir.
            raise TTSChunkMuitoLongoError(
                f"Nao foi possivel dividir o texto em chunks de ate {max_chars} caracteres."
            )
        return chunks

    def _encontrar_ponto_corte(self, pedaco: str) -> int:
        """Encontra o melhor ponto de corte em ``pedaco`` usando pontuacao.

        Procura a ultima ocorrencia de pontuacao forte e, se nao encontrar,
        a ultima de pontuacao fraca. Retorna a posicao (1-indexada) que
        delimita o final do chunk. Retorna 0 quando nao ha pontuacao
        significativa no pedaco.
        """
        # Pontuacoes fortes primeiro — sao fronteiras naturais de frase.
        for regex in (self._re_quebra_forte, self._re_quebra_fraca):
            matches = list(regex.finditer(pedaco))
            if matches:
                return matches[-1].end()
        return 0

    # ------------------------------------------------------------------
    # Chamadas HTTP internas
    # ------------------------------------------------------------------

    async def _gerar_com_retry(self, endpoint: str, payload: dict) -> bytes:
        """Executa ``_chamar_endpoint`` com retry e backoff exponencial.

        Args:
            endpoint: URL completa do endpoint TTS.
            payload: Dicionario JSON a ser enviado no corpo.

        Returns:
            bytes: Conteudo binario retornado pelo servidor.

        Raises:
            TTSErroError: Apos esgotar todas as tentativas.
        """
        ultimo_erro: Exception | None = None
        for tentativa in range(self._max_retries):
            try:
                return await self._chamar_endpoint(endpoint, payload)
            except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
                ultimo_erro = exc
                # Nao espera apos a ultima tentativa.
                if tentativa < self._max_retries - 1:
                    espera = self._espera_backoff(tentativa)
                    logger.warning(
                        "TTS falhou (tentativa %d/%d): %s. Aguardando %ds antes de tentar novamente.",
                        tentativa + 1,
                        self._max_retries,
                        exc,
                        espera,
                    )
                    await asyncio.sleep(espera)
        raise TTSErroError(
            f"Falha no servico de TTS apos {self._max_retries} tentativas: {ultimo_erro}"
        )

    def _espera_backoff(self, tentativa: int) -> int:
        """Calcula o tempo de espera (em segundos) para a tentativa dada.

        Usa a tabela ``_BACKOFF_SEGUNDOS`` (2s, 4s, 8s). Quando a tabela
        nao cobre o indice (caso de max_retries > len(tabela)), repete
        o ultimo valor dobrando a cada vez para preservar a cadencia
        exponencial.
        """
        if tentativa < len(_BACKOFF_SEGUNDOS):
            return _BACKOFF_SEGUNDOS[tentativa]
        return _BACKOFF_SEGUNDOS[-1] * (2 ** (tentativa - len(_BACKOFF_SEGUNDOS) + 1))

    async def _chamar_endpoint(self, endpoint: str, payload: dict) -> bytes:
        """Executa um POST HTTP e trata o resultado.

        Comportamento:
          - HTTP 2xx: retorna o conteudo binario (``response.content``).
          - HTTP 4xx: levanta ``TTSErroError`` imediatamente (erro do
            cliente; nao faz sentido repetir).
          - HTTP 5xx: levanta ``httpx.HTTPStatusError`` para que o loop
            de retry tente novamente.

        Args:
            endpoint: URL completa do endpoint TTS.
            payload: Dicionario JSON a ser enviado.

        Returns:
            bytes: Conteudo binario retornado pelo servidor.
        """
        logger.debug("TTS POST %s payload_chaves=%s", endpoint, list(payload.keys()))
        resposta = await self._client.post(endpoint, json=payload)
        if resposta.status_code >= 500:
            # Levanta HTTPStatusError para o retry capturar.
            resposta.raise_for_status()
        if resposta.status_code >= 400:
            detalhe = self._extrair_detalhe_erro(resposta)
            raise TTSErroError(
                f"Erro do cliente TTS (HTTP {resposta.status_code}): {detalhe}"
            )
        return resposta.content

    def _extrair_detalhe_erro(self, resposta: httpx.Response) -> str:
        """Extrai uma mensagem legivel do corpo de uma resposta de erro."""
        try:
            dados = resposta.json()
        except ValueError:
            return (resposta.text or "").strip()[:500] or "sem corpo de resposta"

        if isinstance(dados, dict):
            for chave in ("detail", "error", "message"):
                if chave in dados:
                    return str(dados[chave])
        return str(dados)[:500]

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Fecha o ``AsyncClient`` subjacente. Idempotente."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None  # type: ignore[assignment]

    async def __aenter__(self) -> "TTSServico":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
