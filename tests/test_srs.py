from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from codereviewer.config import Settings
from codereviewer.db import Database
from codereviewer.diff import parse_unified_diff
from codereviewer.github import GitHubClient, RepositorySource
from codereviewer.indexer import TreeSitterIndexer
from codereviewer.models import ReviewEvaluation, ReviewJob, ReviewRun
from codereviewer.providers.base import FileReview, Finding, ReviewResult
from codereviewer.queue import JobQueue
from codereviewer.repo_config import RepoConfig, parse_repo_config
from codereviewer.review import (
    ReviewOrchestrator,
    finding_markdown,
    quality_score,
    summary_markdown,
    validate_findings,
)

DIFF = """diff --git a/app.ts b/app.ts
--- a/app.ts
+++ b/app.ts
@@ -1 +1,2 @@
-old();
+unsafe();
+other();
"""
PAYLOAD = {
    "installation": {"id": 1},
    "repository": {"id": 2, "name": "repo", "owner": {"login": "owner"}},
    "pull_request": {"number": 3, "head": {"sha": "head"}},
}


def finding(**changes):
    return Finding.model_validate(
        {
            "path": "app.ts",
            "start_line": 1,
            "severity": "critical",
            "category": "SQL Injection",
            "title": "Unsafe query",
            "explanation": "Input is interpolated into SQL.",
            "confidence": 0.8,
            **changes,
        }
    )


def test_config_and_gate_use_validated_findings():
    config = parse_repo_config("""
ignore_paths: ['generated/**', '**/*.test.ts']
min_confidence: 0.85
review_language: en
quality_gate:
  threshold: 90
  penalties:
    critical: 30
""")
    assert config.ignores("generated/app.ts")
    assert config.ignores("app.test.ts")
    assert not config.ignores("app.ts")
    result = ReviewResult(
        summary="Review",
        findings=[
            finding(confidence=0.84),
            finding(confidence=0.85),
            finding(confidence=0.9),
            finding(path="invented.ts", confidence=1),
            finding(title="Naming", severity="high", confidence=1),
        ],
    )
    diff = parse_unified_diff(DIFF)
    validated = validate_findings(result, diff, 50, config.min_confidence)
    assert len(validated) == 2
    assert quality_score(validated, config) == 60
    assert quality_score(validated * 10, config) == 0
    summary = summary_markdown(result, validated, "base", "head", diff, config, 2)
    assert "**FAILED**" in summary and "**60%**" in summary
    assert "1/2 files" in summary and "CRITICAL | -30" in summary


@pytest.mark.parametrize(
    "content",
    [
        "ignore_paths: ../../private",
        "ignore_paths: ['../private']",
        "min_confidence: 0.79",
        "quality_gate: {threshold: 101}",
        "quality_gate: {penalties: {high: -10}}",
        "unknown: true",
        "[]",
        "!!python/object/apply:os.system ['echo unsafe']",
        pytest.param("x" * 32769, id="oversized"),
    ],
)
def test_invalid_repo_config_is_rejected(content):
    with pytest.raises(ValueError):
        parse_repo_config(content)


def test_suggestion_preserves_indentation_and_invalid_ranges_are_not_inline():
    item = finding(suggestion="    safe();")
    assert "```suggestion\n    safe();\n```" in finding_markdown(item, github_suggestion=True)
    result = ReviewResult(summary="", findings=[finding(end_line=1000000000)])
    validated = validate_findings(result, parse_unified_diff(DIFF), 10)
    assert not validated[0].is_inline
    assert not validate_findings(
        ReviewResult(summary="", findings=[finding(confidence=0.79)]), parse_unified_diff(DIFF), 10
    )


def test_tree_sitter_cross_file_typescript_jsx_python_and_inheritance():
    parsed = TreeSitterIndexer().parse_repository(
        [
            RepositorySource("src/service.ts", "export class Base {}\nexport function charge() {}"),
            RepositorySource("src/other.ts", "export function charge() {}"),
            RepositorySource(
                "src/app.ts",
                """import { charge as pay, Base } from './service';
export class Checkout extends Base { submit() { pay(); } }
export const run = () => pay();""",
            ),
            RepositorySource(
                "src/app.test.ts",
                """import { run } from './app';
test('runs', () => { run(); });""",
            ),
            RepositorySource("src/page.tsx", "export const Page = () => <main>Hello</main>;"),
            RepositorySource("src/label.jsx", "export function Label() { return <label />; }"),
            RepositorySource("lib/base.py", "def save():\n    return 1"),
            RepositorySource(
                "lib/use.py", "from .base import save as persist\ndef run():\n    persist()"
            ),
        ]
    )
    relations = {(r.source_id, r.target_id, r.relation) for r in parsed.relations}
    assert ("src/app.ts:Checkout", "src/service.ts:Base", "EXTENDS") in relations
    assert ("src/app.ts:Checkout.submit", "src/service.ts:charge", "CALLS") in relations
    assert ("src/app.ts:run", "src/service.ts:charge", "CALLS") in relations
    assert ("lib/use.py:run", "lib/base.py:save", "CALLS") in relations
    assert any(r[1:] == ("src/app.ts:run", "TESTS") for r in relations)
    assert {s.name for s in parsed.symbols} >= {"Page", "Label"}
    with pytest.raises(ValueError, match="broken.ts"):
        TreeSitterIndexer().parse_repository([RepositorySource("broken.ts", "function (")])


