from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException

from codereviewer.config import get_settings
from codereviewer.db import Database
from codereviewer.github import GitHubClient
from codereviewer.graph import GraphStore
from codereviewer.queue import JobQueue
from codereviewer.webhooks import create_webhook_router

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
queue = JobQueue(database.sessions)
github = GitHubClient(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await database.create_schema()
    await graph.ensure_schema()
    yield
    await github.close()
    await graph.close()
    await database.close()


app = FastAPI(
    title="GraphMethod AI Pull Request Reviewer",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(create_webhook_router(settings, queue, github))


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready() -> dict[str, str]:
    try:
        await graph.verify()
        async with database.engine.connect() as connection:
            await connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Dependencies are unavailable") from exc
    return {"status": "ready"}


def run() -> None:
    uvicorn.run("codereviewer.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
