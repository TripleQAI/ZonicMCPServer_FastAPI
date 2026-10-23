"""Shared test setup: settings with their defaults, never a developer's local .env.

Modules read settings through ``app.config.get_settings()`` at call time, so patching
that one function gives every test the same defaults. A test that changes a value
uses ``monkeypatch.setattr(get_settings(), ...)``, which is undone afterwards.
"""
from __future__ import annotations

import pytest

import app.config
from app.config import Settings


@pytest.fixture(autouse=True)
def default_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(_env_file=None)
    monkeypatch.setattr(app.config, 'get_settings', lambda: settings)
    return settings
