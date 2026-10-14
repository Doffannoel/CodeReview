import httpx
import pytest

from codereviewer.config import Settings
from codereviewer.github import REVIEW_STATUS_CONTEXT, GitHubClient


@pytest.mark.asyncio
async def test_set_commit_status_uses_stable_required_context() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(201, json={"id": 42})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    github = GitHubClient(Settings(github_api_url="https://github.test"), http)

    result = await github.set_commit_status(
        "token",
        "owner",
        "repo",
        "abc123",
        "pending",
        "Analyzing",
    )

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.url == "https://github.test/repos/owner/repo/statuses/abc123"
    assert request.headers["Authorization"] == "Bearer token"
    assert request.read()
    assert __import__("json").loads(request.content) == {
        "state": "pending",
        "context": REVIEW_STATUS_CONTEXT,
        "description": "Analyzing",
    }
    assert result == {"id": 42}
    await http.aclose()
