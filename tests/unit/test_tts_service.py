"""Testes unitarios do servico de TTS.

Cobrem:
  - InstrucaoAudio.to_instruct
  - TTSServico.__init__ (defaults de Settings)
  - TTSServico.gerar_audio (payload, instruct, cache, retry)
  - TTSServico.criar_prompt_reutilizavel
  - TTSServico.gerar_audio_lote (com e sem chunks)
  - TTSServico._dividir_em_chunks
  - TTSServico.gerar_voz_design
  - TTSServico.normalizar_wav_para_base64
  - TTSServico.close

Os testes usam ``unittest.mock`` (nao exigem libs extras alem de pytest) para
mockar ``httpx.AsyncClient`` e respostas do servidor TTS.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import Settings
from app.services.tts import (
    MAX_CHARS_PADRAO,
    InstrucaoAudio,
    TTSChunkMuitoLongoError,
    TTSErroError,
    TTSServico,
)


# ---------------------------------------------------------------------------
# Helpers para mockar respostas httpx
# ---------------------------------------------------------------------------


def _build_response(
    status_code: int,
    *,
    json_body: dict | None = None,
    content: bytes | None = None,
) -> httpx.Response:
    """Constroi um ``httpx.Response`` valido para uso com o mock."""
    request = httpx.Request("POST", "http://test/")
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=request)
    return httpx.Response(
        status_code, content=content or b"", request=request
    )


def _payload(mock_client: AsyncMock) -> dict[str, Any]:
    """Extrai o dict enviado via ``client.post(..., json=payload)``."""
    kwargs = mock_client.post.call_args.kwargs
    return kwargs["json"]


def _make_mock_client(responses: list) -> AsyncMock:
    """Cria um mock de ``httpx.AsyncClient`` que devolve a lista de respostas.

    Cada item de ``responses`` pode ser:
      - um ``httpx.Response`` (retornado diretamente)
      - uma ``Exception`` (levantada)
    """
    client = AsyncMock()
    client.is_closed = False

    async def post_side_effect(*args, **kwargs):  # type: ignore[no-untyped-def]
        if not responses:
            raise AssertionError("mock_client: nao ha mais respostas configuradas")
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    client.post.side_effect = post_side_effect

    async def aclose(*args, **kwargs):  # type: ignore[no-untyped-def]
        client.is_closed = True

    client.aclose.side_effect = aclose
    return client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_minimo() -> Settings:
    """Retorna uma instancia de Settings com valores minimos para os testes."""
    return Settings(
        tts_base_url="http://tts.local:9999",
        tts_timeout=10,
        tts_max_retries=3,
    )


@pytest.fixture
def servico(settings_minimo: Settings) -> TTSServico:
    """Instancia fresca do servico."""
    return TTSServico(
        base_url=settings_minimo.tts_base_url,
        timeout=settings_minimo.tts_timeout,
        max_retries=settings_minimo.tts_max_retries,
        settings=settings_minimo,
    )


@pytest.fixture
def ref_audio_b64() -> str:
    """Base64 curto usado como audio de referencia em todos os testes."""
    return base64.b64encode(b"audio-de-referencia-fake").decode("ascii")


@pytest.fixture
def wav_fake() -> bytes:
    """Bytes representando um WAV minimo (cabecalho RIFF + dados)."""
    return b"RIFF" + b"\x10\x00\x00\x00" + b"WAVEfmt " + b"fake-data-payload"


def _patch_client(servico: TTSServico, mock_client: AsyncMock) -> Any:
    """Substitui o ``httpx.AsyncClient`` do servico por ``mock_client``."""
    servico._client = mock_client
    return mock_client


# ---------------------------------------------------------------------------
# InstrucaoAudio
# ---------------------------------------------------------------------------


class TestInstrucaoAudio:
    """Testes da dataclass ``InstrucaoAudio``."""

    def test_to_instruct_vazio_quando_todos_campos_vazios(self) -> None:
        instr = InstrucaoAudio()
        assert instr.to_instruct() == ""

    def test_to_instruct_apenas_emocao(self) -> None:
        instr = InstrucaoAudio(emocao="fale de forma alegre")
        assert instr.to_instruct() == "fale de forma alegre"

    def test_to_instruct_combina_todos_os_campos(self) -> None:
        instr = InstrucaoAudio(
            emocao="fale de forma alegre",
            prosodia="fale devagar",
            paralinguistica="[sigh]",
        )
        resultado = instr.to_instruct()
        assert "fale de forma alegre" in resultado
        assert "fale devagar" in resultado
        assert "[sigh]" in resultado
        assert (
            resultado
            == "fale de forma alegre fale devagar [sigh]"
        )

    def test_to_instruct_ignora_campos_em_branco(self) -> None:
        instr = InstrucaoAudio(
            emocao="alegre",
            prosodia="   ",
            paralinguistica="",
        )
        assert instr.to_instruct() == "alegre"

    def test_to_instruct_remove_espacos_extras(self) -> None:
        instr = InstrucaoAudio(
            emocao="  alegre  ",
            prosodia="  devagar  ",
        )
        assert instr.to_instruct() == "alegre devagar"


# ---------------------------------------------------------------------------
# TTSServico - Inicializacao
# ---------------------------------------------------------------------------


class TestInicializacao:
    """Testes do construtor e leitura de configuracao."""

    def test_init_ler_defaults_de_settings(
        self, settings_minimo: Settings
    ) -> None:
        servico = TTSServico(settings=settings_minimo)
        try:
            assert servico._base_url == "http://tts.local:9999"
            assert servico._timeout == 10.0
            assert servico._max_retries == 3
            assert isinstance(servico._client, httpx.AsyncClient)
            assert servico._prompt_cache == {}
        finally:
            asyncio.get_event_loop().run_until_complete(servico.close())

    def test_init_remove_barra_final_da_base_url(
        self, settings_minimo: Settings
    ) -> None:
        servico = TTSServico(
            base_url="http://tts.local:9999/", settings=settings_minimo
        )
        try:
            assert servico._base_url == "http://tts.local:9999"
        finally:
            asyncio.get_event_loop().run_until_complete(servico.close())

    def test_init_argumentos_explicitos_prevalecem(
        self, settings_minimo: Settings
    ) -> None:
        servico = TTSServico(
            base_url="http://outro:1234",
            timeout=5.0,
            max_retries=7,
            settings=settings_minimo,
        )
        try:
            assert servico._base_url == "http://outro:1234"
            assert servico._timeout == 5.0
            assert servico._max_retries == 7
        finally:
            asyncio.get_event_loop().run_until_complete(servico.close())

    def test_init_garante_pelo_menos_uma_tentativa(
        self, settings_minimo: Settings
    ) -> None:
        settings_minimo.tts_max_retries = 0
        servico = TTSServico(settings=settings_minimo)
        try:
            assert servico._max_retries == 1
        finally:
            asyncio.get_event_loop().run_until_complete(servico.close())

    def test_init_usa_settings_global_quando_argumentos_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sem argumentos explicitos, deve usar Settings."""
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("TTS_BASE_URL", "http://env:7777")
        monkeypatch.setenv("TTS_TIMEOUT", "99")
        monkeypatch.setenv("TTS_MAX_RETRIES", "2")
        servico = TTSServico()
        try:
            assert servico._base_url == "http://env:7777"
            assert servico._timeout == 99.0
            assert servico._max_retries == 2
        finally:
            asyncio.get_event_loop().run_until_complete(servico.close())
            get_settings.cache_clear()


