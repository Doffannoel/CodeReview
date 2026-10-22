from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

import httpx
import jwt

from codereviewer.config import Settings
from codereviewer.repo_config import RepoConfig

SUMMARY_MARKER = "<!-- graphmethod-ai-review-summary -->"
FINDING_MARKER_PREFIX = "<!-- graphmethod-finding:"
REVIEW_STATUS_CONTEXT = "GraphMethod AI Review"
QUALITY_CHECK_NAME = "GraphMethod Quality Gate"
SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "generated",
    "coverage",
}
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
        self._bot_login: str | None = None

    def _app_jwt(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 30, "exp": now + 540, "iss": self.settings.github_app_id},
            self.settings.github_private_key,
            algorithm="RS256",
        )

    async def installation_token(self, installation_id: int) -> str:
        headers = self._headers(self._app_jwt())
        if self._bot_login is None:
            app = await self._client.get(f"{self.settings.github_api_url}/app", headers=headers)
            app.raise_for_status()
            self._bot_login = f"{app.json()['slug']}[bot]"
        response = await self._client.post(
            f"{self.settings.github_api_url}/app/installations/{installation_id}/access_tokens",
            headers=headers,
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

    async def get_repo_config(self, token: str, owner: str, repo: str, sha: str) -> str | None:
        response = await self._client.get(
            f"{self.settings.github_api_url}/repos/{owner}/{repo}/contents/.graphmethod.yml",
            params={"ref": sha},
            headers=self._headers(token),
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("type") != "file":
            raise ValueError(".graphmethod.yml must be a regular file")
        if data.get("encoding") != "base64" or int(data.get("size", 0)) > 32_768:
            raise ValueError(".graphmethod.yml is too large or has unsupported encoding")
        return base64.b64decode(data["content"]).decode("utf-8")

    async def set_quality_check(
        self,
        token: str,
        owner: str,
        repo: str,
        sha: str,
        request_id: str,
        state: str,
        summary: str,
    ) -> None:
        url = f"{self.settings.github_api_url}/repos/{owner}/{repo}"
        response = await self._client.get(
            f"{url}/commits/{sha}/check-runs",
            headers=self._headers(token),
            params={"check_name": QUALITY_CHECK_NAME, "per_page": 100, "filter": "latest"},
        )
        response.raise_for_status()
        existing = next(
            (
                check
                for check in response.json().get("check_runs", [])
                if check.get("external_id") == request_id
            ),
            None,
        )
        payload = {
            "name": QUALITY_CHECK_NAME,
            "external_id": request_id,
            "status": "in_progress" if state == "pending" else "completed",
            "output": {"title": QUALITY_CHECK_NAME, "summary": summary[:60_000]},
        }
        if state != "pending":
            payload["conclusion"] = {
                "success": "success",
                "failure": "failure",
                "error": "failure",
                "neutral": "neutral",
            }[state]
        if existing:
            response = await self._client.patch(
                f"{url}/check-runs/{existing['id']}",
                headers=self._headers(token),
                json=payload,
            )
        else:
            response = await self._client.post(
                f"{url}/check-runs",
                headers=self._headers(token),
                json={**payload, "head_sha": sha},
            )
        response.raise_for_status()

    async def list_sources(
        self,
        token: str,
        owner: str,
        repo: str,
        sha: str,
        config: RepoConfig | None = None,
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
                and PurePosixPath(path).suffix in SOURCE_EXTENSIONS
                and not (config and config.ignores(path))
                and not parts.intersection(EXCLUDED_PARTS)
                and PurePosixPath(path).name not in EXCLUDED_NAMES
            ):
                if int(item.get("size") or 0) > self.settings.max_source_file_bytes:
                    raise ValueError(f"Source exceeds configured size limit: {path}")
                candidates.append(item)

        if len(candidates) > self.settings.max_repository_files:
            raise RuntimeError(
                f"Repository has {len(candidates)} eligible files; limit is "
                f"{self.settings.max_repository_files}"
            )

        semaphore = asyncio.Semaphore(8)

        async def fetch(item: dict) -> RepositorySource:
            async with semaphore:
                blob = await self._client.get(
                    f"{self.settings.github_api_url}/repos/{owner}/{repo}/git/blobs/{item['sha']}",
                    headers=self._headers(token),
                )
                blob.raise_for_status()
                data = blob.json()
                if data.get("encoding") != "base64":
                    raise ValueError(f"Unsupported source encoding: {item['path']}")
                raw = base64.b64decode(data["content"])
                if len(raw) > self.settings.max_source_file_bytes:
                    raise ValueError(f"Source exceeds configured size limit: {item['path']}")
                return RepositorySource(path=item["path"], content=raw.decode("utf-8"))

        return list(await asyncio.gather(*(fetch(item) for item in candidates)))

    async def _comments(self, token: str, path: str) -> list[dict]:
        comments = []
        page = 1
        while True:
            response = await self._client.get(
                f"{self.settings.github_api_url}{path}",
                headers=self._headers(token),
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            batch = response.json()
            comments.extend(batch)
            if len(batch) < 100:
                return comments
            page += 1

    def is_own_comment(self, comment: dict) -> bool:
        app = comment.get("performed_via_github_app") or {}
        return comment.get("user", {}).get("type") == "Bot" and (
            (
                bool(self.settings.github_app_id)
                and str(app.get("id", "")) == self.settings.github_app_id
            )
            or (
                self._bot_login is not None
                and comment.get("user", {}).get("login") == self._bot_login
            )
        )

    async def list_issue_comments(
        self, token: str, owner: str, repo: str, number: int
    ) -> list[dict]:
        return await self._comments(token, f"/repos/{owner}/{repo}/issues/{number}/comments")

    async def upsert_summary(
        self, token: str, owner: str, repo: str, number: int, body: str
    ) -> int:
        marked_body = f"{SUMMARY_MARKER}\n{body}"
        comments = await self.list_issue_comments(token, owner, repo, number)
        existing = next(
            (
                comment
                for comment in comments
                if SUMMARY_MARKER in comment.get("body", "") and self.is_own_comment(comment)
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
        return await self._comments(token, f"/repos/{owner}/{repo}/pulls/{number}/comments")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
