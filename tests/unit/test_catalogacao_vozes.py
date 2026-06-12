"""Testes do servico de Catalogacao de Vozes.

Cobre a indexacao do dataset a partir de um diretorio temporario,
o mapeamento por (genero, idade), a listagem de categorias/vozes e
os caminhos de recuperacao de info e audio.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.catalogacao_vozes import (
    MAPEAMENTO_CATEGORIA,
    CatalogacaoVozesServico,
    VozInfo,
)


@pytest.fixture
def dataset_tmp(tmp_path: Path) -> Path:
    """Cria um dataset fake com 4 categorias e 2 vozes por categoria."""
    dataset = tmp_path / "dataset"
    estrutura = {
        "Criancas/Menina": ["VOICE_001_Criancas_Menina", "VOICE_002_Criancas_Menina"],
        "Criancas/Menino": ["VOICE_010_Criancas_Menino"],
        "Homens/Adulto": ["VOICE_100_Homens_Adulto"],
        "Homens/Idoso": ["VOICE_200_Homens_Idoso"],
        "Homens/Jovem": ["VOICE_300_Homens_Jovem"],
        "Mulheres/Adulto": ["VOICE_400_Mulheres_Adulto", "VOICE_401_Mulheres_Adulto"],
        "Mulheres/Idoso": ["VOICE_500_Mulheres_Idoso"],
        "Mulheres/Jovem": ["VOICE_600_Mulheres_Jovem"],
    }
    for pasta, vozes in estrutura.items():
        for voz in vozes:
            voz_dir = dataset / pasta / voz
            voz_dir.mkdir(parents=True)
            info = {
                "id": voz,
                "nome": f"Nome amigavel de {voz}",
                "prompt": f"Descricao natural da voz {voz}.",
            }
            (voz_dir / "info.json").write_text(
                json.dumps(info), encoding="utf-8"
            )
            (voz_dir / f"{voz}.wav").write_bytes(b"RIFFfake")
    return dataset


@pytest.fixture
def servico(dataset_tmp: Path) -> CatalogacaoVozesServico:
    """Instancia o servico apontando para o dataset temporario."""
    return CatalogacaoVozesServico(dataset_path=dataset_tmp)


class TestVozInfo:
    """Valida o dataclass de metadata."""

    def test_campos_obrigatorios(self) -> None:
        voz = VozInfo(
            id="VOICE_X",
            categoria="Homens/Adulto",
            nome="X",
            caminho_audio=Path("/tmp/x"),
            caminho_info=Path("/tmp/x/info.json"),
            prompt="descricao",
        )
        assert voz.id == "VOICE_X"
        assert voz.categoria == "Homens/Adulto"
        assert voz.nome == "X"
        assert voz.prompt == "descricao"


class TestCarregamento:
    """Valida a leitura do dataset em disco."""

    def test_carrega_todas_as_vozes(self, servico: CatalogacaoVozesServico) -> None:
        assert servico.contar_vozes() == 10

    def test_categoria_normalizada(self, servico: CatalogacaoVozesServico) -> None:
        info = servico.obter_info_voz("VOICE_100_Homens_Adulto")
        assert info is not None
        assert info.categoria == "Homens/Adulto"

    def test_prompt_e_nome_extraidos(self, servico: CatalogacaoVozesServico) -> None:
        info = servico.obter_info_voz("VOICE_100_Homens_Adulto")
        assert info is not None
        assert info.nome == "Nome amigavel de VOICE_100_Homens_Adulto"
        assert info.prompt == "Descricao natural da voz VOICE_100_Homens_Adulto."

    def test_dataset_inexistente_retorna_cache_vazio(self, tmp_path: Path) -> None:
        servico = CatalogacaoVozesServico(dataset_path=tmp_path / "nao-existe")
        assert servico.contar_vozes() == 0
        assert servico.listar_categorias() == []

    def test_info_json_ausente_e_ignorado(self, tmp_path: Path) -> None:
        dataset = tmp_path / "dataset"
        voz_dir = dataset / "Homens" / "Adulto" / "VOICE_SEM_INFO_Homens_Adulto"
        voz_dir.mkdir(parents=True)
        (voz_dir / "VOICE_SEM_INFO_Homens_Adulto.wav").write_bytes(b"x")
        # Sem info.json: voz nao deve ser indexada
        servico = CatalogacaoVozesServico(dataset_path=dataset)
        assert servico.contar_vozes() == 0

    def test_info_json_invalido_e_ignorado(self, tmp_path: Path) -> None:
        dataset = tmp_path / "dataset"
        voz_dir = dataset / "Homens" / "Adulto" / "VOICE_ERR_Homens_Adulto"
        voz_dir.mkdir(parents=True)
        (voz_dir / "info.json").write_text("{ json invalido", encoding="utf-8")
        servico = CatalogacaoVozesServico(dataset_path=dataset)
        assert servico.contar_vozes() == 0

    def test_nome_padrao_quando_campo_nome_ausente(self, tmp_path: Path) -> None:
        dataset = tmp_path / "dataset"
        voz_dir = dataset / "Homens" / "Adulto" / "VOICE_NOMEDEF_Homens_Adulto"
        voz_dir.mkdir(parents=True)
        (voz_dir / "info.json").write_text(
            json.dumps({"id": "VOICE_NOMEDEF_Homens_Adulto", "prompt": "ok"}),
            encoding="utf-8",
        )
        servico = CatalogacaoVozesServico(dataset_path=dataset)
        info = servico.obter_info_voz("VOICE_NOMEDEF_Homens_Adulto")
        assert info is not None
        assert info.nome == "VOICE_NOMEDEF_Homens_Adulto"


class TestSugerirVozes:
    """Valida o mapeamento (genero, idade) -> categoria."""

    @pytest.mark.parametrize(
        ("genero", "idade", "categoria_esperada"),
        [
            ("Male", "Child", "Criancas/Menino"),
            ("Female", "Child", "Criancas/Menina"),
            ("Male", "Adult", "Homens/Adulto"),
            ("Male", "Elderly", "Homens/Idoso"),
            ("Male", "Jovem", "Homens/Jovem"),
            ("Female", "Adult", "Mulheres/Adulto"),
            ("Female", "Elderly", "Mulheres/Idoso"),
            ("Female", "Jovem", "Mulheres/Jovem"),
        ],
    )
    def test_mapeamento_completo(
        self,
        servico: CatalogacaoVozesServico,
        genero: str,
        idade: str,
        categoria_esperada: str,
    ) -> None:
        sugestoes = servico.sugerir_vozes_por_personagem(genero, idade)
        assert len(sugestoes) >= 1
        assert all(v.categoria == categoria_esperada for v in sugestoes)

    def test_limite_respeitado(self, servico: CatalogacaoVozesServico) -> None:
        sugestoes = servico.sugerir_vozes_por_personagem("Male", "Adult", limite=1)
        assert len(sugestoes) == 1

    def test_perfil_sem_mapeamento_retorna_vazio(
        self, servico: CatalogacaoVozesServico
    ) -> None:
        assert servico.sugerir_vozes_por_personagem("Outro", "Adult") == []
        assert servico.sugerir_vozes_por_personagem("Male", "Desconhecida") == []

    def test_ordenacao_por_compatibilidade(self, tmp_path: Path) -> None:
        """Vozes de categoria exata vem antes das de mesmo genero mas outra idade."""
        dataset = tmp_path / "dataset"
        layout = {
            ("Homens", "Adulto"): ["VOICE_A_Homens_Adulto"],
            ("Homens", "Idoso"): ["VOICE_B_Homens_Idoso"],
            ("Criancas", "Menino"): ["VOICE_C_Criancas_Menino"],
        }
        for (pai, sub), ids in layout.items():
            for vid in ids:
                d = dataset / pai / sub / vid
                d.mkdir(parents=True)
                (d / "info.json").write_text(
                    json.dumps({"id": vid, "prompt": "ok"}), encoding="utf-8"
                )
                (d / f"{vid}.wav").write_bytes(b"x")
        servico = CatalogacaoVozesServico(dataset_path=dataset)
        sugestoes = servico.sugerir_vozes_por_personagem("Male", "Adult", limite=3)
        assert sugestoes[0].categoria == "Homens/Adulto"
        # Demais entradas existem mas tem score menor; mantem ordem deterministica
        assert all(s.categoria in {"Homens/Adulto", "Homens/Idoso"} for s in sugestoes)

    def test_match_genero_para_criancas(self, tmp_path: Path) -> None:
        """Adulto masculino deve preferir Homens/Adulto (1.0) sobre Criancas/Menino (0.5)."""
        dataset = tmp_path / "dataset"
        for (pai, sub), vid in [
            (("Homens", "Adulto"), "VOICE_X_Homens_Adulto"),
            (("Criancas", "Menino"), "VOICE_Y_Criancas_Menino"),
        ]:
            d = dataset / pai / sub / vid
            d.mkdir(parents=True)
            (d / "info.json").write_text(
                json.dumps({"id": vid, "prompt": "ok"}), encoding="utf-8"
            )
            (d / f"{vid}.wav").write_bytes(b"x")
        servico = CatalogacaoVozesServico(dataset_path=dataset)
        sugestoes = servico.sugerir_vozes_por_personagem("Male", "Adult", limite=1)
        assert sugestoes[0].categoria == "Homens/Adulto"


class TestListagens:
    """Valida os metodos de listagem."""

    def test_listar_categorias(self, servico: CatalogacaoVozesServico) -> None:
        categorias = servico.listar_categorias()
        assert sorted(categorias) == categorias
        assert "Homens/Adulto" in categorias
        assert "Mulheres/Jovem" in categorias

    def test_listar_todas_vozes(self, servico: CatalogacaoVozesServico) -> None:
        assert len(servico.listar_todas_vozes()) == 10

    def test_listar_vozes_por_categoria(
        self, servico: CatalogacaoVozesServico
    ) -> None:
        mulheres_adulto = servico.listar_todas_vozes("Mulheres/Adulto")
        assert len(mulheres_adulto) == 2
        assert all(v.categoria == "Mulheres/Adulto" for v in mulheres_adulto)

    def test_listar_vozes_categoria_inexistente(
        self, servico: CatalogacaoVozesServico
    ) -> None:
        assert servico.listar_todas_vozes("Nao/Existe") == []


class TestAcessoVoz:
    """Valida os metodos de acesso direto a uma voz."""

    def test_obter_info_voz_existente(
        self, servico: CatalogacaoVozesServico
    ) -> None:
        info = servico.obter_info_voz("VOICE_100_Homens_Adulto")
        assert info is not None
        assert info.id == "VOICE_100_Homens_Adulto"

    def test_obter_info_voz_inexistente(
        self, servico: CatalogacaoVozesServico
    ) -> None:
        assert servico.obter_info_voz("NAO_EXISTE") is None

    def test_obter_audio_voz_existente(
        self, servico: CatalogacaoVozesServico
    ) -> None:
        audio = servico.obter_audio_voz("VOICE_100_Homens_Adulto")
        assert audio is not None
        assert audio.exists()
        assert audio.suffix == ".wav"
        assert audio.stem == "VOICE_100_Homens_Adulto"

    def test_obter_audio_voz_inexistente(
        self, servico: CatalogacaoVozesServico
    ) -> None:
        assert servico.obter_audio_voz("NAO_EXISTE") is None

    def test_obter_audio_com_arquivo_wav_ausente(self, tmp_path: Path) -> None:
        dataset = tmp_path / "dataset"
        voz_dir = dataset / "Homens" / "Adulto" / "VOICE_SEM_WAV_Homens_Adulto"
        voz_dir.mkdir(parents=True)
        (voz_dir / "info.json").write_text(
            json.dumps({"id": "VOICE_SEM_WAV_Homens_Adulto", "prompt": "x"}),
            encoding="utf-8",
        )
        # sem o .wav
        servico = CatalogacaoVozesServico(dataset_path=dataset)
        assert servico.obter_audio_voz("VOICE_SEM_WAV_Homens_Adulto") is None


class TestRecarregarDataset:
    """Valida o reset do cache."""

    def test_recarregar_limpa_e_recarrega(
        self, servico: CatalogacaoVozesServico
    ) -> None:
        total_inicial = servico.contar_vozes()
        servico.recarregar_dataset()
        assert servico.contar_vozes() == total_inicial

    def test_recarregar_detecta_novas_vozes(self, dataset_tmp: Path) -> None:
        servico = CatalogacaoVozesServico(dataset_path=dataset_tmp)
        assert servico.contar_vozes() == 10
        # Adiciona nova voz ao dataset
        novo_dir = dataset_tmp / "Homens" / "Adulto" / "VOICE_999_Homens_Adulto"
        novo_dir.mkdir(parents=True)
        (novo_dir / "info.json").write_text(
            json.dumps({"id": "VOICE_999_Homens_Adulto", "prompt": "nova"}),
            encoding="utf-8",
        )
        (novo_dir / "VOICE_999_Homens_Adulto.wav").write_bytes(b"x")
        servico.recarregar_dataset()
        assert servico.contar_vozes() == 11
        assert servico.obter_info_voz("VOICE_999_Homens_Adulto") is not None


class TestMapeamento:
    """Garante que a constante de mapeamento nao regride silenciosamente."""

    def test_mapeamento_contem_todas_combinacoes_doc(self) -> None:
        esperado = {
            ("Male", "Child"),
            ("Female", "Child"),
            ("Male", "Adult"),
            ("Male", "Elderly"),
            ("Male", "Jovem"),
            ("Female", "Adult"),
            ("Female", "Elderly"),
            ("Female", "Jovem"),
        }
        assert set(MAPEAMENTO_CATEGORIA.keys()) == esperado

    def test_mapeamento_categorias_validas(self) -> None:
        grupos_validos = {"Criancas", "Homens", "Mulheres"}
        sub_validos = {"Menina", "Menino", "Adulto", "Idoso", "Jovem"}
        for categoria in MAPEAMENTO_CATEGORIA.values():
            grupo, sub = categoria.split("/", 1)
            assert grupo in grupos_validos
            assert sub in sub_validos
