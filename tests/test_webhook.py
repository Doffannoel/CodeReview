import hashlib
import hmac
import json
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from codereviewer.config import Settings
from codereviewer.webhooks import create_webhook_router


@dataclass
class FakeJob:
    id: str = "job-1"
    installation_id: int = 123
    owner: str = "owner"
    repository: str = "repo"
    head_sha: str = "abc123"
    status: str = "queued"


class FakeQueue:
    async def enqueue_pull_request(self, **_: object):
        return FakeJob(), False


class FakeGitHub:
    def __init__(self) -> None:
        self.statuses: list[tuple[object, ...]] = []

    async def installation_token(self, installation_id: int) -> str:
        assert installation_id == 123
        return "installation-token"

    async def set_commit_status(self, *args: object) -> dict:
        self.statuses.append(args)
        return {"id": 1}


def signed_headers(body: bytes, secret: str) -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": f"sha256={digest}",
        "Content-Type": "application/json",
    }


def test_webhook_accepts_valid_signature() -> None:
    secret = "webhook-secret"
    settings = Settings(github_webhook_secret=secret)
    app = FastAPI()
    github = FakeGitHub()
    app.include_router(  # type: ignore[arg-type]
        create_webhook_router(settings, FakeQueue(), github)
    )
    body = json.dumps({"action": "opened"}).encode()

    response = TestClient(app).post(
        "/webhooks/github",
        content=body,
        headers=signed_headers(body, secret),
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": True, "duplicate": False, "job_id": "job-1"}
    assert github.statuses == [
        (
            "installation-token",
            "owner",
            "repo",
            "abc123",
            "pending",
            "GraphMethod is analyzing this pull request...",
        )
    ]


def test_webhook_rejects_invalid_signature() -> None:
    settings = Settings(github_webhook_secret="webhook-secret")
    app = FastAPI()
    app.include_router(  # type: ignore[arg-type]
        create_webhook_router(settings, FakeQueue(), FakeGitHub())
    )

    response = TestClient(app).post(
        "/webhooks/github",
        content=b"{}",
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-1",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )

    assert response.status_code == 401
