"""Rotas HTML (Jinja2 + HTMX) para o Leitor.

Esta camada entrega paginas server-side renderizadas e partials HTMX
para interatividade sem recarregar a pagina. O foco principal da
task_14 eh a tela ``GET /livro/{id}/personagens`` e seus partials
associados.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app.repositories import session_scope
from app.repositories.livro_fala_repo import LivroFalaRepositorio
from app.repositories.livro_personagem_repo import LivroPersonagemRepositorio
from app.repositories.livro_repo import LivroRepositorio
from app.services.catalogacao_vozes import CatalogacaoVozesServico
from app.services.personagens import PersonagensService

logger = logging.getLogger(__name__)

# Templates Jinja2 apontando para app/templates/.
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter()


# =====================================================================
# Helpers de view
# =====================================================================

# Paleta de 8 cores distintas para a borda lateral de cada personagem.
_PALETA_CORES: tuple[str, ...] = (
    "#FF6B6B",
    "#4ECDC4",
    "#45B7D1",
    "#FFA07A",
    "#98D8C8",
    "#F7DC6F",
    "#BB8FCE",
    "#85C1E2",
)


def _cor_para_personagem(nome: str | None) -> str:
    """Gera cor deterministica (HSL) a partir do nome do personagem.

    Usa hash SHA-1 do nome normalizado (case-insensitive, stripped)
    para escolher um indice da paleta de 8 cores. O resultado eh
    estavel entre chamadas (mesmo nome -> mesma cor).

    Args:
        nome: Nome do personagem (pode ser None para personagens sem nome).

    Returns:
        String de cor hex no formato ``#RRGGBB``.
    """
    chave = (nome or "desconhecido").strip().lower()
    if not chave:
        chave = "desconhecido"
    digest = hashlib.sha1(chave.encode("utf-8")).hexdigest()
    indice = int(digest[:8], 16) % len(_PALETA_CORES)
    return _PALETA_CORES[indice]


def _enriquecer_personagens(personagens: list[Any]) -> list[dict[str, Any]]:
    """Converte uma lista de ``LivroPersonagem`` em dicts prontos para o template.

    Acrescenta:
      * ``cor`` — cor deterministica derivada do nome;
      * ``total_falas`` — contagem de falas (lazy, 1 query por personagem);
      * ``fl_eh_narrador`` — mantida como string ``"S"``/``"N"``/``None``.

    Args:
        personagens: Lista de instancias ``LivroPersonagem``.

    Returns:
        Lista de dicionarios serializaveis.
    """
    fala_repo_factory = session_scope
    resultado: list[dict[str, Any]] = []
    with fala_repo_factory() as session:
        fala_repo = LivroFalaRepositorio(session)
        for p in personagens:
            try:
                total = fala_repo.contar_por_personagem(p.cd_sequencial)
            except Exception:  # noqa: BLE001
                total = 0
            resultado.append(
                {
                    "cd_sequencial": p.cd_sequencial,
                    "tx_personagem": p.tx_personagem or "",
                    "fl_eh_narrador": p.fl_eh_narrador or "N",
                    "tx_genero": p.tx_genero,
                    "tx_idade": p.tx_idade,
                    "cor": _cor_para_personagem(p.tx_personagem),
                    "total_falas": total,
                }
            )
    return resultado


def _enriquecer_sugestoes(
    livro_id: int, personagens: list[Any]
) -> list[dict[str, Any]]:
    """Constroi a lista de sugestoes de unificacao (chave: id1/id2 + nome).

    Args:
        livro_id: ID do livro.
        personagens: Personagens atuais do livro (para resolver nomes).

    Returns:
        Lista de dicionarios com shape ``id1, nome1, id2, nome2, justificativa``.
    """
    servico = PersonagensService()
    pares = servico.gerar_sugestoes_unificacao(livro_id)
    indice_por_id: dict[int, Any] = {p.cd_sequencial: p for p in personagens}
    sugestoes: list[dict[str, Any]] = []
    for par in pares:
        p1 = indice_por_id.get(par["personagem1_id"])
        p2 = indice_por_id.get(par["personagem2_id"])
        if p1 is None or p2 is None:
            continue
        sugestoes.append(
            {
                "id1": p1.cd_sequencial,
                "id2": p2.cd_sequencial,
                "nome1": p1.tx_personagem or "",
                "nome2": p2.tx_personagem or "",
                "justificativa": par["justificativa"],
            }
        )
    return sugestoes


# =====================================================================
# Rotas
# =====================================================================

@router.get("/livro/{livro_id}/personagens", response_class=HTMLResponse)
def tela_revisao_personagens(
    request: Request,
    livro_id: int = PathParam(..., ge=1, description="ID do livro"),
) -> HTMLResponse:
    """Tela principal de revisao de personagens e falas (FL-02 etapa 2c).

    Carrega:
      * dados do livro (cabecalho);
      * lista de personagens com cores distintas;
      * contagem de falas por personagem;
      * sugestoes de unificacao (LLM + similaridade textual);
      * estado atual do pipeline (para o badge de status).
    """
    with session_scope() as session:
        livro_repo = LivroRepositorio(session)
        livro = livro_repo.buscar_por_id_sync(livro_id)
        if livro is None:
            raise HTTPException(status_code=404, detail=f"Livro {livro_id} nao encontrado")

        personagem_repo = LivroPersonagemRepositorio(session)
        personagens = personagem_repo.listar_por_livro(livro_id)

    personagens_view = _enriquecer_personagens(personagens)
    sugestoes = _enriquecer_sugestoes(livro_id, personagens)

    return templates.TemplateResponse(
        request,
        "livro_personagens.html",
        {
            "livro": livro,
            "personagens": personagens_view,
            "sugestoes": sugestoes,
        },
    )


@router.get(
    "/livro/{livro_id}/personagem/{personagem_id}/falas-partial",
    response_class=HTMLResponse,
)
def partial_falas_personagem(
    request: Request,
    livro_id: int = PathParam(..., ge=1),
    personagem_id: int = PathParam(..., ge=0, description="0 = estado vazio"),
) -> HTMLResponse:
    """Retorna partial HTMX com a lista de falas de um personagem.

    Quando ``personagem_id == 0``, retorna a mensagem padrao
    "Selecione um personagem...".

    Caso o personagem nao exista ou nao pertenca ao livro, retorna
    partial de erro amigavel.
    """
    if personagem_id == 0:
        return templates.TemplateResponse(
            request,
            "partials/_falas_personagem.html",
            {"personagem": None, "falas": []},
        )

    with session_scope() as session:
        personagem_repo = LivroPersonagemRepositorio(session)
        personagem = personagem_repo.buscar_por_id(personagem_id)
        if personagem is None or personagem.cd_sequenciallivro != livro_id:
            return templates.TemplateResponse(
                request,
                "partials/_falas_personagem.html",
                {"personagem": None, "falas": [], "erro": "Personagem nao encontrado"},
            )

        fala_repo = LivroFalaRepositorio(session)
        falas = fala_repo.listar_por_personagem(personagem_id)

    return templates.TemplateResponse(
        request,
        "partials/_falas_personagem.html",
        {
            "personagem": {
                "cd_sequencial": personagem.cd_sequencial,
                "tx_personagem": personagem.tx_personagem or "",
            },
            "falas": [
                {
                    "cd_sequencial": f.cd_sequencial,
                    "tx_fala": f.tx_fala or "",
                    "eh_narracao": f.eh_narracao or "N",
                    "nr_ordem": f.nr_ordem or 0,
                    "tx_instrucao_emocao": f.tx_instrucao_emocao,
                }
                for f in falas
            ],
        },
    )


@router.get(
    "/livro/{livro_id}/personagem/{personagem_id}/mesclar-form",
    response_class=HTMLResponse,
)
def partial_mesclar_form(
    request: Request,
    livro_id: int = PathParam(..., ge=1),
    personagem_id: int = PathParam(..., ge=1),
) -> HTMLResponse:
    """Retorna formulario de mesclagem (selecionar destino).

    O destino deve ser outro personagem do mesmo livro (excluindo o
    proprio personagem de origem).
    """
    with session_scope() as session:
        personagem_repo = LivroPersonagemRepositorio(session)
        origem = personagem_repo.buscar_por_id(personagem_id)
        if origem is None or origem.cd_sequenciallivro != livro_id:
            raise HTTPException(
                status_code=404,
                detail=f"Personagem {personagem_id} nao encontrado no livro {livro_id}",
            )
        candidatos = [
            p for p in personagem_repo.listar_por_livro(livro_id)
            if p.cd_sequencial != personagem_id
        ]
        livro_repo = LivroRepositorio(session)
        livro = livro_repo.buscar_por_id_sync(livro_id)

    return templates.TemplateResponse(
        request,
        "partials/_mesclar_form.html",
        {
            "livro": livro,
            "origem": {
                "cd_sequencial": origem.cd_sequencial,
                "tx_personagem": origem.tx_personagem or "",
            },
            "candidatos": [
                {
                    "cd_sequencial": p.cd_sequencial,
                    "tx_personagem": p.tx_personagem or "",
                }
                for p in candidatos
            ],
        },
    )


@router.get("/livro/{livro_id}/sugestoes-partial", response_class=HTMLResponse)
def partial_sugestoes(
    request: Request,
    livro_id: int = PathParam(..., ge=1),
) -> HTMLResponse:
    """Retorna partial HTMX com a lista de sugestoes de unificacao."""
    with session_scope() as session:
        personagem_repo = LivroPersonagemRepositorio(session)
        personagens = personagem_repo.listar_por_livro(livro_id)
    sugestoes = _enriquecer_sugestoes(livro_id, personagens)
    return templates.TemplateResponse(
        request,
        "partials/_sugestoes.html",
        {"sugestoes": sugestoes},
    )


@router.get("/api/livro/{livro_id}/status-partial", response_class=HTMLResponse)
def partial_status_analise(
    request: Request,
    livro_id: int = PathParam(..., ge=1),
) -> HTMLResponse:
    """Badge de status usado pelo polling HTMX (a cada 2s)."""
    with session_scope() as session:
        livro_repo = LivroRepositorio(session)
        livro = livro_repo.buscar_por_id_sync(livro_id)
        if livro is None:
            return templates.TemplateResponse(
                request,
                "partials/_status_analise.html",
                {"estado": "desconhecido", "percentual": 0, "mensagem": "Livro nao encontrado"},
            )

    progresso_total = max(livro.progresso_total or 1, 1)
    progresso_atual = min(max(livro.progresso_atual or 0, 0), progresso_total)
    percentual = int((progresso_atual / progresso_total) * 100)

    return templates.TemplateResponse(
        request,
        "partials/_status_analise.html",
        {
            "estado": livro.estado_pipeline,
            "percentual": percentual,
            "mensagem": livro.erro_mensagem,
            "fl_normalizado": livro.fl_normalizado or "N",
        },
    )


# =====================================================================
# Task 15: Tela de Configuracao de Vozes (FL-03)
# =====================================================================


def _enriquecer_personagens_para_vozes(
    personagens: list[Any],
    catalogacao: CatalogacaoVozesServico,
    limite_sugestoes: int = 5,
) -> list[dict[str, Any]]:
    """Anexa cor e sugestoes de voz a cada personagem para o template.

    Cada personagem recebe:
      * ``cor`` — cor deterministica baseada no id (paleta de 8 cores);
      * ``sugestoes`` — ate ``limite_sugestoes`` VozInfo convertidas em
        dicts com ``id, nome, categoria, prompt``.

    Personagens sem genero/idade definidos recebem lista vazia de
    sugestoes (o servico de catalogacao retorna [] nesse caso).
    """
    resultado: list[dict[str, Any]] = []
    for p in personagens:
        sugestoes = catalogacao.sugerir_vozes_por_personagem(
            genero=p.tx_genero or "",
            idade=p.tx_idade or "",
            limite=limite_sugestoes,
        )
        resultado.append(
            {
                "cd_sequencial": p.cd_sequencial,
                "tx_personagem": p.tx_personagem or "",
                "tx_genero": p.tx_genero,
                "tx_idade": p.tx_idade,
                "cd_voz": p.cd_voz,
                "tx_voz_origem": p.tx_voz_origem,
                "fl_voz_aprovada": p.fl_voz_aprovada,
                "fl_eh_narrador": p.fl_eh_narrador,
                "tx_instrucao_estilo": p.tx_instrucao_estilo,
                "tx_voz_referencia_path": p.tx_voz_referencia_path,
                "cor": _cor_para_personagem(p.tx_personagem),
                "sugestoes": [
                    {
                        "id": v.id,
                        "nome": v.nome,
                        "categoria": v.categoria,
                        "prompt": v.prompt,
                    }
                    for v in sugestoes
                ],
            }
        )
    return resultado


@router.get("/livro/{livro_id}/vozes", response_class=HTMLResponse)
def tela_configuracao_vozes(
    request: Request,
    livro_id: int = PathParam(..., ge=1, description="ID do livro"),
) -> HTMLResponse:
    """Tela principal de configuracao de vozes por personagem (FL-03).

    Carrega:
      * dados do livro (cabecalho);
      * lista de personagens do livro com cores distintas;
      * sugestoes automaticas do catalogo de vozes, filtradas por
        (genero, idade) de cada personagem.

    O servico ``CatalogacaoVozesServico`` e instanciado uma unica vez
    por requisicao para aproveitar o cache em memoria.
    """
    catalogacao = CatalogacaoVozesServico()

    with session_scope() as session:
        livro_repo = LivroRepositorio(session)
        livro = livro_repo.buscar_por_id_sync(livro_id)
        if livro is None:
            raise HTTPException(
                status_code=404, detail=f"Livro {livro_id} nao encontrado"
            )
        personagem_repo = LivroPersonagemRepositorio(session)
        personagens = personagem_repo.listar_por_livro(livro_id)

    personagens_view = _enriquecer_personagens_para_vozes(
        personagens, catalogacao
    )

    return templates.TemplateResponse(
        request,
        "livro_vozes.html",
        {
            "livro": livro,
            "personagens": personagens_view,
        },
    )


@router.get(
    "/api/vozes/{voice_id}/audio",
    summary="Serve o arquivo WAV de uma voz do catalogo",
    responses={404: {"description": "Voz ou audio nao encontrado"}},
)
def servir_audio_voz(voice_id: str) -> FileResponse:
    """Retorna o arquivo WAV de referencia de uma voz do catalogo.

    Usado pelos elementos ``<audio>`` no template de configuracao de
    vozes para reproduzir a amostra de cada sugestao.

    Args:
        voice_id: Identificador da voz (nome do diretorio leaf no dataset).

    Raises:
        HTTPException 404: Quando a voz nao existe no catalogo ou seu
            arquivo WAV nao esta presente no disco.
    """
    catalogacao = CatalogacaoVozesServico()
    audio_path = catalogacao.obter_audio_voz(voice_id)
    if audio_path is None or not audio_path.exists():
        logger.warning("Audio nao encontrado para voz '%s'", voice_id)
        raise HTTPException(
            status_code=404,
            detail=f"Arquivo de audio nao encontrado para a voz '{voice_id}'",
        )
    return FileResponse(audio_path, media_type="audio/wav")
