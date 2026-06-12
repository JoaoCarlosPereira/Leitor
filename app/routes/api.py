"""Endpoints JSON da API REST (fila, download de audiolivro, etc.).

Os endpoints retornam ``application/json`` e nao dependem de templates.
A camada HTMX da UI consome estas rotas para acoes como reordenar,
remover, pausar e retomar livros na fila, e para servir o arquivo de
audio final.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.dependencies import get_livro_repo
from app.repositories import LivroRepositorio

logger = logging.getLogger(__name__)

router = APIRouter(tags=["api"])


# -----------------------------------------------------------------------------
# Download do audiolivro
# -----------------------------------------------------------------------------


def _resolver_caminho_audio(caminho_str: str) -> Path:
    """Resolve o caminho absoluto do audio final.

    Aceita caminhos absolutos ou relativos a ``audio_output_path``.
    """
    caminho = Path(caminho_str)
    if caminho.is_absolute():
        return caminho
    from app.config import get_settings

    return get_settings().audio_output_path / caminho


@router.get("/livro/{livro_id}/download")
def download_livro(
    livro_id: int,
    request: Request,
    download: str | None = Query(default=None, alias="download"),
    livro_repo: LivroRepositorio = Depends(get_livro_repo),
) -> object:
    """Serve o arquivo de audio final do livro.

    Query string:
      - ``download=true``: dispara download (Content-Disposition: attachment).
      - default: serve inline para o player HTML5.
    """
    logger.info("GET /api/livro/%s/download download=%s", livro_id, download)
    livro = livro_repo.buscar_por_id_sync(livro_id)
    if livro is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Livro id={livro_id} nao encontrado",
        )
    if livro.estado_pipeline != "concluido":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Livro id={livro_id} nao esta concluido "
                f"(estado='{livro.estado_pipeline}')"
            ),
        )
    if not livro.caminho_audio_final:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Caminho do audio final nao registrado para este livro",
        )

    caminho = _resolver_caminho_audio(livro.caminho_audio_final)
    if not caminho.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Arquivo de audio nao encontrado em disco: {caminho}",
        )

    media_type, _ = mimetypes.guess_type(caminho.name)
    if media_type is None:
        media_type = "application/octet-stream"

    disposition = "attachment" if (download or "").lower() == "true" else "inline"
    filename = f"{livro.tx_titulo or f'livro_{livro_id}'}{caminho.suffix}"

    return FileResponse(
        path=str(caminho),
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


# -----------------------------------------------------------------------------
# Fila de producao (JSON)
# -----------------------------------------------------------------------------


@router.get("/fila")
def listar_fila() -> JSONResponse:
    """Lista os livros atualmente na fila de producao (FIFO)."""
    from tasks.fila import listar_fila as listar_fila_task

    try:
        livros = listar_fila_task()
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao listar fila via API")
        livros = []
    return JSONResponse(content={"fila": livros, "total": len(livros)})


@router.put("/fila/{livro_id}/reordenar")
def reordenar_fila(
    livro_id: int,
    payload: dict,
    request: Request,
) -> HTMLResponse:
    """Move um livro para nova posicao na fila.

    Aceita JSON ``{"nova_posicao": int}`` ou form-encoded (HTMX).
    Retorna a partial atualizada do item para o swap do HTMX.
    """
    from tasks.fila import reordenar_fila as reordenar_fila_task

    nova_posicao = payload.get("nova_posicao")
    if nova_posicao is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campo 'nova_posicao' e obrigatorio",
        )
    try:
        nova_posicao = int(nova_posicao)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'nova_posicao' invalido: {exc}",
        ) from exc

    logger.info(
        "PUT /api/fila/%s/reordenar nova_posicao=%s", livro_id, nova_posicao
    )
    try:
        reordenar_fila_task(livro_id, nova_posicao)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    # Recarrega estado do livro e retorna partial
    from app.routes.html import templates
    from app.dependencies import get_livro_repo
    from app.repositories.database import get_session

    sess = next(iter([get_session()]))
    try:
        repo = LivroRepositorio(sess)
        livro = repo.buscar_por_id_sync(livro_id)
    finally:
        sess.close()
    if livro is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Livro id={livro_id} nao encontrado",
        )
    return templates.TemplateResponse(
        "partials/fila/_item_fila.html",
        {"request": request, "livro": livro},
    )


@router.delete("/fila/{livro_id}")
def remover_da_fila(livro_id: int) -> JSONResponse:
    """Remove um livro da fila (nao exclui o registro do livro)."""
    from tasks.fila import remover_da_fila as remover_da_fila_task

    logger.info("DELETE /api/fila/%s", livro_id)
    try:
        remover_da_fila_task(livro_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return JSONResponse(content={"ok": True, "livro_id": livro_id})


@router.put("/livro/{livro_id}/pausar-fila")
def pausar_livro_fila(
    livro_id: int,
    request: Request,
) -> HTMLResponse:
    """Pausa a producao de um livro na fila."""
    from tasks.fila import pausar_livro as pausar_livro_task

    logger.info("PUT /api/livro/%s/pausar-fila", livro_id)
    try:
        pausar_livro_task(livro_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    from app.routes.html import templates
    from app.repositories.database import get_session

    sess = next(iter([get_session()]))
    try:
        repo = LivroRepositorio(sess)
        livro = repo.buscar_por_id_sync(livro_id)
    finally:
        sess.close()
    if livro is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Livro id={livro_id} nao encontrado",
        )
    return templates.TemplateResponse(
        "partials/fila/_item_fila.html",
        {"request": request, "livro": livro},
    )


@router.put("/livro/{livro_id}/retomar-fila")
def retomar_livro_fila(
    livro_id: int,
    request: Request,
) -> HTMLResponse:
    """Retoma a producao de um livro pausado."""
    from tasks.fila import retomar_livro as retomar_livro_task

    logger.info("PUT /api/livro/%s/retomar-fila", livro_id)
    try:
        retomar_livro_task(livro_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    from app.routes.html import templates
    from app.repositories.database import get_session

    sess = next(iter([get_session()]))
    try:
        repo = LivroRepositorio(sess)
        livro = repo.buscar_por_id_sync(livro_id)
    finally:
        sess.close()
    if livro is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Livro id={livro_id} nao encontrado",
        )
    return templates.TemplateResponse(
        "partials/fila/_item_fila.html",
        {"request": request, "livro": livro},
    )
