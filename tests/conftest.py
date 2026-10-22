import pytest

from codereviewer.config import Settings


@pytest.fixture(autouse=True)
def ignore_local_secrets(monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
