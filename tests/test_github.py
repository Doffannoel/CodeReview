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


async def test_comment_pagination_and_verified_app_identity():
    pages = []

    async def handler(request):
        page = int(request.url.params["page"])
        pages.append(page)
        return httpx.Response(
            200, json=[{"id": i} for i in range(100)] if page == 1 else [{"id": 101}]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        github = GitHubClient(Settings(github_app_id="42"), http)
        github._bot_login = "reviewer[bot]"
        assert len(await github.list_issue_comments("token", "o", "r", 1)) == 101
        assert github.is_own_comment({"user": {"type": "Bot", "login": "reviewer[bot]"}})
        assert not github.is_own_comment({"user": {"type": "Bot", "login": "other[bot]"}})
        assert not github.is_own_comment({"user": {"type": "User", "login": "reviewer[bot]"}})
    assert pages == [1, 2]
