"""Aplicacao FastAPI principal - factory.

Configura o app com:
- Jinja2 templates e arquivos estaticos
- CORS para dev
- Healthcheck verificando PostgreSQL, Redis, LLM e TTS
- Middleware de request logging
- Rotas de templates HTML e endpoints REST
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import Settings, get_settings
from app.repositories.database import engine

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Cria e configura a instancia do FastAPI."""
    settings = get_settings()

    app = FastAPI(
        title="Leitor",
        description="Plataforma de producao de audiolivros multi-voz",
        version="0.1.0",
        debug=settings.app_debug,
    )

    # Garante que os diretorios existam antes de montar rotas estaticas
    Path(settings.upload_path).mkdir(parents=True, exist_ok=True)
    Path(settings.audio_output_path).mkdir(parents=True, exist_ok=True)
    Path(settings.storage_path).mkdir(parents=True, exist_ok=True)

    # Diretorios da propria aplicacao
    app_dir = Path(__file__).resolve().parent
    templates_dir = app_dir / "templates"
    static_dir = app_dir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    # CORS (dev) - permite qualquer origem/metodo/header
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Middleware de request logging
    @app.middleware("http")
    async def log_requests(request: Request, call_next: Any) -> Any:
        """Loga inicio, fim e duracao de cada requisicao."""
        inicio = time.perf_counter()
        logger.info(
            "request start method=%s path=%s",
            request.method,
            request.url.path,
        )
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "request error method=%s path=%s err=%s",
                request.method,
                request.url.path,
                exc,
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Erro interno do servidor"},
            )
        duracao_ms = (time.perf_counter() - inicio) * 1000
        logger.info(
            "request end method=%s path=%s status=%s duracao_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            duracao_ms,
        )
        return response

    # Arquivos estaticos (CSS/JS proprios)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Rotas
    from app.routes import api as api_routes
    from app.routes import config as config_routes
    from app.routes import html as html_routes
    from app.routes import upload as upload_routes

    app.include_router(html_routes.router)
    app.include_router(api_routes.router)
    app.include_router(upload_routes.router)
    app.include_router(config_routes.router)

    # Healthcheck detalhado
    @app.get("/health", tags=["health"])
    def health(settings_dep: Settings = None) -> dict[str, Any]:  # type: ignore[assignment]
        """Healthcheck verificando PostgreSQL, Redis, LLM e TTS."""
        result: dict[str, Any] = {
            "status": "ok",
            "service": "leitor",
            "version": "0.1.0",
            "checks": {
                "postgres": _check_postgres(),
                "redis": _check_redis(settings),
                "llm": _check_http(settings.llm_base_url, timeout=5.0),
                "tts": _check_http(settings.tts_base_url, timeout=5.0),
            },
        }
        # Se algum check falhou, marca status como degraded
        if any(v == "fail" for v in result["checks"].values()):
            result["status"] = "degraded"
        return result

    return app


def _check_postgres() -> str:
    """Verifica conectividade com o PostgreSQL via SELECT 1."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("PostgreSQL healthcheck falhou: %s", exc)
        return "fail"


def _check_redis(settings: Settings) -> str:
    """Verifica conectividade com o Redis (broker Celery)."""
    try:
        import redis  # type: ignore[import-not-found]

        cliente = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        cliente.ping()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis healthcheck falhou: %s", exc)
        return "fail"


def _check_http(url: str, timeout: float = 5.0) -> str:
    """Verifica se um servico HTTP esta acessivel."""
    try:
        import httpx  # type: ignore[import-not-found]

        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
        # 2xx, 3xx e 4xx sao considerados "servico acessivel".
        # 5xx ou erro de conexao sao "fail".
        if resp.status_code < 500:
            return "ok"
        return "fail"
    except Exception as exc:  # noqa: BLE001
        logger.warning("HTTP healthcheck falhou para %s: %s", url, exc)
        return "fail"


# Instancia exposta para `uvicorn app.main:app`
app = create_app()
