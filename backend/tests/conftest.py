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
