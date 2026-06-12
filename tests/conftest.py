"""Fixtures compartilhadas para os testes."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import BigInteger, Integer, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.repositories.database import Base
from app.repositories.models import (
    EstadoPipeline,
    LivroAPI,
    LivroCabecalho,
    LivroFala,
    LivroPagina,
    LivroPersonagem,
)


@pytest.fixture
def engine() -> Generator[Engine, None, None]:
    """Engine SQLite em memoria com schema criado e tipos de ID adaptados.

    SQLite nao suporta BigInteger com autoincrement — reescrevemos os tipos
    das colunas PK para Integer antes de criar as tabelas, garantindo que
    AUTOINCREMENT funcione corretamente em testes.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )

    # Ajusta tipos de PK para Integer (SQLite nao tem BigInteger autoincrement)
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if col.primary_key and isinstance(col.type, BigInteger):
                col.type = Integer()

    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Generator[Session, None, None]:
    """Sessao SQLAlchemy para um teste, com cleanup no fim."""
    Sess = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    s = Sess()
    try:
        yield s
    finally:
        s.close()


# ----------------------------- Factories ----------------------------------

def make_livro(**kwargs) -> LivroCabecalho:
    """Cria um LivroCabecalho com valores padrao."""
    import uuid
    defaults = {
        "tx_titulo": f"Livro Teste {uuid.uuid4().hex[:8]}",
        "fl_lido": "N",
        "fl_normalizado": "N",
        "fl_narrador": "N",
        "fl_produzido": "N",
        "estado_pipeline": EstadoPipeline.AGUARDANDO.value,
        "progresso_atual": 0,
        "progresso_total": 6,
        "autor": "Autor Teste",
    }
    defaults.update(kwargs)
    return LivroCabecalho(**defaults)


def make_pagina(livro_id: int, nr: int = 1, **kwargs) -> LivroPagina:
    """Cria uma LivroPagina pertencente ao livro_id."""
    defaults = {
        "cd_sequenciallivro": livro_id,
        "nr_pagina": nr,
        "tx_pagina": f"Texto da pagina {nr}",
        "fl_processado": "N",
    }
    defaults.update(kwargs)
    return LivroPagina(**defaults)


def make_personagem(livro_id: int, nome: str = "Personagem", **kwargs) -> LivroPersonagem:
    """Cria um LivroPersonagem pertencente ao livro_id."""
    defaults = {
        "cd_sequenciallivro": livro_id,
        "tx_personagem": nome,
        "tx_genero": "Female",
        "tx_idade": "Adult",
    }
    defaults.update(kwargs)
    return LivroPersonagem(**defaults)


def make_fala(
    livro_id: int,
    pagina_id: int,
    personagem_id: int,
    texto: str = "Fala teste",
    **kwargs,
) -> LivroFala:
    """Cria uma LivroFala."""
    defaults = {
        "cd_sequenciallivro": livro_id,
        "cd_sequencialpagina": pagina_id,
        "cd_sequencialpersonagem": personagem_id,
        "tx_fala": texto,
        "fl_processado": "N",
        "nr_ordem": 0,
    }
    defaults.update(kwargs)
    return LivroFala(**defaults)


def make_api(**kwargs) -> LivroAPI:
    """Cria uma LivroAPI com valores padrao."""
    from datetime import datetime, timedelta

    defaults = {
        "tx_key": "key-default",
        "tx_nome": "API Teste",
        "tx_servico": "llm",
        "fl_ativo": "S",
        "dt_expiracao": datetime.utcnow() + timedelta(days=30),
    }
    defaults.update(kwargs)
    return LivroAPI(**defaults)
