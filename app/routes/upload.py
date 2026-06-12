"""Endpoints de upload binario de arquivos.

O endpoint principal (``POST /api/livro/upload/file``) recebe o PDF
via ``multipart/form-data``, salva em ``upload_path``, cria o registro
de ``LivroCabecalho`` e dispara o pipeline Celery (se a task estiver
disponivel).
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.config import get_settings
from app.dependencies import get_livro_repo
from app.repositories import LivroRepositorio
from app.repositories.database import session_scope
from app.repositories.models.livro_cabecalho import EstadoPipeline, LivroCabecalho

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])


# Limite de tamanho do PDF (50 MB por padrao).
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _importar_tarefa_pipeline() -> Any:
    """Importa lazy a task ``executar_pipeline_task`` do Celery."""
    try:
        from tasks.pipeline import executar_pipeline_task  # type: ignore[import-not-found]

        return executar_pipeline_task
    except Exception as exc:  # noqa: BLE001
        logger.warning("Modulo tasks.pipeline indisponivel: %s", exc)
        return None


def _importar_enfileirar() -> Any:
    """Importa lazy a funcao ``enfileirar_livro`` do Celery."""
    try:
        from tasks.fila import enfileirar_livro  # type: ignore[import-not-found]

        return enfileirar_livro
    except Exception as exc:  # noqa: BLE001
        logger.warning("tasks.fila.enfileirar_livro indisponivel: %s", exc)
        return None


@router.post(
    "/livro/upload/file",
    status_code=status.HTTP_201_CREATED,
    summary="Recebe PDF (multipart) e inicia o pipeline",
)
async def upload_livro(
    titulo: str = Form(..., min_length=1, max_length=500),
    autor: str = Form(..., min_length=1, max_length=500),
    arquivo: UploadFile = File(...),
    livro_repo: LivroRepositorio = Depends(get_livro_repo),
) -> dict[str, Any]:
    """Recebe o PDF, salva, cria o livro e dispara o pipeline.

    Args:
        titulo: Titulo do livro (Form).
        autor: Autor do livro (Form).
        arquivo: Arquivo PDF (File, multipart).

    Returns:
        Dict com ``livro_id``, ``status`` e ``caminho_pdf``.
    """
    logger.info(
        "POST /api/livro/upload/file - titulo='%s' autor='%s' arquivo='%s'",
        titulo,
        autor,
        arquivo.filename,
    )

    # Validacao de extensao
    filename = arquivo.filename or "upload.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Apenas arquivos PDF sao aceitos",
        )

    settings = get_settings()
    upload_dir = Path(settings.upload_path)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Gera nome de arquivo unico para evitar colisao
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_titulo = "".join(
        c if c.isalnum() or c in ("-", "_") else "_" for c in titulo[:40]
    ).strip("_") or "livro"
    unique_id = uuid.uuid4().hex[:8]
    destino = upload_dir / f"{timestamp}_{safe_titulo}_{unique_id}.pdf"

    # Salva o arquivo em chunks para nao carregar tudo em memoria
    bytes_escritos = 0
    try:
        with destino.open("wb") as saida:
            while True:
                chunk = await arquivo.read(1024 * 1024)  # 1 MB
                if not chunk:
                    break
                bytes_escritos += len(chunk)
                if bytes_escritos > MAX_UPLOAD_BYTES:
                    saida.close()
                    destino.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            f"Arquivo excede o limite de {MAX_UPLOAD_BYTES} bytes"
                        ),
                    )
                saida.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro ao salvar PDF enviado")
        destino.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao salvar arquivo: {exc}",
        ) from exc
    finally:
        await arquivo.close()

    if bytes_escritos == 0:
        destino.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Arquivo vazio",
        )

    # Cria o registro do livro
    try:
        livro = LivroCabecalho(
            tx_titulo=titulo.strip(),
            autor=autor.strip(),
            caminho_pdf=str(destino),
            estado_pipeline=EstadoPipeline.AGUARDANDO.value,
            progresso_atual=0,
            progresso_total=6,
        )
        with session_scope() as session:
            repo = LivroRepositorio(session)
            novo = repo.salvar(livro)
            livro_id = novo.cd_sequencial
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro ao criar livro apos upload")
        destino.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao registrar livro: {exc}",
        ) from exc

    # Enfileira (task opcional)
    enfileirar = _importar_enfileirar()
    if enfileirar is not None:
        try:
            enfileirar(livro_id)
        except Exception:
            logger.exception("Falha ao enfileirar livro id=%s", livro_id)

    # Dispara o pipeline (task opcional)
    pipeline_task = _importar_tarefa_pipeline()
    if pipeline_task is not None:
        try:
            pipeline_task.delay(livro_id)
        except Exception:
            logger.exception(
                "Falha ao despachar pipeline task para livro id=%s", livro_id
            )

    return {
        "livro_id": livro_id,
        "titulo": titulo,
        "autor": autor,
        "status": EstadoPipeline.AGUARDANDO.value,
        "caminho_pdf": str(destino),
        "tamanho_bytes": bytes_escritos,
    }
