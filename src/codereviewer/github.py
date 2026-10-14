from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

import httpx
import jwt

from codereviewer.config import Settings

SUMMARY_MARKER = "<!-- graphmethod-ai-review-summary -->"
FINDING_MARKER_PREFIX = "<!-- graphmethod-finding:"
REVIEW_STATUS_CONTEXT = "GraphMethod AI Review"
EXCLUDED_PARTS = {".git", "node_modules", "vendor", "dist", "build", "generated"}
EXCLUDED_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "go.sum",
    "poetry.lock",
}


@dataclass(frozen=True)
class RepositorySource:
    path: str
    content: str


class GitHubClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=30)
        self._owns_client = client is None

    def _app_jwt(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 30, "exp": now + 540, "iss": self.settings.github_app_id},
            self.settings.github_private_key,
            algorithm="RS256",
        )

    async def installation_token(self, installation_id: int) -> str:
        response = await self._client.post(
            f"{self.settings.github_api_url}/app/installations/{installation_id}/access_tokens",
            headers=self._headers(self._app_jwt()),
        )
        response.raise_for_status()
        return response.json()["token"]

    @staticmethod
    def _headers(token: str, accept: str = "application/vnd.github+json") -> dict[str, str]:
        return {
            "Accept": accept,
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "graphmethod-code-reviewer/0.1",
        }

    async def get_pull_request(self, token: str, owner: str, repo: str, number: int) -> dict:
        response = await self._client.get(
            f"{self.settings.github_api_url}/repos/{owner}/{repo}/pulls/{number}",
            headers=self._headers(token),
        )
        response.raise_for_status()
        return response.json()

    async def get_pull_request_diff(self, token: str, owner: str, repo: str, number: int) -> str:
        response = await self._client.get(
            f"{self.settings.github_api_url}/repos/{owner}/{repo}/pulls/{number}",
            headers=self._headers(token, "application/vnd.github.v3.diff"),
        )
        response.raise_for_status()
        return response.text

    async def set_commit_status(
        self,
        token: str,
        owner: str,
        repo: str,
        sha: str,
        state: str,
        description: str,
    ) -> dict:
        response = await self._client.post(
            f"{self.settings.github_api_url}/repos/{owner}/{repo}/statuses/{sha}",
            headers=self._headers(token),
            json={
                "state": state,
                "context": REVIEW_STATUS_CONTEXT,
                "description": description[:140],
            },
        )
        response.raise_for_status()
        return response.json()

    async def list_python_sources(
        self, token: str, owner: str, repo: str, sha: str
    ) -> list[RepositorySource]:
        response = await self._client.get(
            f"{self.settings.github_api_url}/repos/{owner}/{repo}/git/trees/{sha}",
            params={"recursive": "1"},
            headers=self._headers(token),
        )
        response.raise_for_status()
        tree = response.json()
        if tree.get("truncated"):
            raise RuntimeError(
                "GitHub tree response was truncated; repository is too large for MVP indexing"
            )

        candidates = []
        for item in tree.get("tree", []):
            path = item.get("path", "")
            parts = set(PurePosixPath(path).parts)
            if (
                item.get("type") == "blob"
                and path.endswith(".py")
                and not parts.intersection(EXCLUDED_PARTS)
                and PurePosixPath(path).name not in EXCLUDED_NAMES
                and int(item.get("size") or 0) <= self.settings.max_source_file_bytes
            ):
                candidates.append(item)

        if len(candidates) > self.settings.max_repository_files:
            raise RuntimeError(
                f"Repository has {len(candidates)} eligible files; limit is "
                f"{self.settings.max_repository_files}"
            )

        sources: list[RepositorySource] = []
        for item in candidates:
            blob = await self._client.get(item["url"], headers=self._headers(token))
            blob.raise_for_status()
            data = blob.json()
            if data.get("encoding") != "base64":
                continue
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            sources.append(RepositorySource(path=item["path"], content=content))
        return sources

    async def list_issue_comments(
        self, token: str, owner: str, repo: str, number: int
    ) -> list[dict]:
        response = await self._client.get(
            f"{self.settings.github_api_url}/repos/{owner}/{repo}/issues/{number}/comments",
            headers=self._headers(token),
            params={"per_page": 100},
        )
        response.raise_for_status()
        return response.json()

    async def upsert_summary(
        self, token: str, owner: str, repo: str, number: int, body: str
    ) -> int:
        marked_body = f"{SUMMARY_MARKER}\n{body}"
        comments = await self.list_issue_comments(token, owner, repo, number)
        existing = next(
            (
                comment
                for comment in comments
                if SUMMARY_MARKER in comment.get("body", "")
                and comment.get("user", {}).get("type") == "Bot"
            ),
            None,
        )
        if existing:
            response = await self._client.patch(
                f"{self.settings.github_api_url}/repos/{owner}/{repo}/issues/comments/"
                f"{existing['id']}",
                headers=self._headers(token),
                json={"body": marked_body},
            )
        else:
            response = await self._client.post(
                f"{self.settings.github_api_url}/repos/{owner}/{repo}/issues/{number}/comments",
                headers=self._headers(token),
                json={"body": marked_body},
            )
        response.raise_for_status()
        return int(response.json()["id"])

    async def create_review(
        self,
        token: str,
        owner: str,
        repo: str,
        number: int,
        head_sha: str,
        body: str,
        comments: list[dict],
    ) -> dict:
        response = await self._client.post(
            f"{self.settings.github_api_url}/repos/{owner}/{repo}/pulls/{number}/reviews",
            headers=self._headers(token),
            json={
                "commit_id": head_sha,
                "event": "COMMENT",
                "body": body,
                "comments": comments,
            },
        )
        response.raise_for_status()
        return response.json()

    async def list_review_comments(
        self, token: str, owner: str, repo: str, number: int
    ) -> list[dict]:
        response = await self._client.get(
            f"{self.settings.github_api_url}/repos/{owner}/{repo}/pulls/{number}/comments",
            headers=self._headers(token),
            params={"per_page": 100},
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