async def test_manual_review_requeues_same_commit_and_deduplicates_after_resolution():
    database = Database(Settings(database_url="sqlite+aiosqlite:///:memory:"))
    await database.create_schema()
    queue = JobQueue(database.sessions)
    automatic, _ = await queue.enqueue_pull_request(
        delivery_id="auto",
        event="pull_request",
        action="opened",
        payload=PAYLOAD,
    )
    payload = deepcopy(PAYLOAD)
    payload.update(
        issue={"number": 3, "pull_request": {"url": "ignored"}},
        comment={
            "id": 42,
            "body": "/review",
            "author_association": "MEMBER",
            "user": {"type": "User"},
        },
    )
    manual, duplicate = await queue.enqueue_pull_request(
        delivery_id="manual",
        event="issue_comment",
        action="created",
        payload=payload,
    )
    assert manual and automatic and manual.id != automatic.id and not duplicate
    assert (await queue.claim()).id == automatic.id
    assert await queue.claim() is None  # One active review per PR.
    await queue.complete(automatic.id)
    assert (await queue.claim()).id == manual.id
    async with database.sessions() as session, session.begin():
        (await session.get(ReviewJob, manual.id)).head_sha = "head"
    repeated, duplicate = await queue.enqueue_pull_request(
        delivery_id="redelivered",
        event="issue_comment",
        action="created",
        payload=payload,
    )
    assert duplicate and repeated.id == manual.id
    payload["comment"]["id"] = 43
    payload["comment"]["author_association"] = "NONE"
    rejected, _ = await queue.enqueue_pull_request(
        delivery_id="untrusted",
        event="issue_comment",
        action="created",
        payload=payload,
    )
    assert rejected is None
    await database.close()


@pytest.mark.parametrize(
    "mode", ["normal", "ignored", "stale", "manual", "coverage", "config", "timeout"]
)
async def test_review_pipeline_scores_persists_and_never_passes_stale_head(mode):
    ignored, stale = mode == "ignored", mode == "stale"
    database = Database(Settings(database_url="sqlite+aiosqlite:///:memory:"))
    await database.create_schema()
    job, _ = await JobQueue(database.sessions).enqueue_pull_request(
        delivery_id="delivery",
        event="pull_request",
        action="opened",
        payload=PAYLOAD,
    )
    if mode == "manual":
        async with database.sessions() as session, session.begin():
            stored = await session.get(ReviewJob, job.id)
            stored.head_sha, stored.request_key = "", "comment:42"
        job.head_sha, job.request_key = "", "comment:42"
    pr = {"head": {"sha": "head"}, "base": {"sha": "base"}, "state": "open", "changed_files": 1}
    github = AsyncMock(spec=GitHubClient)
    github.installation_token.return_value = "token"
    github.get_pull_request.side_effect = [pr, {**pr, "head": {"sha": "new"}} if stale else pr]
    github.get_pull_request_diff.return_value = DIFF
    github.get_repo_config.return_value = "ignore_paths: ['*.ts']" if ignored else None
    github.list_review_comments.return_value = []
    graph = AsyncMock()
    graph.has_snapshot.return_value = True
    graph.changed_symbols.return_value = []
    graph.retrieve_context.return_value = []
    provider = AsyncMock()
    provider.name, provider.model = "fake", "fake-model"
    provider.review.return_value = ReviewResult(
        summary="Review",
        findings=[finding()],
        file_reviews=[FileReview(path="app.ts", change_summary="Changed calls.")],
    )
    reviewer = ReviewOrchestrator(Settings(), database.sessions, github, graph, provider)
    if mode in {"coverage", "config", "timeout"}:
        if mode == "coverage":
            provider.review.return_value.file_reviews = []
        elif mode == "config":
            github.get_repo_config.return_value = "min_confidence: 0.1"
        else:
            import asyncio

            provider.review.side_effect = asyncio.CancelledError
        with pytest.raises((ValueError, RuntimeError, __import__("asyncio").CancelledError)):
            await reviewer.execute(job)
        async with database.sessions() as session:
            run = (await session.scalars(select(ReviewRun))).one()
        assert run.status == "failed"
        assert all(call.args[-2] == "pending" for call in github.set_commit_status.await_args_list)
        github.upsert_summary.assert_not_awaited()
        await database.close()
        return
    await reviewer.execute(job)
    github.get_repo_config.assert_awaited_once_with("token", "owner", "repo", "base")
    async with database.sessions() as session:
        run = (await session.scalars(select(ReviewRun))).one()
        evaluation = (await session.scalars(select(ReviewEvaluation))).one_or_none()
    if stale:
        assert run.status == "superseded" and evaluation is None
        github.upsert_summary.assert_not_awaited()
        assert len(github.set_commit_status.await_args_list) == 1
    else:
        assert evaluation.score == (100 if ignored else 75)
        state = github.set_commit_status.await_args_list[-1].args[-2]
        assert state == ("success" if ignored else "failure")
        assert evaluation.passed == ignored
        assert evaluation.details["files_reviewed"] == (0 if ignored else 1)
        if ignored:
            provider.review.assert_not_awaited()
        else:
            assert "Price Tampering" in provider.review.await_args.args[0]
            assert "Guard Clauses" in provider.review.await_args.args[0]
    await database.close()


