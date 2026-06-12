"""Rotas FastAPI da aplicacao (HTML + API REST).

Exporta os routers para uso no ``app/main.py``::

    from app.routes import html, api, upload, config as config_routes

    app.include_router(html.router)
    app.include_router(api.router, prefix="/api")
    app.include_router(upload.router, prefix="/api")
    app.include_router(config_routes.router, prefix="/api")
"""

from app.routes import api, config, html, upload

__all__ = ["html", "api", "upload", "config"]
