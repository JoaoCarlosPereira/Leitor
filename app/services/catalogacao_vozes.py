r"""Servico de Catalogacao de Vozes (FL-03).

Analisa o dataset local de 500 vozes, indexa os ``info.json`` de cada
amostra em memoria e expoe uma API para sugerir a voz mais adequada
para um personagem a partir de seu genero e faixa etaria.

Cada subdiretorio leaf do dataset segue o layout::

    dataset/<Categoria>/<Subcategoria>/VOICE_<N>_<Cat>_<Sub>/{id}.wav
                                                     \-- info.json --/

O servico mantem um cache em memoria (`_cache`) indexado pelo id da
voz (nome do diretorio leaf) e nao le novamente o dataset a cada
consulta. O metodo ``recarregar_dataset`` permite invalidar o cache
apos a adicao de novas vozes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


# Mapeamento do perfil (genero, idade) -> categoria usada no dataset.
# Permite localizar o diretorio correto a partir de campos normalizados
# vindos do banco de dados, sem que o chamador precise conhecer a
# estrutura de pastas do dataset.
MAPEAMENTO_CATEGORIA: dict[tuple[str, str], str] = {
    ("Male", "Child"): "Criancas/Menino",
    ("Female", "Child"): "Criancas/Menina",
    ("Male", "Adult"): "Homens/Adulto",
    ("Male", "Elderly"): "Homens/Idoso",
    ("Male", "Jovem"): "Homens/Jovem",
    ("Female", "Adult"): "Mulheres/Adulto",
    ("Female", "Elderly"): "Mulheres/Idoso",
    ("Female", "Jovem"): "Mulheres/Jovem",
}


@dataclass
class VozInfo:
    """Metadata de uma voz do dataset.

    Attributes:
        id: Identificador unico da voz (nome do diretorio leaf).
        categoria: Categoria composta ``"<Grupo>/<Subgrupo>"`` do dataset.
        nome: Nome amigavel da voz, lido de ``info.json`` (campo
            ``"nome"``) ou igual ao id quando ausente.
        caminho_audio: Diretorio da amostra de audio; o arquivo
            ``<id>.wav`` deve estar contido neste diretorio.
        caminho_info: Caminho completo do ``info.json`` correspondente.
        prompt: Descricao em linguagem natural da voz (campo
            ``"prompt"`` do ``info.json``).
    """

    id: str
    categoria: str
    nome: str
    caminho_audio: Path
    caminho_info: Path
    prompt: str


class CatalogacaoVozesServico:
    """Indexa o dataset de vozes e sugere amostras por personagem.

    O servico e stateless do ponto de vista do chamador — todo o estado
    vive no cache interno (`_cache`). A leitura do dataset acontece uma
    unica vez no construtor; novas amostras podem ser descobertas via
    ``recarregar_dataset``.
    """

    _ARQUIVO_INFO = "info.json"
    _ARQUIVO_AUDIO_EXTENSAO = ".wav"

    def __init__(self, dataset_path: Path | None = None) -> None:
        """Inicializa o servico e carrega o dataset em memoria.

        Args:
            dataset_path: Caminho para o diretorio raiz do dataset.
                Quando ``None``, usa ``get_settings().dataset_path``.
        """
        if dataset_path is None:
            dataset_path = get_settings().dataset_path
        self._dataset_path = Path(dataset_path)
        self._cache: dict[str, VozInfo] = {}
        self._carregar_dataset()

    def _carregar_dataset(self) -> None:
        """Percorre ``dataset_path`` e indexa todos os ``info.json``.

        O layout esperado e ``<dataset>/<Categoria>/<Subcategoria>/<id>``.
        Diretorios que nao contem ``info.json`` sao ignorados — isso
        permite coexistir arquivos auxiliares (readme, .gitkeep) na
        arvore sem quebrar a indexacao.
        """
        if not self._dataset_path.exists():
            logger.warning(
                "Dataset path nao encontrado: %s — servico iniciara vazio",
                self._dataset_path,
            )
            return

        for categoria_dir in sorted(self._dataset_path.iterdir()):
            if not categoria_dir.is_dir():
                continue
            for subcategoria_dir in sorted(categoria_dir.iterdir()):
                if not subcategoria_dir.is_dir():
                    continue
                categoria = f"{categoria_dir.name}/{subcategoria_dir.name}"
                for voz_dir in sorted(subcategoria_dir.iterdir()):
                    if not voz_dir.is_dir():
                        continue
                    info_file = voz_dir / self._ARQUIVO_INFO
                    if not info_file.exists():
                        logger.debug("info.json ausente em %s — pulando", voz_dir)
                        continue
                    try:
                        with info_file.open(encoding="utf-8") as fp:
                            info = json.load(fp)
                    except (OSError, json.JSONDecodeError) as exc:
                        logger.warning("Falha ao ler %s: %s", info_file, exc)
                        continue
                    self._cache[voz_dir.name] = VozInfo(
                        id=voz_dir.name,
                        categoria=categoria,
                        nome=info.get("nome", voz_dir.name),
                        caminho_audio=voz_dir,
                        caminho_info=info_file,
                        prompt=info.get("prompt", ""),
                    )

        logger.info(
            "Dataset de vozes carregado: %d amostras em %d categorias a partir de %s",
            len(self._cache),
            len(self.listar_categorias()),
            self._dataset_path,
        )

    def sugerir_vozes_por_personagem(
        self,
        genero: str,
        idade: str,
        limite: int = 5,
    ) -> list[VozInfo]:
        """Retorna as vozes mais compativeis com o perfil do personagem.

        A compatibilidade e calculada via :meth:`_calcular_compatibilidade`
        e as melhores pontuacoes sao retornadas ate o ``limite`` indicado.

        Args:
            genero: ``"Male"`` ou ``"Female"``.
            idade: ``"Child"``, ``"Adult"`` ou ``"Elderly"``
                (tambem aceita ``"Jovem"`` presente no dataset).
            limite: Numero maximo de sugestoes a retornar.

        Returns:
            Lista ordenada por compatibilidade decrescente, contendo
            no maximo ``limite`` elementos. Retorna lista vazia se o
            perfil nao tiver categoria mapeada.
        """
        filtro = MAPEAMENTO_CATEGORIA.get((genero, idade))
        if not filtro:
            logger.info(
                "Perfil sem categoria mapeada (genero=%s, idade=%s)",
                genero,
                idade,
            )
            return []

        candidatos = [voz for voz in self._cache.values() if voz.categoria == filtro]
        if not candidatos:
            return []

        if len(candidatos) <= limite:
            return candidatos

        ordenados = sorted(
            candidatos,
            key=lambda v: self._calcular_compatibilidade(v, genero, idade),
            reverse=True,
        )
        return ordenados[:limite]

    def _calcular_compatibilidade(
        self,
        voz: VozInfo,
        genero: str,
        idade: str,
    ) -> float:
        """Calcula score de compatibilidade entre uma voz e o perfil.

        - 1.0 quando a categoria da voz bate exatamente com o mapeamento
          de (genero, idade).
        - 0.5 quando apenas o genero (grupo do dataset) bate.
        - 0.0 caso contrario.

        O calculo eh deterministico: a funcao nao toca o sistema de
        arquivos, apenas o ``VozInfo`` em memoria.
        """
        categoria_esperada = MAPEAMENTO_CATEGORIA.get((genero, idade))
        if categoria_esperada and voz.categoria == categoria_esperada:
            return 1.0

        grupo_esperado = categoria_esperada.split("/", 1)[0] if categoria_esperada else ""
        grupo_voz = voz.categoria.split("/", 1)[0] if voz.categoria else ""

        # Mapas genero -> grupo do dataset (usados para detectar match
        # parcial quando a idade nao bate mas o genero bate).
        genero_para_grupo = {
            "Male": "Homens",
            "Female": "Mulheres",
        }
        grupo_por_genero = genero_para_grupo.get(genero, "")

        if (
            genero in genero_para_grupo
            and voz.categoria.startswith("Criancas/")
            and grupo_por_genero == "Homens"
            and voz.categoria.endswith("Menino")
        ):
            return 0.5
        if (
            genero in genero_para_grupo
            and voz.categoria.startswith("Criancas/")
            and grupo_por_genero == "Mulheres"
            and voz.categoria.endswith("Menina")
        ):
            return 0.5

        if grupo_esperado and grupo_voz == grupo_esperado:
            return 0.5
        if grupo_voz == grupo_por_genero:
            return 0.5
        return 0.0

    def listar_todas_vozes(self, categoria: str | None = None) -> list[VozInfo]:
        """Lista todas as vozes indexadas, opcionalmente filtradas.

        Args:
            categoria: Quando informada, retorna apenas vozes com
                categoria exata. Quando ``None``, retorna todas.

        Returns:
            Lista de ``VozInfo`` ordenada por id.
        """
        if categoria is None:
            return sorted(self._cache.values(), key=lambda v: v.id)
        return sorted(
            (v for v in self._cache.values() if v.categoria == categoria),
            key=lambda v: v.id,
        )

    def listar_categorias(self) -> list[str]:
        """Retorna a lista unica de categorias presentes no dataset."""
        return sorted({voz.categoria for voz in self._cache.values()})

    def obter_info_voz(self, voice_id: str) -> VozInfo | None:
        """Retorna o ``VozInfo`` cacheado para o id informado, ou ``None``."""
        return self._cache.get(voice_id)

    def obter_audio_voz(self, voice_id: str) -> Path | None:
        """Retorna o caminho do arquivo ``.wav`` da voz, se existir.

        Verifica que o arquivo esta presente no disco antes de retornar
        o caminho — isso permite detectar inconsistencias entre o
        ``info.json`` e a amostra de audio.
        """
        info = self._cache.get(voice_id)
        if info is None:
            return None
        candidato = info.caminho_audio / f"{info.id}{self._ARQUIVO_AUDIO_EXTENSAO}"
        if not candidato.exists():
            logger.warning("Audio nao encontrado para voz %s em %s", voice_id, candidato)
            return None
        return candidato

    def contar_vozes(self) -> int:
        """Retorna a quantidade de vozes atualmente indexadas."""
        return len(self._cache)

    def recarregar_dataset(self) -> None:
        """Limpa o cache e reindexa o dataset do disco.

        Util apos a adicao de novas amostras de voz sem precisar
        reiniciar a aplicacao.
        """
        self._cache.clear()
        self._carregar_dataset()