def test_default_and_commonjs_imports_resolve_without_global_name_guessing():
    parsed = TreeSitterIndexer().parse_repository(
        [
            RepositorySource("lib.js", "function helper() {}\nexport default function run() {}"),
            RepositorySource(
                "caller.js", "import execute from './lib';\nfunction main() { execute(); }"
            ),
            RepositorySource("db.cjs", "exports.save = () => 1;"),
            RepositorySource(
                "service.cjs",
                "const {save: persist} = require('./db.cjs');\nfunction main() { persist(); }",
            ),
        ]
    )
    relations = {(r.source_id, r.target_id, r.relation) for r in parsed.relations}
    assert ("caller.js:main", "lib.js:run", "CALLS") in relations
    assert ("service.cjs:main", "db.cjs:save", "CALLS") in relations


async def test_inline_dedup_ignores_spoofed_markers_and_previous_commits():
    from codereviewer.github import FINDING_MARKER_PREFIX

    diff = parse_unified_diff(DIFF)
    result = ReviewResult(summary="", findings=[finding()])
    findings = validate_findings(result, diff, 10)
    marker = f"{FINDING_MARKER_PREFIX}{findings[0].fingerprint} -->"
    github = AsyncMock(spec=GitHubClient)
    github.is_own_comment.side_effect = lambda c: c.get("own", False)
    github.list_review_comments.return_value = [
        {"body": marker, "commit_id": "head"},
        {"body": marker, "commit_id": "previous", "own": True},
    ]
    reviewer = ReviewOrchestrator(Settings(), None, github, AsyncMock(), AsyncMock())
    job = ReviewJob(owner="owner", repository="repo", pull_request_number=3, head_sha="head")
    await reviewer._publish("token", job, result, diff, findings, "base")
    github.create_review.assert_awaited_once()
    github.create_review.reset_mock()
    github.list_review_comments.return_value.append(
        {"body": marker, "commit_id": "head", "own": True}
    )
    await reviewer._publish("token", job, result, diff, findings, "base")
    github.create_review.assert_not_awaited()


async def test_expired_claim_is_recovered_with_attempt_count_preserved():
    from datetime import UTC, datetime, timedelta

    database = Database(Settings(database_url="sqlite+aiosqlite:///:memory:"))
    await database.create_schema()
    queue = JobQueue(database.sessions)
    job, _ = await queue.enqueue_pull_request(
        delivery_id="lease",
        event="pull_request",
        action="opened",
        payload=PAYLOAD,
    )
    await queue.claim()
    async with database.sessions() as session, session.begin():
        stored = await session.get(ReviewJob, job.id)
        stored.locked_at = datetime.now(UTC) - timedelta(minutes=5)
    recovered = await queue.claim()
    assert recovered.id == job.id and recovered.attempt == 2
    await database.close()


async def test_github_config_check_run_and_multilanguage_source_fetch():
    calls = []

    async def handler(request):
        calls.append(request)
        if request.url.path.endswith(".graphmethod.yml"):
            assert request.url.params["ref"] == "base"
            return httpx.Response(404)
        if request.url.path.endswith("/check-runs") and request.method == "GET":
            return httpx.Response(200, json={"check_runs": []})
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(201, json={"id": 1})
        if "/git/trees/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": path, "type": "blob", "size": 1, "sha": "blob"}
                        for path in [
                            "a.py",
                            "b.ts",
                            "c.tsx",
                            "d.jsx",
                            "node_modules/e.js",
                            "skip.js",
                        ]
                    ]
                },
            )
        return httpx.Response(200, json={"encoding": "base64", "content": "eA=="})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        github = GitHubClient(Settings(github_api_url="https://github.test"), http)
        assert await github.get_repo_config("token", "o", "r", "base") is None
        await github.set_quality_check("token", "o", "r", "sha", "job", "failure", "75/80")
        sources = await github.list_sources(
            "token", "o", "r", "sha", RepoConfig(ignore_paths=["skip.js"])
        )
    assert {s.path for s in sources} == {"a.py", "b.ts", "c.tsx", "d.jsx"}
    import json

    check = next(json.loads(r.content) for r in calls if r.method == "POST")
    assert check["status"] == "completed" and check["conclusion"] == "failure"