# ---------------------------------------------------------------------------
# TTSServico.gerar_audio
# ---------------------------------------------------------------------------


class TestGerarAudio:
    """Testes do metodo principal de geracao de audio."""

    @pytest.mark.asyncio
    async def test_envia_payload_basico(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
        wav_fake: bytes,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(200, content=wav_fake)]
        )
        _patch_client(servico, mock_client)
        resultado = await servico.gerar_audio(
            texto="Ola, mundo!",
            ref_audio_base64=ref_audio_b64,
        )
        assert resultado == wav_fake
        assert mock_client.post.call_count == 1
        url, kwargs = mock_client.post.call_args[0][0], mock_client.post.call_args[1]
        assert url == "http://tts.local:9999/v1/audio/clone"
        corpo: dict[str, Any] = _payload(mock_client)
        assert corpo["input"] == "Ola, mundo!"
        assert corpo["ref_audio"] == ref_audio_b64
        assert corpo["language"] == "Portuguese"
        assert corpo["response_format"] == "wav"
        assert "ref_text" not in corpo
        assert "instruct" not in corpo

    @pytest.mark.asyncio
    async def test_inclui_ref_text_quando_informado(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
        wav_fake: bytes,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(200, content=wav_fake)]
        )
        _patch_client(servico, mock_client)
        await servico.gerar_audio(
            texto="Testando",
            ref_audio_base64=ref_audio_b64,
            ref_text="meu texto de referencia",
        )
        corpo = _payload(mock_client)
        assert corpo["ref_text"] == "meu texto de referencia"

    @pytest.mark.asyncio
    async def test_inclui_instruct_quando_instrucao_tem_conteudo(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
        wav_fake: bytes,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(200, content=wav_fake)]
        )
        _patch_client(servico, mock_client)
        await servico.gerar_audio(
            texto="Testando",
            ref_audio_base64=ref_audio_b64,
            instrucao=InstrucaoAudio(
                emocao="fale com raiva",
                prosodia="gritando",
            ),
        )
        corpo = _payload(mock_client)
        assert corpo["instruct"] == "fale com raiva gritando"

    @pytest.mark.asyncio
    async def test_nao_inclui_instruct_quando_instrucao_vazia(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
        wav_fake: bytes,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(200, content=wav_fake)]
        )
        _patch_client(servico, mock_client)
        await servico.gerar_audio(
            texto="Testando",
            ref_audio_base64=ref_audio_b64,
            instrucao=InstrucaoAudio(),
        )
        corpo = _payload(mock_client)
        assert "instruct" not in corpo

    @pytest.mark.asyncio
    async def test_usa_voice_clone_prompt_em_cache(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
        wav_fake: bytes,
    ) -> None:
        chave = servico._chave_prompt(ref_audio_b64, "ref-x")
        servico._prompt_cache[chave] = "PROMPT-PRE-COMPUTADO"
        mock_client = _make_mock_client(
            [_build_response(200, content=wav_fake)]
        )
        _patch_client(servico, mock_client)
        await servico.gerar_audio(
            texto="Testando",
            ref_audio_base64=ref_audio_b64,
            ref_text="ref-x",
        )
        corpo = _payload(mock_client)
        assert corpo["voice_clone_prompt"] == "PROMPT-PRE-COMPUTADO"

    @pytest.mark.asyncio
    async def test_cache_desabilitado_nao_envia_prompt(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
        wav_fake: bytes,
    ) -> None:
        servico._prompt_cache[servico._chave_prompt(ref_audio_b64, "ref-y")] = "PROMPT"
        mock_client = _make_mock_client(
            [_build_response(200, content=wav_fake)]
        )
        _patch_client(servico, mock_client)
        await servico.gerar_audio(
            texto="Testando",
            ref_audio_base64=ref_audio_b64,
            ref_text="ref-y",
            use_prompt_cache=False,
        )
        corpo = _payload(mock_client)
        assert "voice_clone_prompt" not in corpo

    @pytest.mark.asyncio
    async def test_retry_em_erro_5xx(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
        wav_fake: bytes,
    ) -> None:
        mock_client = _make_mock_client(
            [
                _build_response(500, content=b"erro 1"),
                _build_response(502, content=b"erro 2"),
                _build_response(200, content=wav_fake),
            ]
        )
        # Acelera o teste: zero delay entre tentativas.
        with patch("app.services.tts.asyncio.sleep", new=AsyncMock()):
            _patch_client(servico, mock_client)
            resultado = await servico.gerar_audio(
                texto="Testando",
                ref_audio_base64=ref_audio_b64,
            )
        assert resultado == wav_fake
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_retry_em_timeout(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
        wav_fake: bytes,
    ) -> None:
        mock_client = _make_mock_client(
            [
                httpx.TimeoutException("timeout 1"),
                httpx.TimeoutException("timeout 2"),
                _build_response(200, content=wav_fake),
            ]
        )
        with patch("app.services.tts.asyncio.sleep", new=AsyncMock()):
            _patch_client(servico, mock_client)
            resultado = await servico.gerar_audio(
                texto="Testando",
                ref_audio_base64=ref_audio_b64,
            )
        assert resultado == wav_fake
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_levanta_tts_erro_apos_esgotar_retries(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
    ) -> None:
        mock_client = _make_mock_client(
            [
                _build_response(500, content=b"servidor quebrado"),
                _build_response(500, content=b"servidor quebrado"),
                _build_response(500, content=b"servidor quebrado"),
            ]
        )
        with patch("app.services.tts.asyncio.sleep", new=AsyncMock()):
            _patch_client(servico, mock_client)
            with pytest.raises(TTSErroError) as info:
                await servico.gerar_audio(
                    texto="Testando",
                    ref_audio_base64=ref_audio_b64,
                )
        assert "3" in str(info.value)
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_erro_4xx_nao_gera_retry(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(400, json_body={"detail": "parametro invalido"})]
        )
        _patch_client(servico, mock_client)
        with pytest.raises(TTSErroError) as info:
            await servico.gerar_audio(
                texto="Testando",
                ref_audio_base64=ref_audio_b64,
            )
        assert "parametro invalido" in str(info.value)
        assert "400" in str(info.value)
        # 4xx nao deve ser retentado.
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_erro_4xx_com_detalhe_texto(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(422, content=b"erro de validacao")]
        )
        _patch_client(servico, mock_client)
        with pytest.raises(TTSErroError) as info:
            await servico.gerar_audio(
                texto="x",
                ref_audio_base64=ref_audio_b64,
            )
        assert "422" in str(info.value)
        assert "erro de validacao" in str(info.value)

    @pytest.mark.asyncio
    async def test_texto_vazio_levanta_value_error(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
    ) -> None:
        with pytest.raises(ValueError):
            await servico.gerar_audio(texto="", ref_audio_base64=ref_audio_b64)

    @pytest.mark.asyncio
    async def test_ref_audio_vazio_levanta_value_error(
        self,
        servico: TTSServico,
    ) -> None:
        with pytest.raises(ValueError):
            await servico.gerar_audio(texto="ola", ref_audio_base64="")

    def test_aplica_timeout_configurado(
        self, settings_minimo: Settings
    ) -> None:
        """O AsyncClient do servico deve usar o timeout configurado."""
        servico = TTSServico(
            base_url=settings_minimo.tts_base_url,
            timeout=42.0,
            max_retries=1,
            settings=settings_minimo,
        )
        try:
            assert servico._client.timeout.connect == 42.0
        finally:
            asyncio.get_event_loop().run_until_complete(servico.close())

    @pytest.mark.asyncio
    async def test_retry_usa_backoff_de_2_4_8_segundos(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
    ) -> None:
        """Verifica que as esperas entre tentativas seguem 2s, 4s, 8s."""
        esperas: list[float] = []

        async def fake_sleep(t: float) -> None:
            esperas.append(t)

        mock_client = _make_mock_client(
            [
                _build_response(500, content=b"x"),
                _build_response(500, content=b"x"),
                _build_response(500, content=b"x"),
            ]
        )
        _patch_client(servico, mock_client)
        with patch("app.services.tts.asyncio.sleep", new=fake_sleep):
            with pytest.raises(TTSErroError):
                await servico.gerar_audio(
                    texto="ola",
                    ref_audio_base64=ref_audio_b64,
                )
        # 3 tentativas -> 2 esperas (apos tentativa 0 e 1).
        assert esperas == [2, 4]
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_retry_5xx_levanta_http_status_error(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
    ) -> None:
        """Garante que 5xx leva a retries ate esgotar e levanta TTSErroError."""
        mock_client = _make_mock_client(
            [
                _build_response(500, content=b"erro"),
                _build_response(500, content=b"erro"),
                _build_response(500, content=b"erro"),
            ]
        )
        with patch("app.services.tts.asyncio.sleep", new=AsyncMock()):
            _patch_client(servico, mock_client)
            with pytest.raises(TTSErroError):
                await servico.gerar_audio(
                    texto="x",
                    ref_audio_base64=ref_audio_b64,
                )
        assert mock_client.post.call_count == 3


