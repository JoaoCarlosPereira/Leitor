"""Helpers de injecao de dependencia para o FastAPI.

Centraliza a criacao dos repositorios a partir de uma sessao do
SQLAlchemy, expondo funcoes `Depends(...)` reutilizaveis nas rotas.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.repositories import (
    LivroAPIRepositorio,
    LivroFalaRepositorio,
    LivroPaginaRepositorio,
    LivroPersonagemRepositorio,
    LivroRepositorio,
)
from app.repositories.database import get_session


def get_livro_repo(session: Session = Depends(get_session)) -> LivroRepositorio:
    """Retorna um ``LivroRepositorio`` ligado a sessao do request."""
    return LivroRepositorio(session)


def get_livro_pagina_repo(
    session: Session = Depends(get_session),
) -> LivroPaginaRepositorio:
    """Retorna um ``LivroPaginaRepositorio`` ligado a sessao do request."""
    return LivroPaginaRepositorio(session)


def get_livro_personagem_repo(
    session: Session = Depends(get_session),
) -> LivroPersonagemRepositorio:
    """Retorna um ``LivroPersonagemRepositorio`` ligado a sessao do request."""
    return LivroPersonagemRepositorio(session)


def get_livro_fala_repo(
    session: Session = Depends(get_session),
) -> LivroFalaRepositorio:
    """Retorna um ``LivroFalaRepositorio`` ligado a sessao do request."""
    return LivroFalaRepositorio(session)


def get_livro_api_repo(
    session: Session = Depends(get_session),
) -> LivroAPIRepositorio:
    """Retorna um ``LivroAPIRepositorio`` ligado a sessao do request."""
    return LivroAPIRepositorio(session)


__all__ = [
    "get_livro_repo",
    "get_livro_pagina_repo",
    "get_livro_personagem_repo",
    "get_livro_fala_repo",
    "get_livro_api_repo",
]
