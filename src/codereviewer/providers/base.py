from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field


class ConflictDetail(BaseModel):
    target: str = Field(
        description=(
            "Existing file, symbol, configuration, API, data contract, or flow that the PR "
            "change demonstrably conflicts with."
        )
    )
    description: str = Field(
        description="What is incompatible and the concrete consequence if it is not resolved."
    )


class Finding(BaseModel):
    path: str
    start_line: int | None = None
    end_line: int | None = None
    severity: Literal["low", "medium", "high", "critical"]
    category: str
    title: str
    explanation: str
    conflict: ConflictDetail | None = Field(
        default=None,
        description=(
            "Conflict details when the defect is caused by an incompatibility with existing "
            "code or behavior; otherwise null. This is not for Git merge conflicts."
        ),
    )
    suggestion: str | None = Field(
        default=None,
        description=(
            "Exact replacement code for the reported line range, without Markdown fences. "
            "Use null when a safe exact replacement is not possible."
        ),
    )
    confidence: float = Field(default=0.5, ge=0, le=1)
    supporting_context_ids: list[str] = Field(default_factory=list)


class FileReview(BaseModel):
    path: str = Field(description="Exact repository-relative path from the supplied PR diff.")
    change_summary: str = Field(
        description="Brief factual explanation of what changed in this file."
    )


class ReviewResult(BaseModel):
    summary: str
    file_reviews: list[FileReview] = Field(default_factory=list)
    impact: str = ""
    findings: list[Finding] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class ModelReviewResponse(BaseModel):
    summary: str = Field(description="Concise summary of the PR purpose and implementation.")
    file_reviews: list[FileReview] = Field(
        description="Exactly one entry for every file present in the supplied PR diff."
    )
    impact: str = Field(
        description=(
            "Impact on existing flows, logic, dependencies, and compatibility, based only "
            "on the supplied diff and structural context."
        )
    )
    findings: list[Finding] = Field(default_factory=list)


class ProviderError(RuntimeError):
    pass


class ProviderRateLimited(ProviderError):
    pass


class InvalidProviderOutput(ProviderError):
    pass


class ReviewProvider(Protocol):
    name: str
    model: str

    async def review(self, prompt: str) -> ReviewResult: ...
