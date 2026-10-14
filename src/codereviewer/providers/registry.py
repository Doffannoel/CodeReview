from __future__ import annotations

from codereviewer.config import Settings
from codereviewer.providers.base import ReviewProvider
from codereviewer.providers.gemini import GeminiReviewProvider


def create_provider(settings: Settings) -> ReviewProvider:
    if settings.llm_provider == "gemini":
        return GeminiReviewProvider(
            api_key=settings.llm_api_key_value,
            model=settings.llm_model,
            fallback_model=settings.llm_fallback_model,
            base_url=settings.llm_base_url,
            max_retries=settings.llm_max_retries,
            retry_base_seconds=settings.llm_retry_base_seconds,
        )
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
