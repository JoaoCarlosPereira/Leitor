"""Configuração central da aplicação via variáveis de ambiente."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações carregadas de variáveis de ambiente / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+psycopg2://leitor:leitor@localhost:5432/leitor"
    db_user: str = "leitor"
    db_password: str = "leitor"
    db_name: str = "leitor"
    db_host: str = "localhost"
    db_port: int = 5432

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = (
        "db+postgresql://leitor:leitor@localhost:5432/leitor"
    )

    # LLM Local
    llm_base_url: str = "http://192.168.2.112:8000/v1/"
    llm_api_key: str = "local"
    llm_model: str = "qwen3.6-35b"
    llm_timeout: int = 60
    llm_max_retries: int = 3

    # TTS Local
    tts_base_url: str = "http://192.168.2.112:8881"
    tts_timeout: int = 120
    tts_max_retries: int = 3

    # Paths
    dataset_path: Path = Path("./dataset")
    storage_path: Path = Path("./storage")
    upload_path: Path = Path("./uploads")
    audio_output_path: Path = Path("./output")

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Retorna instância singleton de Settings."""
    return Settings()
