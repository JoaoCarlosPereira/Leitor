"""Endpoints REST para configuracao de chaves de API (TB_LIVROAPIS)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import get_livro_api_repo
from app.repositories import LivroAPIRepositorio
from app.repositories.models.livro_api import LivroAPI

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])


class APIKeyRequest(BaseModel):
    """Body de POST /api/config/apis."""

    tx_key: str = Field(..., min_length=1)
    tx_nome: str = Field(..., min_length=1, max_length=200)
    tx_servico: str = Field(..., min_length=1, max_length=100)
    dt_expiracao: str | None = Field(
        default=None,
        description="Data ISO 8601 (opcional)",
    )


def _serializar(api: LivroAPI) -> dict[str, Any]:
    """Serializa ``LivroAPI`` para JSON, omitindo o valor da chave por seguranca."""
    return {
        "id": api.cd_sequencial,
        "nome": api.tx_nome,
        "servico": api.tx_servico,
        "ativo": api.fl_ativo,
        "dt_expiracao": (
            api.dt_expiracao.isoformat() if api.dt_expiracao else None
        ),
        "dt_manutencao": (
            api.dt_manutencao.isoformat()
            if getattr(api, "dt_manutencao", None)
            else None
        ),
    }


@router.get(
    "/apis",
    summary="Lista chaves de API cadastradas",
)
def listar_apis(
    api_repo: LivroAPIRepositorio = Depends(get_livro_api_repo),
) -> dict[str, Any]:
    """Retorna todas as chaves de API ativas (omite o valor da chave)."""
    logger.info("GET /api/config/apis")
    chaves = api_repo.listar(apenas_ativas=True)
    return {
        "chaves": [_serializar(c) for c in chaves],
        "total": len(chaves),
    }


@router.post(
    "/apis",
    status_code=status.HTTP_201_CREATED,
    summary="Cadastra nova chave de API",
)
def criar_api(
    payload: APIKeyRequest,
    api_repo: LivroAPIRepositorio = Depends(get_livro_api_repo),
) -> dict[str, Any]:
    """Cria e persiste uma nova chave de API."""
    logger.info(
        "POST /api/config/apis - servico='%s' nome='%s'",
        payload.tx_servico,
        payload.tx_nome,
    )
    from datetime import datetime

    dt_exp: datetime | None = None
    if payload.dt_expiracao:
        try:
            dt_exp = datetime.fromisoformat(payload.dt_expiracao)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"dt_expiracao invalida: {payload.dt_expiracao}",
            ) from exc

    try:
        api = api_repo.salvar_chave(
            tx_key=payload.tx_key,
            tx_nome=payload.tx_nome,
            tx_servico=payload.tx_servico,
            dt_expiracao=dt_exp,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro ao salvar chave de API")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao salvar chave: {exc}",
        ) from exc

    return _serializar(api)