# ---------------------------------------------------------------------------
# TTSServico.criar_prompt_reutilizavel
# ---------------------------------------------------------------------------


class TestCriarPromptReutilizavel:
    """Testes do endpoint dedicado de criacao de prompt."""

    @pytest.mark.asyncio
    async def test_retorna_prompt_e_popula_cache(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(200, json_body={"voice_clone_prompt": "PROMPT-XYZ"})]
        )
        _patch_client(servico, mock_client)
        prompt = await servico.criar_prompt_reutilizavel(
            ref_audio_base64=ref_audio_b64, ref_text="ref-x"
        )
        assert prompt == "PROMPT-XYZ"
        assert (
            servico._prompt_cache[
                servico._chave_prompt(ref_audio_b64, "ref-x")
            ]
            == "PROMPT-XYZ"
        )

    @pytest.mark.asyncio
    async def test_prompt_vazio_nao_popula_cache(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(200, json_body={"voice_clone_prompt": ""})]
        )
        _patch_client(servico, mock_client)
        prompt = await servico.criar_prompt_reutilizavel(
            ref_audio_base64=ref_audio_b64, ref_text="ref"
        )
        assert prompt == ""
        assert servico._prompt_cache == {}

    @pytest.mark.asyncio
    async def test_ref_audio_vazio_levanta_value_error(
        self,
        servico: TTSServico,
    ) -> None:
        with pytest.raises(ValueError):
            await servico.criar_prompt_reutilizavel(
                ref_audio_base64="", ref_text="x"
            )

    @pytest.mark.asyncio
    async def test_erro_5xx_no_prompt_levanta_tts_erro(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(500, content=b"servidor fora")]
        )
        _patch_client(servico, mock_client)
        with pytest.raises(TTSErroError) as info:
            await servico.criar_prompt_reutilizavel(
                ref_audio_base64=ref_audio_b64, ref_text="x"
            )
        assert "500" in str(info.value)

    @pytest.mark.asyncio
    async def test_endpoint_correto(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(200, json_body={"voice_clone_prompt": "P"})]
        )
        _patch_client(servico, mock_client)
        await servico.criar_prompt_reutilizavel(
            ref_audio_base64=ref_audio_b64, ref_text="x"
        )
        url = mock_client.post.call_args[0][0]
        assert url == "http://tts.local:9999/v1/audio/clone/prompt"


