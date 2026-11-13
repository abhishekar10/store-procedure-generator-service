"""Shared pytest fixtures for the fast, offline unit test suite."""

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """Settings are process-cached, so a test that changes the environment
    must not leak that into the next test. Clearing on both sides is cheap."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def isolated_app_db(tmp_path, monkeypatch):
    """Redirects the app database to a throwaway temp file for the duration of
    a test, so store tests never touch real data.

    Configuration now comes from the environment rather than module-level
    constants, so this overrides the env vars and rebuilds the cached Settings
    -- the same path production uses.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.db"))
    get_settings.cache_clear()
    yield tmp_path / "app.db"
