import json

import httpx
import pytest
import respx

from codereviewer.providers.base import ProviderRateLimited
from codereviewer.providers.gemini import GeminiReviewProvider


@pytest.mark.asyncio
@respx.mock
async def test_gemini_provider_normalizes_structured_output() -> None:
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "summary": "One issue found.",
                                            "file_reviews": [
                                                {
                                                    "path": "app.py",
                                                    "change_summary": "Updates result handling.",
                                                }
                                            ],
                                            "impact": "The result flow now handles missing values.",
                                            "findings": [
                                                {
                                                    "path": "app.py",
                                                    "start_line": 2,
                                                    "severity": "high",
                                                    "category": "correctness",
                                                    "title": "Unchecked result",
                                                    "explanation": "The result can be null.",
                                                    "conflict": {
                                                        "target": "app.py::caller",
                                                        "description": (
                                                            "The caller assumes a non-null result."
                                                        ),
                                                    },
                                                }
                                            ],
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
            },
        )
    )
    provider = GeminiReviewProvider(
        api_key="secret",
        model="test-model",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )

    result = await provider.review("review this")
    await provider.close()

    assert route.called
    assert result.provider == "gemini"
    assert result.model == "test-model"
    assert result.input_tokens == 100
    assert result.file_reviews[0].path == "app.py"
    assert result.impact == "The result flow now handles missing values."
    assert result.findings[0].path == "app.py"
    assert result.findings[0].conflict is not None
    assert result.findings[0].conflict.target == "app.py::caller"
    request_payload = json.loads(route.calls.last.request.content)
    deprecated_parameters = {
        "temperature",
        "topP",
        "topK",
        "candidateCount",
        "thinkingBudget",
    }
    assert deprecated_parameters.isdisjoint(request_payload["generationConfig"])
    assert request_payload["generationConfig"]["responseMimeType"] == "application/json"
    assert "key=" not in str(route.calls.last.request.url)
    assert route.calls.last.request.headers["x-goog-api-key"] == "secret"


@pytest.mark.asyncio
@respx.mock
async def test_gemini_provider_retries_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("codereviewer.providers.gemini.asyncio.sleep", record_sleep)
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent"
    ).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                                "content": {
                                    "parts": [
                                        {
                                            "text": json.dumps(
                                                {
                                                    "summary": "Recovered.",
                                                    "file_reviews": [],
                                                    "impact": "No material impact.",
                                                }
                                            )
                                        }
                                    ]
                                }
                        }
                    ]
                },
            ),
        ]
    )
    provider = GeminiReviewProvider(
        "secret",
        "test-model",
        "https://generativelanguage.googleapis.com/v1beta",
    )

    result = await provider.review("review this")
    await provider.close()

    assert result.summary == "Recovered."
    assert route.call_count == 2
    assert len(delays) == 1


@pytest.mark.asyncio
@respx.mock
async def test_gemini_provider_falls_back_after_primary_remains_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def skip_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("codereviewer.providers.gemini.asyncio.sleep", skip_sleep)
    primary = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/primary:generateContent"
    ).mock(return_value=httpx.Response(503))
    fallback = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/fallback:generateContent"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "summary": "Recovered via fallback.",
                                            "file_reviews": [],
                                            "impact": "No material impact.",
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )
    )
    provider = GeminiReviewProvider(
        "secret",
        "primary",
        "https://generativelanguage.googleapis.com/v1beta",
        fallback_model="fallback",
        max_retries=1,
    )

    result = await provider.review("review this")
    await provider.close()

    assert primary.call_count == 2
    assert fallback.call_count == 1
    assert result.summary == "Recovered via fallback."
    assert result.model == "fallback"


@pytest.mark.asyncio
@respx.mock
async def test_gemini_provider_maps_rate_limit() -> None:
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent"
    ).mock(return_value=httpx.Response(429))
    provider = GeminiReviewProvider(
        "secret",
        "test-model",
        "https://generativelanguage.googleapis.com/v1beta",
        max_retries=0,
    )

    with pytest.raises(ProviderRateLimited):
        await provider.review("review this")
    await provider.close()