# ---------------------------------------------------------------------------
# TTSServico._dividir_em_chunks
# ---------------------------------------------------------------------------


class TestDividirEmChunks:
    """Testes da funcao de chunking de texto."""

    def test_texto_vazio_retorna_lista_vazia(self, servico: TTSServico) -> None:
        assert servico._dividir_em_chunks("") == []
        assert servico._dividir_em_chunks("   ") == []

    def test_texto_menor_que_max_chars_retorna_uma_pagina(
        self, servico: TTSServico
    ) -> None:
        texto = "Ola, mundo!"
        assert servico._dividir_em_chunks(texto) == [texto]

    def test_texto_exatamente_max_chars(self, servico: TTSServico) -> None:
        texto = "a" * MAX_CHARS_PADRAO
        assert servico._dividir_em_chunks(texto) == [texto]

    def test_quebra_em_pontuacao_forte(self, servico: TTSServico) -> None:
        max_chars = 30
        texto = "Primeira frase longa aqui. Segunda frase longa aqui."
        chunks = servico._dividir_em_chunks(texto, max_chars=max_chars)
        assert all(len(c) <= max_chars for c in chunks)
        assert "".join(chunks).replace(" ", "") == texto.replace(" ", "")

    def test_quebra_em_pontuacao_fraca_quando_nao_ha_forte(
        self, servico: TTSServico
    ) -> None:
        max_chars = 25
        texto = "um texto bem grande sem ponto final, com virgula aqui"
        chunks = servico._dividir_em_chunks(texto, max_chars=max_chars)
        assert all(len(c) <= max_chars for c in chunks)
        assert all(c.strip() for c in chunks)
        assert "".join(chunks).replace(" ", "") == texto.replace(" ", "")

    def test_quebra_no_ultimo_espaco_quando_nao_ha_pontuacao(
        self, servico: TTSServico
    ) -> None:
        max_chars = 20
        texto = "abcdefghij klmnopqrst uvwxyzabcd efghijklmn"
        chunks = servico._dividir_em_chunks(texto, max_chars=max_chars)
        assert all(len(c) <= max_chars for c in chunks)
        assert "".join(chunks).replace(" ", "") == texto.replace(" ", "")

    def test_preserva_pontuacao_no_final_dos_chunks(
        self, servico: TTSServico
    ) -> None:
        max_chars = 25
        texto = "Ola! Como vai? Tudo bem. Espero que sim."
        chunks = servico._dividir_em_chunks(texto, max_chars=max_chars)
        juncao = "".join(chunks)
        assert "!" in juncao
        assert "?" in juncao
        assert "." in juncao

    def test_max_chars_invalido_levanta_value_error(
        self, servico: TTSServico
    ) -> None:
        with pytest.raises(ValueError):
            servico._dividir_em_chunks("texto qualquer", max_chars=0)

    def test_nao_corta_no_meio_de_palavra_sem_espaco(
        self, servico: TTSServico
    ) -> None:
        max_chars = 10
        texto = "abcdefghijklmnopqrstuvwxyz"
        chunks = servico._dividir_em_chunks(texto, max_chars=max_chars)
        assert all(len(c) <= max_chars for c in chunks)
        assert "".join(chunks) == texto

    def test_multiplos_chunks_em_sequencia(self, servico: TTSServico) -> None:
        max_chars = 30
        # Tres frases com tamanho aproximado a 30 chars cada.
        texto = (
            "Primeira frase de teste. "  # 25
            "Segunda frase de teste. "   # 25
            "Terceira frase de teste."   # 25
        )
        chunks = servico._dividir_em_chunks(texto, max_chars=max_chars)
        assert len(chunks) >= 2
        assert all(len(c) <= max_chars for c in chunks)
        assert "".join(chunks).replace(" ", "") == texto.replace(" ", "")

    def test_texto_nao_quebra_instrucao_especifica(
        self, servico: TTSServico
    ) -> None:
        """Frase terminando em interrogacao deve ser preservada."""
        max_chars = 20
        texto = "Voce ouviu? Nao escutei."
        chunks = servico._dividir_em_chunks(texto, max_chars=max_chars)
        # O "?" deve estar dentro de um chunk.
        assert any("?" in c for c in chunks)


