import pytest
from sqlalchemy import event

from codereviewer.config import Settings
from codereviewer.db import Database
from codereviewer.queue import JobQueue

PAYLOAD = {
    "installation": {"id": 123},
    "repository": {"id": 456, "name": "repo", "owner": {"login": "owner"}},
    "pull_request": {"number": 7, "head": {"sha": "abc123"}},
}


@pytest.mark.asyncio
async def test_queue_is_idempotent_and_claims_job() -> None:
    database = Database(Settings(database_url="sqlite+aiosqlite:///:memory:"))

    @event.listens_for(database.engine.sync_engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    await database.create_schema()
    queue = JobQueue(database.sessions)

    job, duplicate = await queue.enqueue_pull_request(
        delivery_id="delivery-1",
        event="pull_request",
        action="opened",
        payload=PAYLOAD,
    )
    repeated, repeated_duplicate = await queue.enqueue_pull_request(
        delivery_id="delivery-1",
        event="pull_request",
        action="opened",
        payload=PAYLOAD,
    )
    claimed = await queue.claim()

    assert job is not None
    assert not duplicate
    assert repeated is not None
    assert repeated.id == job.id
    assert repeated_duplicate
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "processing"

    await queue.complete(job.id)
    assert await queue.claim() is None
    await database.close()
