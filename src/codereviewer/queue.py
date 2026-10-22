from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from codereviewer.models import ReviewJob, WebhookDelivery

SUPPORTED_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}
logger = logging.getLogger(__name__)


def _is_unique_violation(error: IntegrityError) -> bool:
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate is not None:
        return sqlstate == "23505"
    return "unique constraint failed" in str(original).lower()


class JobQueue:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def enqueue_pull_request(
        self,
        *,
        delivery_id: str,
        event: str,
        action: str,
        payload: dict,
    ) -> tuple[ReviewJob | None, bool]:
        manual = event == "issue_comment" and action == "created"
        if manual:
            comment = payload.get("comment") or {}
            issue = payload.get("issue") or {}
            if (
                str(comment.get("body", "")).strip() != "/review"
                or not issue.get("pull_request")
                or comment.get("user", {}).get("type") == "Bot"
                or comment.get("author_association") not in {"OWNER", "MEMBER", "COLLABORATOR"}
            ):
                return None, False
            pull_request = {"number": issue["number"], "head": {"sha": ""}}
            request_key = f"comment:{int(comment['id'])}"
        elif event == "pull_request" and action in SUPPORTED_ACTIONS:
            pull_request = payload["pull_request"]
            request_key = "automatic"
        else:
            return None, False

        repository = payload["repository"]
        installation = payload.get("installation") or {}
        if not installation.get("id"):
            raise ValueError("Webhook payload does not contain an installation id")

        delivery = WebhookDelivery(
            delivery_id=delivery_id,
            event=event,
            action=action,
            payload=payload,
        )
        job = ReviewJob(
            delivery_id=delivery_id,
            installation_id=int(installation["id"]),
            repository_id=int(repository["id"]),
            owner=repository["owner"]["login"],
            repository=repository["name"],
            pull_request_number=int(pull_request["number"]),
            head_sha=pull_request["head"]["sha"],
            request_key=request_key,
        )

        async with self.sessions() as session:
            if manual:
                existing = (
                    await session.execute(
                        select(ReviewJob).where(
                            ReviewJob.repository_id == job.repository_id,
                            ReviewJob.request_key == request_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing:
                    return existing, True
            if await session.get(WebhookDelivery, delivery_id):
                existing = (
                    await session.execute(
                        select(ReviewJob).where(
                            ReviewJob.delivery_id == delivery_id,
                        )
                    )
                ).scalar_one_or_none()
                return existing, True
            try:
                # ReviewJob only stores the delivery's scalar foreign key. Flush
                # the parent explicitly so databases that enforce foreign keys do
                # not attempt to insert the child first.
                session.add(delivery)
                await session.flush()
                session.add(job)
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                if _is_unique_violation(error):
                    statement = select(ReviewJob).where(
                        ReviewJob.repository_id == int(repository["id"]),
                        ReviewJob.pull_request_number == int(pull_request["number"]),
                        ReviewJob.request_key == request_key,
                    )
                    if not manual:
                        statement = statement.where(ReviewJob.head_sha == job.head_sha)
                    existing = (await session.execute(statement)).scalar_one_or_none()
                    return existing, True
                logger.exception("failed to enqueue GitHub delivery %s", delivery_id)
                raise
        return job, False

    async def claim(self, lease_seconds: float = 120) -> ReviewJob | None:
        now = datetime.now(UTC)
        async with self.sessions() as session, session.begin():
            if session.bind.dialect.name == "postgresql":
                # Claims are short; serialize selection to prevent two workers taking one PR.
                await session.execute(text("SELECT pg_advisory_xact_lock(746326002)"))
            await session.execute(
                update(ReviewJob)
                .where(
                    ReviewJob.status == "processing",
                    ReviewJob.locked_at < now - timedelta(seconds=lease_seconds),
                )
                .values(status="queued", locked_at=None, available_at=now)
            )
            active = aliased(ReviewJob)
            statement = (
                select(ReviewJob)
                .where(ReviewJob.status == "queued", ReviewJob.available_at <= now)
                .where(
                    ~select(active.id)
                    .where(
                        active.repository_id == ReviewJob.repository_id,
                        active.pull_request_number == ReviewJob.pull_request_number,
                        active.status == "processing",
                    )
                    .exists()
                )
                .order_by(ReviewJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            job = (await session.execute(statement)).scalar_one_or_none()
            if job is None:
                return None
            job.status = "processing"
            job.attempt += 1
            job.locked_at = now
            return job

    async def complete(self, job_id: str) -> None:
        async with self.sessions() as session, session.begin():
            job = await session.get(ReviewJob, job_id, with_for_update=True)
            if job:
                job.status = "completed"
                job.completed_at = datetime.now(UTC)
                job.error_message = None

    async def fail(self, job_id: str, message: str, max_attempts: int) -> bool:
        async with self.sessions() as session, session.begin():
            job = await session.get(ReviewJob, job_id, with_for_update=True)
            if job is None:
                return False
            job.error_message = message[:4000]
            if job.attempt < max_attempts:
                job.status = "queued"
                job.available_at = datetime.now(UTC) + timedelta(seconds=2**job.attempt)
                job.locked_at = None
            else:
                job.status = "failed"
                job.completed_at = datetime.now(UTC)
                return True
            return False