# ---------------------------------------------------------------------------
# TTSServico.gerar_audio_lote
# ---------------------------------------------------------------------------


class TestGerarAudioLote:
    """Testes do processamento em batch por personagem."""

    @pytest.mark.asyncio
    async def test_lista_vazia_levanta_value_error(
        self, servico: TTSServico
    ) -> None:
        with pytest.raises(ValueError):
            await servico.gerar_audio_lote(
                falas=[], referencia_voz={"ref_audio_base64": "abc", "ref_text": ""}
            )

    @pytest.mark.asyncio
    async def test_referencia_invalida_levanta_value_error(
        self, servico: TTSServico
    ) -> None:
        with pytest.raises(ValueError):
            await servico.gerar_audio_lote(
                falas=[{"texto": "ola"}],
                referencia_voz={"ref_text": ""},
            )

    @pytest.mark.asyncio
    async def test_instrucao_invalida_levanta_value_error(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
    ) -> None:
        with pytest.raises(ValueError):
            await servico.gerar_audio_lote(
                falas=[{"texto": "ola", "instrucao": "nao-e-dataclass"}],
                referencia_voz={"ref_audio_base64": ref_audio_b64},
            )

    @pytest.mark.asyncio
    async def test_processa_multiplas_falas(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
        wav_fake: bytes,
    ) -> None:
        falas = [
            {"texto": "Ola!", "instrucao": None, "lang": "Portuguese"},
            {"texto": "Como vai?", "instrucao": None, "lang": "Portuguese"},
        ]
        referencia = {"ref_audio_base64": ref_audio_b64, "ref_text": "ref-x"}
        mock_client = _make_mock_client(
            [
                _build_response(200, content=wav_fake),
                _build_response(200, content=wav_fake),
            ]
        )
        _patch_client(servico, mock_client)
        audios = await servico.gerar_audio_lote(falas, referencia)
        assert len(audios) == 2
        assert all(a == wav_fake for a in audios)
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_chama_gerar_audio_por_chunk(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
        wav_fake: bytes,
    ) -> None:
        # Texto de ~900 chars dividido com max_chars=500 (padrao) gera 2 chunks.
        texto_longo = "Frase grande com ponto final. " * 30
        chunks = servico._dividir_em_chunks(texto_longo)  # usa max_chars=500
        assert len(chunks) >= 2

        falas = [{"texto": texto_longo, "instrucao": None, "lang": "Portuguese"}]
        mock_client = _make_mock_client(
            [_build_response(200, content=wav_fake) for _ in chunks]
        )
        _patch_client(servico, mock_client)
        audios = await servico.gerar_audio_lote(
            falas, {"ref_audio_base64": ref_audio_b64}
        )
        assert mock_client.post.call_count == len(chunks)
        assert len(audios) == len(chunks)

    @pytest.mark.asyncio
    async def test_passa_instrucao_para_cada_chunk(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
        wav_fake: bytes,
    ) -> None:
        texto_longo = "A frase grande com ponto. " * 30
        instrucao = InstrucaoAudio(emocao="alegre", prosodia="devagar")
        falas = [
            {"texto": texto_longo, "instrucao": instrucao, "lang": "Portuguese"}
        ]
        # Calcula quantos chunks serao gerados para fornecer mocks suficientes.
        chunks = servico._dividir_em_chunks(texto_longo)
        mock_client = _make_mock_client(
            [_build_response(200, content=wav_fake) for _ in chunks]
        )
        _patch_client(servico, mock_client)
        await servico.gerar_audio_lote(
            falas, {"ref_audio_base64": ref_audio_b64}
        )
        for chamada in mock_client.post.call_args_list:
            corpo = chamada.kwargs["json"]
            assert corpo["instruct"] == "alegre devagar"

    @pytest.mark.asyncio
    async def test_usa_ref_text_da_referencia(
        self,
        servico: TTSServico,
        ref_audio_b64: str,
        wav_fake: bytes,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(200, content=wav_fake)]
        )
        _patch_client(servico, mock_client)
        await servico.gerar_audio_lote(
            [{"texto": "Ola", "instrucao": None, "lang": "Portuguese"}],
            {"ref_audio_base64": ref_audio_b64, "ref_text": "minha-ref"},
        )
        corpo = _payload(mock_client)
        assert corpo["ref_text"] == "minha-ref"


