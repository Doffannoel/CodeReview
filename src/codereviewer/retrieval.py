from __future__ import annotations

from dataclasses import dataclass

from codereviewer.diff import PullRequestDiff
from codereviewer.graph import GraphContext, GraphStore

RELATION_SCORE = {
    "IMPLEMENTS": 0.95,
    "CALLS": 0.90,
    "TESTS": 0.85,
    "EXTENDS": 0.82,
    "REFERENCES": 0.70,
}


@dataclass(frozen=True)
class ContextItem:
    symbol_id: str
    path: str
    qualified_name: str
    kind: str
    content: str
    relation: str
    distance: int
    score: float
    token_count: int
    selected: bool = False


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def score_context(context: GraphContext) -> float:
    relations = context.relation.split("->")
    base = max((RELATION_SCORE.get(relation, 0.55) for relation in relations), default=0.55)
    return max(0.1, base - (context.distance - 1) * 0.18)


def select_with_budget(items: list[ContextItem], token_budget: int) -> list[ContextItem]:
    selected: list[ContextItem] = []
    consumed = 0
    seen: set[str] = set()
    for item in sorted(items, key=lambda value: (-value.score, value.token_count)):
        if item.symbol_id in seen or consumed + item.token_count > token_budget:
            continue
        selected.append(ContextItem(**{**item.__dict__, "selected": True}))
        consumed += item.token_count
        seen.add(item.symbol_id)
    return selected


class GraphRetriever:
    def __init__(self, store: GraphStore, max_hops: int, max_nodes_per_symbol: int) -> None:
        self.store = store
        self.max_hops = max_hops
        self.max_nodes_per_symbol = max_nodes_per_symbol

    async def retrieve(
        self,
        repository_id: int,
        sha: str,
        diff: PullRequestDiff,
        token_budget: int,
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        changed_lines = {file.path: file.new_changed_lines for file in diff.files}
        seeds = await self.store.changed_symbols(repository_id, sha, changed_lines)
        contexts = await self.store.retrieve_context(
            repository_id,
            sha,
            [seed.version_id for seed in seeds],
            self.max_hops,
            max(1, len(seeds)) * self.max_nodes_per_symbol,
        )
        seed_candidates = [
            ContextItem(
                symbol_id=seed.symbol_id,
                path=seed.path,
                qualified_name=seed.qualified_name,
                kind=seed.kind,
                content=seed.content,
                relation="CHANGED_SYMBOL",
                distance=0,
                score=1.0,
                token_count=estimate_tokens(seed.content),
            )
            for seed in seeds
        ]
        related_candidates = [
            ContextItem(
                symbol_id=context.symbol_id,
                path=context.path,
                qualified_name=context.qualified_name,
                kind=context.kind,
                content=context.content,
                relation=context.relation,
                distance=context.distance,
                score=score_context(context),
                token_count=estimate_tokens(context.content),
            )
            for context in contexts
        ]
        candidates = [*seed_candidates, *related_candidates]
        return candidates, select_with_budget(candidates, token_budget)
