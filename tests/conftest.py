import pytest


@pytest.fixture(autouse=True)
def _no_backup_providers(monkeypatch):
    """Tests must not depend on the developer's .env: no backup LLM providers unless a test adds one."""
    from server import config
    monkeypatch.setattr(config, "LLM_BACKUPS", [])
