from __future__ import annotations

import base64

from codereviewer.config import Settings
from codereviewer.db import Database


def test_default_model_uses_gemini_3_8_flash() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_model == "gemini-3.8-flash"
    assert settings.llm_fallback_model == "gemini-3.7-flash"


def test_settings_read_runtime_secrets_from_files(tmp_path):
    webhook_file = tmp_path / "webhook_secret"
    webhook_file.write_text("from-file\n", encoding="utf-8")
    key_file = tmp_path / "private_key_base64"
    key_file.write_text(
        base64.b64encode(b"test-private-key").decode("ascii"),
        encoding="utf-8",
    )

    settings = Settings(
        github_webhook_secret="from-environment",
        github_webhook_secret_file=str(webhook_file),
        github_private_key_base64_file=str(key_file),
    )

    assert settings.github_webhook_secret_value == "from-file"
    assert settings.github_private_key == "test-private-key"


def test_database_password_file_supports_url_reserved_characters(tmp_path):
    password_file = tmp_path / "postgres_password"
    password_file.write_text("p@ss:/word", encoding="utf-8")
    settings = Settings(
        database_host="postgres",
        database_password_file=str(password_file),
    )

    database = Database(settings)

    assert database.engine.url.host == "postgres"
    assert database.engine.url.password == "p@ss:/word"
