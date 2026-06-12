"""Testes de smoke para as rotas FastAPI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Cliente de teste FastAPI com todas as dependencias externas mockadas."""
    with patch("app.config.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            database_url="sqlite:///:memory:",
            tts_base_url="http://tts.local:9999",
            tts_timeout=10,
            tts_max_retries=1,
            upload_path="/tmp/uploads",
            audio_output_path=MagicMock(__truediv__=lambda *a, **k: MagicMock()),
        )
        from app.main import create_app

        app = create_app()
        return TestClient(app)


class TestHealthEndpoint:
    def test_health_retorna_200(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "service" in data
        # Aceita 'ok' ou 'degraded' (sem dependencias reais, fica degraded)
