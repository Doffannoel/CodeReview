from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Penalties(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    critical: int = Field(default=25, ge=1, le=100)
    high: int = Field(default=10, ge=1, le=100)
    medium: int = Field(default=5, ge=1, le=100)
    low: int = Field(default=1, ge=1, le=100)


class QualityGate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    threshold: int = Field(default=80, ge=0, le=100)
    penalties: Penalties = Field(default_factory=Penalties)


class RepoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ignore_paths: list[str] = Field(default_factory=list, max_length=100)
    min_confidence: float = Field(default=0.8, ge=0.8, le=1)
    max_findings: int = Field(default=10, ge=1, le=50)
    review_language: Literal["id", "en"] = "id"
    quality_gate: QualityGate = Field(default_factory=QualityGate)

    @field_validator("ignore_paths")
    @classmethod
    def validate_patterns(cls, patterns: list[str]) -> list[str]:
        if any(
            not p or len(p) > 256 or p.startswith("/") or ".." in p.split("/") or "\\" in p
            for p in patterns
        ):
            raise ValueError("ignore_paths must contain relative POSIX glob patterns")
        return patterns

    def ignores(self, path: str) -> bool:
        return any(
            fnmatchcase(path, pattern)
            or (pattern.startswith("**/") and fnmatchcase(path, pattern[3:]))
            for pattern in self.ignore_paths
        )


def parse_repo_config(content: str | None) -> RepoConfig:
    if content is None:
        return RepoConfig()
    if len(content.encode("utf-8")) > 32_768:
        raise ValueError(".graphmethod.yml exceeds 32 KiB")
    try:
        value = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError("Invalid .graphmethod.yml YAML") from exc
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError(".graphmethod.yml must be a mapping")
    return RepoConfig.model_validate(value)
