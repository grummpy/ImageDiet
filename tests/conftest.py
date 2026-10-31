"""Isolation for the suite: no hub fetch, reports and work dirs stay in tmp."""

from __future__ import annotations

import pytest

from imagediet.config import Settings
from imagediet.samplegen import build_site


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEDIET_OFFLINE", "1")
    monkeypatch.setenv("IMAGEDIET_DISABLE_HUB", "1")
    monkeypatch.setenv("IMAGEDIET_REPORTS", str(tmp_path / "reports"))
    monkeypatch.setenv("IMAGEDIET_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("IMAGEDIET_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("IMAGEDIET_INVENTORY_FILE", raising=False)
    monkeypatch.delenv("IMAGEDIET_SITES_FILE", raising=False)
    monkeypatch.delenv("IMAGEDIET_SAMPLE_ROOT", raising=False)


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def synthetic_site(tmp_path):
    return build_site(tmp_path / "site")
