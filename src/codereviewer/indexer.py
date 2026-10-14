from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field

from codereviewer.github import RepositorySource


@dataclass(frozen=True)
class SymbolReference:
    relation: str
    target_name: str


@dataclass
class ParsedSymbol:
    stable_id: str
    path: str
    qualified_name: str
    name: str
    kind: str
    signature: str
    content: str
    start_line: int
    end_line: int
    references: list[SymbolReference] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedRelation:
    source_id: str
    target_id: str
    relation: str


@dataclass
class ParsedRepository:
    files: list[RepositorySource]
    symbols: list[ParsedSymbol]
    relations: list[ParsedRelation]


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self, path: str, source: str) -> None:
        self.path = path
        self.source = source
        self.lines = source.splitlines()
        self.symbols: list[ParsedSymbol] = []
        self.scope: list[str] = []
        self.current: list[ParsedSymbol] = []

    def _source_segment(self, node: ast.AST) -> str:
        segment = ast.get_source_segment(self.source, node)
        if segment:
            return segment
        start = max(int(getattr(node, "lineno", 1)) - 1, 0)
        end = int(getattr(node, "end_lineno", start + 1))
        return "\n".join(self.lines[start:end])

    def _signature(self, node: ast.AST) -> str:
        first_line = self._source_segment(node).splitlines()[0]
        return first_line.rstrip(":").strip()

    def _new_symbol(self, node: ast.AST, name: str, kind: str) -> ParsedSymbol:
        qualified = ".".join([*self.scope, name])
        return ParsedSymbol(
            stable_id=f"{self.path}:{qualified}",
            path=self.path,
            qualified_name=qualified,
            name=name,
            kind=kind,
            signature=self._signature(node),
            content=self._source_segment(node),
            start_line=int(getattr(node, "lineno", 1)),
            end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        is_protocol = any(
            (name := dotted_name(base)) and name.split(".")[-1] in {"Protocol", "Interface"}
            for base in node.bases
        )
        symbol = self._new_symbol(node, node.name, "interface" if is_protocol else "class")
        for base in node.bases:
            name = dotted_name(base)
            if name:
                relation = (
                    "IMPLEMENTS"
                    if name.split(".")[-1].endswith(("Protocol", "Interface"))
                    else "EXTENDS"
                )
                symbol.references.append(SymbolReference(relation, name))
        self.symbols.append(symbol)
        self.scope.append(node.name)
        self.current.append(symbol)
        self.generic_visit(node)
        self.current.pop()
        self.scope.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = "test" if node.name.startswith("test_") else "method" if self.scope else "function"
        symbol = self._new_symbol(node, node.name, kind)
        self.symbols.append(symbol)
        self.scope.append(node.name)
        self.current.append(symbol)
        self.generic_visit(node)
        self.current.pop()
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.current:
            target = dotted_name(node.func)
            if target:
                relation = "TESTS" if self.current[-1].kind == "test" else "CALLS"
                self.current[-1].references.append(SymbolReference(relation, target))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if self.current and isinstance(node.ctx, ast.Load):
            self.current[-1].references.append(SymbolReference("REFERENCES", node.id))


class PythonAstIndexer:
    def parse_file(self, source: RepositorySource) -> list[ParsedSymbol]:
        try:
            tree = ast.parse(source.content, filename=source.path)
        except SyntaxError:
            return []
        visitor = _SymbolVisitor(source.path, source.content)
        visitor.visit(tree)
        return visitor.symbols

    def parse_repository(self, sources: list[RepositorySource]) -> ParsedRepository:
        symbols = [symbol for source in sources for symbol in self.parse_file(source)]
        by_leaf: dict[str, list[ParsedSymbol]] = {}
        for symbol in symbols:
            by_leaf.setdefault(symbol.name, []).append(symbol)

        relations: set[tuple[str, str, str]] = set()
        for symbol in symbols:
            for reference in symbol.references:
                leaf = reference.target_name.split(".")[-1]
                matches = [candidate for candidate in by_leaf.get(leaf, []) if candidate != symbol]
                if len(matches) == 1:
                    relations.add((symbol.stable_id, matches[0].stable_id, reference.relation))
                    continue
                same_file = [candidate for candidate in matches if candidate.path == symbol.path]
                if len(same_file) == 1:
                    relations.add((symbol.stable_id, same_file[0].stable_id, reference.relation))

        return ParsedRepository(
            files=sources,
            symbols=symbols,
            relations=[ParsedRelation(*relation) for relation in sorted(relations)],
        )
