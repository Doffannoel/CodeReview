from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

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


def node_text(node: Node | None) -> str:
    return node.text.decode("utf-8") if node and node.text else ""


def walk(node: Node):
    yield node
    for child in node.named_children:
        yield from walk(child)


class TreeSitterIndexer:
    def __init__(self) -> None:
        self.parsers = {
            "python": Parser(Language(tree_sitter_python.language())),
            "javascript": Parser(Language(tree_sitter_javascript.language())),
            "typescript": Parser(Language(tree_sitter_typescript.language_typescript())),
            "tsx": Parser(Language(tree_sitter_typescript.language_tsx())),
        }
        self.imports: dict[str, dict[str, tuple[str, str]]] = {}
        self.defaults: dict[str, str] = {}

    def parse_file(self, source: RepositorySource) -> list[ParsedSymbol]:
        suffix = PurePosixPath(source.path).suffix
        language = (
            "python"
            if suffix == ".py"
            else "tsx"
            if suffix == ".tsx"
            else "typescript"
            if suffix in {".ts", ".mts", ".cts"}
            else "javascript"
        )
        root = self.parsers[language].parse(source.content.encode("utf-8")).root_node
        if root.has_error:
            return []
        aliases: dict[str, tuple[str, str]] = {}
        for node in walk(root):
            if (
                node.type == "assignment_expression"
                and node_text(node.child_by_field_name("left")) == "module.exports"
            ):
                value = node.child_by_field_name("right")
                self.defaults[source.path] = (
                    node_text(value) if value and value.type == "identifier" else "exports"
                )
            if node.type == "export_statement" and any(c.type == "default" for c in node.children):
                exported = node.child_by_field_name("declaration") or node.child_by_field_name(
                    "value"
                )
                if exported:
                    self.defaults[source.path] = node_text(
                        exported.child_by_field_name("name")
                    ) or (
                        node_text(exported)
                        if exported.type == "identifier"
                        else (
                            f"callback@{exported.start_point.row + 1}:{exported.start_point.column}"
                        )
                    )
            if node.type == "variable_declarator":
                value = node.child_by_field_name("value")
                name_node = node.child_by_field_name("name")
                if (
                    value
                    and value.type == "call_expression"
                    and node_text(value.child_by_field_name("function")) == "require"
                ):
                    args = value.child_by_field_name("arguments")
                    if (
                        args
                        and len(args.named_children) == 1
                        and args.named_children[0].type == "string"
                    ):
                        module = node_text(args.named_children[0]).strip("'\"")
                        if name_node.type == "identifier":
                            aliases[node_text(name_node)] = (module, "*")
                        elif name_node.type == "object_pattern":
                            for child in name_node.named_children:
                                imported = node_text(child.child_by_field_name("key")) or node_text(
                                    child
                                )
                                alias = node_text(child.child_by_field_name("value")) or imported
                                aliases[alias] = (module, imported)
            if node.type == "import_statement" and language != "python":
                module = node_text(node.child_by_field_name("source")).strip("'\"")
                clause = next((n for n in node.named_children if n.type == "import_clause"), None)
                if clause:
                    for child in clause.named_children:
                        if child.type == "identifier":
                            aliases[node_text(child)] = (module, "default")
                        for spec in walk(child):
                            if spec.type == "import_specifier":
                                name = node_text(spec.child_by_field_name("name"))
                                alias = node_text(spec.child_by_field_name("alias")) or name
                                aliases[alias] = (module, name)
                            elif spec.type == "namespace_import":
                                aliases[node_text(spec.named_children[-1])] = (module, "*")
            elif node.type == "import_from_statement":
                module = node_text(node.child_by_field_name("module_name"))
                for child in node.children_by_field_name("name"):
                    name = node_text(child.child_by_field_name("name")) or node_text(child)
                    alias = node_text(child.child_by_field_name("alias")) or name
                    aliases[alias] = (module, name)
            elif node.type == "import_statement" and language == "python":
                for child in node.named_children:
                    name = node_text(child.child_by_field_name("name")) or node_text(child)
                    alias = node_text(child.child_by_field_name("alias")) or name
                    aliases[alias] = (name, "*")
        self.imports[source.path] = aliases
        symbols: list[ParsedSymbol] = []
        classes = {
            "class_definition",
            "class_declaration",
            "abstract_class_declaration",
            "interface_declaration",
        }
        functions = {
            "function_definition",
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "arrow_function",
            "function_expression",
            "function",
        }

        def visit(node: Node, scope: list[str], current: ParsedSymbol | None) -> None:
            name = node_text(node.child_by_field_name("name"))
            if node.type in functions and not name:
                parent = node.parent
                if parent and parent.type in {
                    "variable_declarator",
                    "pair",
                    "public_field_definition",
                }:
                    name = node_text(
                        parent.child_by_field_name("name") or parent.child_by_field_name("key")
                    )
                elif parent and parent.type == "assignment_expression":
                    name = node_text(parent.child_by_field_name("left")).split(".")[-1]
                else:
                    name = f"callback@{node.start_point.row + 1}:{node.start_point.column}"
            if node.type in classes | functions:
                kind = (
                    "interface"
                    if node.type == "interface_declaration"
                    else "class"
                    if node.type in classes
                    else "method"
                    if scope
                    else "function"
                )
                if kind not in {"class", "interface"} and (
                    name.startswith("test_")
                    or any(
                        part in {"tests", "__tests__"} for part in PurePosixPath(source.path).parts
                    )
                    or ".test." in source.path
                    or ".spec." in source.path
                ):
                    kind = "test"
                qualified = ".".join([*scope, name])
                body = node.child_by_field_name("body")
                signature = source.content.encode()[
                    node.start_byte : body.start_byte if body else node.end_byte
                ]
                symbol = ParsedSymbol(
                    f"{source.path}:{qualified}",
                    source.path,
                    qualified,
                    name,
                    kind,
                    signature.decode().strip().rstrip(":"),
                    node_text(node),
                    node.start_point.row + 1,
                    node.end_point.row + 1,
                )
                symbols.append(symbol)
                if node.type in classes:
                    bases = node.child_by_field_name("superclasses")
                    if bases:
                        for base in bases.named_children:
                            symbol.references.append(SymbolReference("EXTENDS", node_text(base)))
                    for child in node.named_children:
                        if child.type == "class_heritage":
                            for base in child.named_children:
                                if base.type in {"extends_clause", "implements_clause"}:
                                    relation = (
                                        "EXTENDS" if base.type == "extends_clause" else "IMPLEMENTS"
                                    )
                                    for target in base.named_children:
                                        symbol.references.append(
                                            SymbolReference(relation, node_text(target))
                                        )
                                else:
                                    symbol.references.append(
                                        SymbolReference("EXTENDS", node_text(base))
                                    )
                scope = [*scope, name]
                current = symbol
            if current and node.type in {"call", "call_expression", "new_expression"}:
                target = node_text(
                    node.child_by_field_name("function") or node.child_by_field_name("constructor")
                )
                if target:
                    current.references.append(
                        SymbolReference(
                            "TESTS" if current.kind == "test" else "CALLS",
                            target,
                        )
                    )
            for child in node.named_children:
                visit(child, scope, current)

        module_symbol = ParsedSymbol(
            f"{source.path}:<module>",
            source.path,
            "<module>",
            "<module>",
            "module",
            source.path,
            source.content,
            1,
            max(1, len(source.content.splitlines())),
        )
        visit(root, [], module_symbol)
        if module_symbol.references or not symbols:
            symbols.append(module_symbol)
        return symbols

    def parse_repository(self, sources: list[RepositorySource]) -> ParsedRepository:
        self.imports.clear()
        self.defaults.clear()
        symbols = []
        for source in sources:
            parsed = self.parse_file(source)
            if not parsed:
                raise ValueError(f"Tree-sitter could not parse {source.path}")
            symbols.extend(parsed)
        by_leaf: dict[str, list[ParsedSymbol]] = {}
        for symbol in symbols:
            by_leaf.setdefault(symbol.name, []).append(symbol)
        paths = {source.path for source in sources}
        relations: set[tuple[str, str, str]] = set()
        for symbol in symbols:
            for reference in symbol.references:
                parts = reference.target_name.split(".")
                binding = self.imports.get(symbol.path, {}).get(parts[0])
                matches = []
                if binding:
                    module, imported = binding
                    if symbol.path.endswith(".py"):
                        depth = len(module) - len(module.lstrip("."))
                        module_path = module.lstrip(".").replace(".", "/")
                        if depth:
                            module_path = posixpath.join(
                                posixpath.dirname(symbol.path), *([".."] * (depth - 1)), module_path
                            )
                    else:
                        module_path = posixpath.join(posixpath.dirname(symbol.path), module)
                    module_path = posixpath.normpath(module_path)
                    resolved = {
                        p
                        for p in paths
                        if p == module_path
                        or str(PurePosixPath(p).with_suffix("")) == module_path
                        or str(PurePosixPath(p).with_suffix(""))
                        in {f"{module_path}/index", f"{module_path}/__init__"}
                    }
                    target = (
                        "default"
                        if imported == "*" and len(parts) == 1
                        else parts[-1]
                        if imported == "*" or len(parts) > 1
                        else imported
                    )
                    matches = [
                        s
                        for s in symbols
                        if s.path in resolved
                        and (
                            s.name == target
                            or (target == "default" and s.name == self.defaults.get(s.path))
                        )
                    ]
                else:
                    candidates = [s for s in by_leaf.get(parts[-1], []) if s != symbol]
                    local = [s for s in candidates if s.path == symbol.path]
                    # ponytail: unresolved receivers use unique names; add type resolution
                    # when overloaded methods or dynamic imports need exact call graphs.
                    matches = local or candidates
                if len(matches) == 1:
                    relations.add((symbol.stable_id, matches[0].stable_id, reference.relation))
        return ParsedRepository(sources, symbols, [ParsedRelation(*r) for r in sorted(relations)])


PythonAstIndexer = TreeSitterIndexer