# ---------------------------------------------------------------------------
# TTSServico.gerar_voz_design
# ---------------------------------------------------------------------------


class TestGerarVozDesign:
    """Testes do Voice Design."""

    @pytest.mark.asyncio
    async def test_envia_payload_correto(
        self,
        servico: TTSServico,
        wav_fake: bytes,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(200, content=wav_fake)]
        )
        _patch_client(servico, mock_client)
        resultado = await servico.gerar_voz_design(
            descricao="voz masculina adulta, grave e solene"
        )
        assert resultado == wav_fake
        url = mock_client.post.call_args[0][0]
        assert url == "http://tts.local:9999/v1/audio/design"
        corpo: dict[str, Any] = _payload(mock_client)
        assert "voz masculina adulta, grave e solene" in corpo["instruct"]
        assert "language" in corpo
        assert corpo["response_format"] == "wav"

    @pytest.mark.asyncio
    async def test_usa_texto_referencia_customizado(
        self,
        servico: TTSServico,
        wav_fake: bytes,
    ) -> None:
        mock_client = _make_mock_client(
            [_build_response(200, content=wav_fake)]
        )
        _patch_client(servico, mock_client)
        await servico.gerar_voz_design(
            descricao="voz feminina", texto_referencia="meu texto custom"
        )
        corpo = _payload(mock_client)
        assert corpo["input"] == "meu texto custom"

    @pytest.mark.asyncio
    async def test_descricao_vazia_levanta_value_error(
        self,
        servico: TTSServico,
    ) -> None:
        with pytest.raises(ValueError):
            await servico.gerar_voz_design(descricao="")

    @pytest.mark.asyncio
    async def test_retry_em_erro_5xx(
        self,
        servico: TTSServico,
        wav_fake: bytes,
    ) -> None:
        mock_client = _make_mock_client(
            [
                _build_response(500, content=b"x"),
                _build_response(200, content=wav_fake),
            ]
        )
        with patch("app.services.tts.asyncio.sleep", new=AsyncMock()):
            _patch_client(servico, mock_client)
            resultado = await servico.gerar_voz_design(descricao="voz jovem")
        assert resultado == wav_fake
        assert mock_client.post.call_count == 2


