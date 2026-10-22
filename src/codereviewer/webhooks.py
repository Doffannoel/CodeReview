from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException, Request, status

from codereviewer.config import Settings
from codereviewer.github import GitHubClient
from codereviewer.queue import JobQueue
from codereviewer.security import verify_github_signature


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
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Webhook payload must be an object")

        action = str(payload.get("action", ""))
        try:
            job, duplicate = await queue.enqueue_pull_request(
                delivery_id=x_github_delivery,
                event=x_github_event,
                action=action,
                payload=payload,
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return {
            "accepted": job is not None and not duplicate,
            "duplicate": duplicate,
            "job_id": job.id if job and not duplicate else None,
        }

    return router
