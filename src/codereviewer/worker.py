from __future__ import annotations

import asyncio
import logging
import signal

from codereviewer.config import get_settings
from codereviewer.db import Database
from codereviewer.github import GitHubClient
from codereviewer.graph import GraphStore
from codereviewer.providers.registry import create_provider
from codereviewer.queue import JobQueue
from codereviewer.review import ReviewOrchestrator

logger = logging.getLogger(__name__)


async def worker_loop() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    database = Database(settings)
    graph = GraphStore(
        settings.neo4j_uri,
        settings.neo4j_username,
        settings.neo4j_password_value,
    )
    github = GitHubClient(settings)
    provider = create_provider(settings)
    queue = JobQueue(database.sessions)
    reviewer = ReviewOrchestrator(settings, database.sessions, github, graph, provider)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(event, stop.set)
        except NotImplementedError:
            pass

    await database.create_schema()
    await graph.ensure_schema()
    logger.info("review worker started")
    try:
        while not stop.is_set():
            job = await queue.claim(settings.worker_job_timeout_seconds + 30)
            if job is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
                continue
            try:
                logger.info(
                    "processing review job %s for %s/%s#%s",
                    job.id,
                    job.owner,
                    job.repository,
                    job.pull_request_number,
                )
                if job.attempt > settings.worker_max_attempts:
                    raise RuntimeError("Review exhausted attempts after worker interruption")
                async with asyncio.timeout(settings.worker_job_timeout_seconds):
                    await reviewer.execute(job)
                await queue.complete(job.id)
            except Exception as exc:
                logger.exception("review job %s failed", job.id)
                terminal = await queue.fail(
                    job.id,
                    str(exc) or type(exc).__name__,
                    settings.worker_max_attempts,
                )
                if terminal and not settings.dry_run and job.head_sha:
                    try:
                        token = await github.installation_token(job.installation_id)
                        await reviewer._status(
                            token,
                            job,
                            "error",
                            "GraphMethod analysis failed after all retry attempts.",
                        )
                    except Exception:
                        logger.exception(
                            "failed to update terminal GitHub status for review job %s", job.id
                        )
    finally:
        await github.close()
        close_provider = getattr(provider, "close", None)
        if close_provider:
            await close_provider()
        await graph.close()
        await database.close()


def run() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    run()
