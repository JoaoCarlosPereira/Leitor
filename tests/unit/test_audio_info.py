"""Testes do servico de informacoes de audio."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestObterInfoAudio:
    def test_arquivo_inexistente_retorna_vazio(self, tmp_path: Path) -> None:
        from app.services.audio_info import obter_info_audio

        caminho = tmp_path / "nao_existe.wav"
        resultado = obter_info_audio(caminho)
        assert resultado == {}

    def test_wav_minimo_retorna_tamanho(self, tmp_path: Path) -> None:
        from app.services.audio_info import obter_info_audio

        caminho = tmp_path / "fake.wav"
        # WAV minimo (header RIFF + data)
        caminho.write_bytes(b"RIFF" + b"\x10\x00\x00\x00" + b"WAVE" + b"\x00" * 20)

        resultado = obter_info_audio(caminho)
        # Pelo menos o tamanho deve ser retornado
        assert "tamanho_mb" in resultado
        assert resultado["tamanho_mb"] >= 0
