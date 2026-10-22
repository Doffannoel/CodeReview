import asyncio
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from codereviewer.config import Settings
from codereviewer.db import Database
from codereviewer.diff import parse_unified_diff
from codereviewer.github import RepositorySource
from codereviewer.graph import GraphStore
from codereviewer.indexer import TreeSitterIndexer
from codereviewer.models import ReviewJob
from codereviewer.queue import JobQueue
from codereviewer.retrieval import GraphRetriever

pytestmark = pytest.mark.skipif(
    os.getenv("GRAPHMETHOD_INTEGRATION_TESTS") != "1",
    reason="Requires isolated services from deploy/test/compose.yaml",
)


async def test_postgres_upgrade_preserves_jobs_and_concurrent_claims_are_exclusive():
    url = make_url("postgresql+asyncpg://srs_test:local-test-only@127.0.0.1:15432/srs_test")
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
    name = f"srs_{uuid.uuid4().hex}"
    async with admin.connect() as connection:
        await connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    database = Database(
        Settings(database_url=url.set(database=name).render_as_string(hide_password=False))
    )
    try:
        await database.create_schema()
        queue = JobQueue(database.sessions)
        payload = {
            "installation": {"id": 1},
            "repository": {"id": 2, "name": "repo", "owner": {"login": "owner"}},
            "pull_request": {"number": 3, "head": {"sha": "head"}},
        }
        job, _ = await queue.enqueue_pull_request(
            delivery_id="legacy",
            event="pull_request",
            action="opened",
            payload=payload,
        )
        async with database.engine.begin() as connection:
            await connection.exec_driver_sql(
                "ALTER TABLE review_jobs DROP COLUMN request_key CASCADE"
            )
            await connection.exec_driver_sql(
                "ALTER TABLE review_jobs ADD CONSTRAINT "
                "uq_review_job_target UNIQUE "
                "(repository_id, pull_request_number, head_sha)"
            )
        await asyncio.gather(database.create_schema(), database.create_schema())
        async with database.sessions() as session:
            restored = await session.get(ReviewJob, job.id)
            assert restored.request_key == "automatic"
        payload["pull_request"]["head"]["sha"] = "second"
        second, _ = await queue.enqueue_pull_request(
            delivery_id="next",
            event="pull_request",
            action="synchronize",
            payload=payload,
        )
        claims = await asyncio.gather(queue.claim(), queue.claim())
        assert len([c for c in claims if c]) == 1
        await queue.complete(job.id)
        assert (await queue.claim()).id == second.id
        async with database.sessions() as session:
            assert len((await session.scalars(select(ReviewJob))).all()) == 2
    finally:
        await database.close()
        async with admin.connect() as connection:
            await connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.dispose()


async def test_neo4j_tree_sitter_callers_tests_and_class_labels():
    graph = GraphStore("bolt://127.0.0.1:17687", "neo4j", "local-test-only")
    repo_id = uuid.uuid4().int % 2_000_000_000
    try:
        await graph.ensure_schema()
        parsed = TreeSitterIndexer().parse_repository(
            [
                RepositorySource("service.ts", "export function pay() { return 1; }"),
                RepositorySource(
                    "route.ts",
                    "import {pay} from './service';\nexport function route() { return pay(); }",
                ),
                RepositorySource(
                    "base.ts", "export class Base {}\nexport class Child extends Base {}"
                ),
                RepositorySource(
                    "pay.test.ts", "import {pay} from './service';\ntest('pay', () => pay());"
                ),
            ]
        )
        await graph.store_snapshot(repo_id, "test/repo", "head", parsed)
        assert await graph.has_snapshot(repo_id, "head")
        diff = parse_unified_diff(
            "diff --git a/service.ts b/service.ts\n--- a/service.ts\n"
            "+++ b/service.ts\n@@ -1 +1 @@\n-old\n+export function pay() {}\n"
        )
        candidates, selected = await GraphRetriever(graph, 2, 12).retrieve(
            repo_id, "head", diff, 10000
        )
        assert any(c.path == "route.ts" and "CALLS" in c.relation for c in candidates)
        assert any(c.path == "pay.test.ts" and "TESTS" in c.relation for c in selected)
        async with graph.driver.session() as session:
            result = await session.run(
                "MATCH (n:Class {repository_id: $id}) RETURN count(n) AS count", id=repo_id
            )
            assert (await result.single())["count"] == 2
    finally:
        async with graph.driver.session() as session:
            await session.run("MATCH (n {repository_id: $id}) DETACH DELETE n", id=repo_id)
        await graph.close()
