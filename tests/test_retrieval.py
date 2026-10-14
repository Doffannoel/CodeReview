import pytest

from codereviewer.diff import parse_unified_diff
from codereviewer.graph import GraphContext, GraphSymbol
from codereviewer.retrieval import (
    ContextItem,
    GraphRetriever,
    score_context,
    select_with_budget,
)


def item(symbol_id: str, score: float, tokens: int) -> ContextItem:
    return ContextItem(
        symbol_id=symbol_id,
        path=f"{symbol_id}.py",
        qualified_name=symbol_id,
        kind="function",
        content="x",
        relation="CALLS",
        distance=1,
        score=score,
        token_count=tokens,
    )


def test_direct_relationship_scores_above_two_hop() -> None:
    direct = GraphContext("a", "a.py", "a", "function", "def a", "x", "CALLS", 1)
    indirect = GraphContext("b", "b.py", "b", "function", "def b", "x", "CALLS", 2)
    assert score_context(direct) > score_context(indirect)


def test_budget_prefers_high_score_and_deduplicates() -> None:
    selected = select_with_budget(
        [item("high", 0.9, 6), item("low", 0.2, 4), item("high", 0.8, 2)],
        token_budget=10,
    )
    assert [value.symbol_id for value in selected] == ["high", "low"]
    assert all(value.selected for value in selected)


class FakeGraphStore:
    async def changed_symbols(self, *_: object) -> list[GraphSymbol]:
        return [
            GraphSymbol(
                symbol_id="repo:app.py:run",
                version_id="sha:app.py:run",
                path="app.py",
                qualified_name="run",
                kind="function",
                signature="def run()",
                content="def run():\n    new()",
                start_line=1,
                end_line=2,
            )
        ]

    async def retrieve_context(self, *_: object) -> list[GraphContext]:
        return []


@pytest.mark.asyncio
async def test_changed_symbol_is_always_a_context_candidate() -> None:
    diff = parse_unified_diff(
        """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def run():
-    old()
+    new()
"""
    )
    retriever = GraphRetriever(FakeGraphStore(), max_hops=2, max_nodes_per_symbol=10)  # type: ignore[arg-type]

    candidates, selected = await retriever.retrieve(1, "sha", diff, token_budget=100)

    assert candidates[0].relation == "CHANGED_SYMBOL"
    assert candidates[0].score == 1.0
    assert selected[0].symbol_id == "repo:app.py:run"
