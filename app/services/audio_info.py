"""Utilitários para extração de metadados de arquivos de áudio.

Usado pela UI de download para exibir duração, tamanho, sample rate e
canais do audiolivro final sem precisar reprocessar o pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path

import soundfile as sf

logger = logging.getLogger(__name__)


def _formatar_duracao(segundos: float) -> str:
    """Formata segundos em ``MM:SS`` (ou ``HH:MM:SS`` se >= 1h)."""
    total = int(segundos)
    horas, resto = divmod(total, 3600)
    minutos, segs = divmod(resto, 60)
    if horas > 0:
        return f"{horas}:{minutos:02d}:{segs:02d}"
    return f"{minutos}:{segs:02d}"


def obter_info_audio(caminho: str | Path) -> dict:
    """Retorna metadados de um arquivo de áudio.

    O dicionário retornado contém:
      - ``tamanho_mb`` (float, sempre presente se o arquivo existir)
      - ``duracao`` (str ``MM:SS`` ou ``HH:MM:SS``) se o cabeçalho for
        legível pelo ``soundfile``.
      - ``sample_rate`` (int) e ``canais`` (int) quando disponíveis.

    Se o arquivo não existir, retorna ``{}``.
    Qualquer falha de leitura do cabeçalho é registrada e ignorada — o
    retorno ainda inclui o tamanho em MB do arquivo.
    """
    caminho = Path(caminho)
    info: dict = {}

    if not caminho.exists():
        logger.warning("obter_info_audio: arquivo inexistente %s", caminho)
        return info

    try:
        tamanho_bytes = caminho.stat().st_size
        info["tamanho_mb"] = round(tamanho_bytes / (1024 * 1024), 2)
    except OSError:
        logger.exception("Falha ao obter tamanho do arquivo %s", caminho)
        return info

    try:
        with sf.SoundFile(str(caminho)) as f:
            total_frames = len(f)
            samplerate = f.samplerate
            info["duracao"] = _formatar_duracao(total_frames / samplerate)
            info["sample_rate"] = int(samplerate)
            info["canais"] = int(f.channels)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao ler metadados de audio de %s: %s", caminho, exc)

    return info
