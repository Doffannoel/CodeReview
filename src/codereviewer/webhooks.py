from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from codereviewer.config import Settings
from codereviewer.github import GitHubClient
from codereviewer.queue import JobQueue
from codereviewer.security import verify_github_signature

logger = logging.getLogger(__name__)


def create_webhook_router(
    settings: Settings,
    queue: JobQueue,
    github: GitHubClient,
) -> APIRouter:
    router = APIRouter()

    @router.post("/webhooks/github", status_code=status.HTTP_202_ACCEPTED)
    async def github_webhook(
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict:
        body = await request.body()
        if not verify_github_signature(
            body,
            x_hub_signature_256,
            settings.github_webhook_secret_value,
        ):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        if not x_github_event or not x_github_delivery:
            raise HTTPException(status_code=400, detail="Missing GitHub webhook headers")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

        action = str(payload.get("action", ""))
        try:
            job, duplicate = await queue.enqueue_pull_request(
                delivery_id=x_github_delivery,
                event=x_github_event,
                action=action,
                payload=payload,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if job is not None and job.status in {"queued", "processing"} and not settings.dry_run:
            try:
                token = await github.installation_token(job.installation_id)
                await github.set_commit_status(
                    token,
                    job.owner,
                    job.repository,
                    job.head_sha,
                    "pending",
                    "GraphMethod is analyzing this pull request...",
                )
            except Exception as exc:
                logger.exception("failed to create pending status for review job %s", job.id)
                raise HTTPException(
                    status_code=502,
                    detail="Review queued, but the required GitHub status could not be created",
                ) from exc

        return {
            "accepted": job is not None and not duplicate,
            "duplicate": duplicate,
            "job_id": job.id if job and not duplicate else None,
        }

    return router
