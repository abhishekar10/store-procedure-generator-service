"""Shared pytest fixtures for the fast, offline unit test suite."""

import pytest

from app import config


@pytest.fixture
def isolated_app_db(tmp_path, monkeypatch):
    """Redirects backend/data/app.db to a throwaway temp file for the
    duration of a test, so conversation_store/telemetry_store tests never
    touch real data."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "APP_DB_PATH", tmp_path / "app.db")
    yield tmp_path / "app.db"