# ---------------------------------------------------------------------------
# TTSServico.normalizar_wav_para_base64
# ---------------------------------------------------------------------------


class TestNormalizarWavParaBase64:
    """Testes do helper sincrono de leitura de WAV."""

    def test_le_arquivo_wav_e_codifica_em_base64(
        self, servico: TTSServico, tmp_path: Path
    ) -> None:
        caminho = tmp_path / "amostra.wav"
        conteudo = b"RIFF" + b"\x00" * 100
        caminho.write_bytes(conteudo)
        resultado = servico.normalizar_wav_para_base64(str(caminho))
        esperado = base64.b64encode(conteudo).decode("ascii")
        assert resultado == esperado

    def test_arquivo_inexistente_levanta_file_not_found(
        self, servico: TTSServico, tmp_path: Path
    ) -> None:
        caminho = tmp_path / "nao-existe.wav"
        with pytest.raises(FileNotFoundError):
            servico.normalizar_wav_para_base64(str(caminho))


# ---------------------------------------------------------------------------
# TTSServico.close
# ---------------------------------------------------------------------------


class TestClose:
    """Testes do ciclo de vida do AsyncClient."""

    @pytest.mark.asyncio
    async def test_close_fecha_cliente(self, servico: TTSServico) -> None:
        await servico.close()
        assert servico._client is None

    @pytest.mark.asyncio
    async def test_close_idempotente(self, servico: TTSServico) -> None:
        await servico.close()
        # Segunda chamada nao deve levantar.
        await servico.close()
        assert servico._client is None

    @pytest.mark.asyncio
    async def test_context_manager(self, settings_minimo: Settings) -> None:
        async with TTSServico(settings=settings_minimo) as svc:
            assert isinstance(svc, TTSServico)
            assert svc._client is not None
        assert svc._client is None


