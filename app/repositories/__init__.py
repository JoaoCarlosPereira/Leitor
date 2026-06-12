"""Repositórios de acesso ao banco de dados."""

from app.repositories.database import session_scope
from app.repositories.livro_api_repo import LivroAPIRepositorio
from app.repositories.livro_fala_repo import LivroFalaRepositorio
from app.repositories.livro_pagina_repo import LivroPaginaRepositorio
from app.repositories.livro_personagem_repo import LivroPersonagemRepositorio
from app.repositories.livro_repo import LivroRepositorio

__all__ = [
    "session_scope",
    "LivroAPIRepositorio",
    "LivroFalaRepositorio",
    "LivroPaginaRepositorio",
    "LivroPersonagemRepositorio",
    "LivroRepositorio",
]
