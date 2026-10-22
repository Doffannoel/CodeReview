from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://codereviewer:codereviewer@localhost/codereviewer"
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "codereviewer"
    database_username: str = "codereviewer"
    database_password: SecretStr = SecretStr("")
    database_password_file: str = ""

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("codereviewer-password")
    neo4j_password_file: str = ""

    github_app_id: str = ""
    github_private_key_base64: SecretStr = SecretStr("")
    github_private_key_base64_file: str = ""
    github_webhook_secret: SecretStr = SecretStr("")
    github_webhook_secret_file: str = ""
    github_api_url: str = "https://api.github.com"

    llm_provider: str = "gemini"
    llm_model: str = "gemini-3.8-flash"
    llm_fallback_model: str = "gemini-3.7-flash"
    llm_api_key: SecretStr = SecretStr("")
    llm_api_key_file: str = ""
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    llm_max_retries: int = Field(default=4, ge=0, le=10)
    llm_retry_base_seconds: float = Field(default=1.0, ge=0.1, le=60)

    retrieval_mode: str = "graph"
    graph_max_hops: int = Field(default=2, ge=1, le=2)
    graph_max_nodes_per_symbol: int = Field(default=12, ge=1, le=100)
    context_token_budget: int = Field(default=10_000, ge=1_000)
    max_repository_files: int = Field(default=500, ge=1)
    max_source_file_bytes: int = Field(default=250_000, ge=1_000)
    max_changed_files: int = Field(default=30, ge=1)
    max_changed_lines: int = Field(default=2_000, ge=1)
    max_findings: int = Field(default=10, ge=1, le=50)
    worker_poll_seconds: float = Field(default=2.0, ge=0.1)
    worker_max_attempts: int = Field(default=3, ge=1, le=10)
    worker_job_timeout_seconds: float = Field(default=90, ge=10, le=3600)
    dry_run: bool = False

    @field_validator("retrieval_mode")
    @classmethod
    def require_graph_mode(cls, value: str) -> str:
        if value != "graph":
            raise ValueError("graphmethod supports RETRIEVAL_MODE=graph only")
        return value

    @property
    def github_private_key(self) -> str:
        encoded = self.github_private_key_base64_value
        if not encoded:
            return ""
        return base64.b64decode(encoded).decode("utf-8")

    @staticmethod
    def _secret_value(value: SecretStr, file_path: str) -> str:
        if file_path:
            return Path(file_path).read_text(encoding="utf-8").strip()
        return value.get_secret_value().strip()

    @property
    def database_password_value(self) -> str:
        return self._secret_value(self.database_password, self.database_password_file)

    @property
    def neo4j_password_value(self) -> str:
        return self._secret_value(self.neo4j_password, self.neo4j_password_file)

    @property
    def github_private_key_base64_value(self) -> str:
        return self._secret_value(
            self.github_private_key_base64,
            self.github_private_key_base64_file,
        )

    @property
    def github_webhook_secret_value(self) -> str:
        return self._secret_value(self.github_webhook_secret, self.github_webhook_secret_file)

    @property
    def llm_api_key_value(self) -> str:
        return self._secret_value(self.llm_api_key, self.llm_api_key_file)


@lru_cache
def get_settings() -> Settings:
    return Settings()