# ---------------------------------------------------------------------------
# Cobertura de metodos privados auxiliares
# ---------------------------------------------------------------------------


class TestHelpersPrivados:
    """Cobre metodos privados de pequena superficie."""

    def test_chave_prompt_curta(self, servico: TTSServico) -> None:
        chave = servico._chave_prompt("ABCDEF" * 100, "ref")
        # O tamanho da chave e prefixo (32) + ":" + ref_text.
        assert len(chave) == 32 + 1 + len("ref")

    def test_chave_prompt_para_ref_audio_vazio(self, servico: TTSServico) -> None:
        chave = servico._chave_prompt("", "ref")
        assert chave == ":ref"

    def test_encontrar_ponto_corte_ponto_final(self, servico: TTSServico) -> None:
        pedaco = "Frase legal. Outra frase"
        assert servico._encontrar_ponto_corte(pedaco) == len("Frase legal.")

    def test_encontrar_ponto_corte_virgula(self, servico: TTSServico) -> None:
        pedaco = "frase sem fim, mais texto aqui"
        assert servico._encontrar_ponto_corte(pedaco) == len("frase sem fim,")

    def test_encontrar_ponto_corte_sem_pontuacao(self, servico: TTSServico) -> None:
        assert servico._encontrar_ponto_corte("abcdefghij") == 0

    def test_espera_backoff_tabela(self, servico: TTSServico) -> None:
        assert servico._espera_backoff(0) == 2
        assert servico._espera_backoff(1) == 4
        assert servico._espera_backoff(2) == 8

    def test_espera_backoff_extrapola_tabela(self, servico: TTSServico) -> None:
        # Apos 3 (indice 3) dobra a partir de 8.
        assert servico._espera_backoff(3) == 16
        assert servico._espera_backoff(4) == 32
