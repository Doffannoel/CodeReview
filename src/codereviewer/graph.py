from __future__ import annotations

from dataclasses import dataclass

from neo4j import AsyncDriver, AsyncGraphDatabase

from codereviewer.indexer import ParsedRepository, content_hash

ALLOWED_RELATIONSHIPS = {"CALLS", "IMPLEMENTS", "EXTENDS", "REFERENCES", "TESTS"}


@dataclass(frozen=True)
class GraphSymbol:
    symbol_id: str
    version_id: str
    path: str
    qualified_name: str
    kind: str
    signature: str
    content: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class GraphContext:
    symbol_id: str
    path: str
    qualified_name: str
    kind: str
    signature: str
    content: str
    relation: str
    distance: int


class GraphStore:
    def __init__(self, uri: str, username: str, password: str) -> None:
        self.driver: AsyncDriver = AsyncGraphDatabase.driver(uri, auth=(username, password))

    async def close(self) -> None:
        await self.driver.close()

    async def verify(self) -> None:
        await self.driver.verify_connectivity()

    async def ensure_schema(self) -> None:
        statements = [
            "CREATE CONSTRAINT repository_id IF NOT EXISTS "
            "FOR (n:Repository) REQUIRE n.repository_id IS UNIQUE",
            "CREATE CONSTRAINT snapshot_id IF NOT EXISTS "
            "FOR (n:Snapshot) REQUIRE n.snapshot_id IS UNIQUE",
            "CREATE CONSTRAINT file_stable_id IF NOT EXISTS "
            "FOR (n:File) REQUIRE n.stable_id IS UNIQUE",
            "CREATE CONSTRAINT file_version_id IF NOT EXISTS "
            "FOR (n:FileVersion) REQUIRE n.version_id IS UNIQUE",
            "CREATE CONSTRAINT symbol_stable_id IF NOT EXISTS "
            "FOR (n:Symbol) REQUIRE n.stable_id IS UNIQUE",
            "CREATE CONSTRAINT symbol_version_id IF NOT EXISTS "
            "FOR (n:SymbolVersion) REQUIRE n.version_id IS UNIQUE",
        ]
        async with self.driver.session() as session:
            for statement in statements:
                await session.run(statement)

    @staticmethod
    def snapshot_id(repository_id: int, sha: str) -> str:
        return f"{repository_id}:{sha}"

    async def has_snapshot(self, repository_id: int, sha: str) -> bool:
        async with self.driver.session() as session:
            result = await session.run(
                "MATCH (s:Snapshot {snapshot_id: $snapshot_id, status: 'ready'}) "
                "RETURN count(s) > 0 AS found",
                snapshot_id=self.snapshot_id(repository_id, sha),
            )
            record = await result.single()
            return bool(record and record["found"])

    async def store_snapshot(
        self,
        repository_id: int,
        full_name: str,
        sha: str,
        parsed: ParsedRepository,
    ) -> None:
        snapshot_id = self.snapshot_id(repository_id, sha)
        files = [
            {
                "stable_id": f"{repository_id}:{source.path}",
                "version_id": f"{snapshot_id}:{source.path}",
                "path": source.path,
                "content_hash": content_hash(source.content),
            }
            for source in parsed.files
        ]
        symbols = [
            {
                "stable_id": f"{repository_id}:{symbol.stable_id}",
                "version_id": f"{snapshot_id}:{symbol.stable_id}",
                "file_version_id": f"{snapshot_id}:{symbol.path}",
                "path": symbol.path,
                "qualified_name": symbol.qualified_name,
                "name": symbol.name,
                "kind": symbol.kind,
                "signature": symbol.signature,
                "content": symbol.content,
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
                "content_hash": content_hash(symbol.content),
            }
            for symbol in parsed.symbols
        ]

        async with self.driver.session() as session:
            await session.run(
                """
                MERGE (r:Repository {repository_id: $repository_id})
                SET r.full_name = $full_name
                MERGE (s:Snapshot {snapshot_id: $snapshot_id})
                SET s.repository_id = $repository_id, s.sha = $sha, s.status = 'building'
                MERGE (r)-[:HAS_SNAPSHOT]->(s)
                """,
                repository_id=repository_id,
                full_name=full_name,
                snapshot_id=snapshot_id,
                sha=sha,
            )
            await session.run(
                """
                MATCH (snapshot:Snapshot {snapshot_id: $snapshot_id})
                UNWIND $files AS file
                MERGE (stable:File {stable_id: file.stable_id})
                SET stable.repository_id = $repository_id, stable.path = file.path
                MERGE (version:FileVersion {version_id: file.version_id})
                SET version.repository_id = $repository_id,
                    version.snapshot_sha = $sha,
                    version.path = file.path,
                    version.content_hash = file.content_hash
                MERGE (stable)-[:HAS_VERSION]->(version)
                MERGE (snapshot)-[:CONTAINS]->(version)
                """,
                snapshot_id=snapshot_id,
                repository_id=repository_id,
                sha=sha,
                files=files,
            )
            await session.run(
                """
                MATCH (snapshot:Snapshot {snapshot_id: $snapshot_id})
                UNWIND $symbols AS symbol
                MATCH (file:FileVersion {version_id: symbol.file_version_id})
                MERGE (stable:Symbol {stable_id: symbol.stable_id})
                SET stable.repository_id = $repository_id,
                    stable.path = symbol.path,
                    stable.qualified_name = symbol.qualified_name,
                    stable.name = symbol.name,
                    stable.kind = symbol.kind
                MERGE (version:SymbolVersion {version_id: symbol.version_id})
                SET version.repository_id = $repository_id,
                    version.snapshot_sha = $sha,
                    version.symbol_id = symbol.stable_id,
                    version.path = symbol.path,
                    version.qualified_name = symbol.qualified_name,
                    version.name = symbol.name,
                    version.kind = symbol.kind,
                    version.signature = symbol.signature,
                    version.content = symbol.content,
                    version.start_line = symbol.start_line,
                    version.end_line = symbol.end_line,
                    version.content_hash = symbol.content_hash
                MERGE (stable)-[:HAS_VERSION]->(version)
                MERGE (snapshot)-[:CONTAINS]->(version)
                MERGE (file)-[:DECLARES]->(version)
                """,
                snapshot_id=snapshot_id,
                repository_id=repository_id,
                sha=sha,
                symbols=symbols,
            )

            for relation_type in ALLOWED_RELATIONSHIPS:
                relationships = [
                    {
                        "source": f"{snapshot_id}:{relation.source_id}",
                        "target": f"{snapshot_id}:{relation.target_id}",
                    }
                    for relation in parsed.relations
                    if relation.relation == relation_type
                ]
                if not relationships:
                    continue
                await session.run(
                    f"""
                    UNWIND $relationships AS relation
                    MATCH (source:SymbolVersion {{version_id: relation.source}})
                    MATCH (target:SymbolVersion {{version_id: relation.target}})
                    MERGE (source)-[:{relation_type}]->(target)
                    """,
                    relationships=relationships,
                )
            await session.run(
                "MATCH (s:Snapshot {snapshot_id: $snapshot_id}) SET s.status = 'ready'",
                snapshot_id=snapshot_id,
            )

    async def changed_symbols(
        self,
        repository_id: int,
        sha: str,
        changed_lines: dict[str, set[int]],
    ) -> list[GraphSymbol]:
        rows = [{"path": path, "lines": sorted(lines)} for path, lines in changed_lines.items()]
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (snapshot:Snapshot {snapshot_id: $snapshot_id})-[:CONTAINS]->
                      (symbol:SymbolVersion)
                UNWIND $changes AS change
                WITH symbol, change
                WHERE symbol.path = change.path
                  AND any(line IN change.lines
                          WHERE line >= symbol.start_line AND line <= symbol.end_line)
                RETURN DISTINCT symbol
                """,
                snapshot_id=self.snapshot_id(repository_id, sha),
                changes=rows,
            )
            records = await result.data()
            matched_paths = {dict(record["symbol"])["path"] for record in records}
            missing_paths = [row["path"] for row in rows if row["path"] not in matched_paths]
            if missing_paths:
                fallback = await session.run(
                    """
                    MATCH (snapshot:Snapshot {snapshot_id: $snapshot_id})-[:CONTAINS]->
                          (symbol:SymbolVersion)
                    WHERE symbol.path IN $paths
                    RETURN DISTINCT symbol
                    ORDER BY (symbol.end_line - symbol.start_line) ASC
                    """,
                    snapshot_id=self.snapshot_id(repository_id, sha),
                    paths=missing_paths,
                )
                fallback_records = await fallback.data()
                per_path_count: dict[str, int] = {}
                for record in fallback_records:
                    path = dict(record["symbol"])["path"]
                    if per_path_count.get(path, 0) < 3:
                        records.append(record)
                        per_path_count[path] = per_path_count.get(path, 0) + 1
        symbols = []
        for record in records:
            node = dict(record["symbol"])
            symbols.append(
                GraphSymbol(
                    symbol_id=node["symbol_id"],
                    version_id=node["version_id"],
                    path=node["path"],
                    qualified_name=node["qualified_name"],
                    kind=node["kind"],
                    signature=node["signature"],
                    content=node["content"],
                    start_line=int(node["start_line"]),
                    end_line=int(node["end_line"]),
                )
            )
        return symbols

    async def retrieve_context(
        self,
        repository_id: int,
        sha: str,
        seed_version_ids: list[str],
        max_hops: int,
        limit: int,
    ) -> list[GraphContext]:
        if not seed_version_ids:
            return []
        if max_hops not in {1, 2}:
            raise ValueError("max_hops must be 1 or 2")
        query = f"""
            MATCH (seed:SymbolVersion)
            WHERE seed.version_id IN $seed_ids
            MATCH path = (seed)-[:CALLS|IMPLEMENTS|EXTENDS|REFERENCES|TESTS*1..{max_hops}]-(related)
            WHERE related:SymbolVersion
              AND related.repository_id = $repository_id
              AND related.snapshot_sha = $sha
              AND NOT related.version_id IN $seed_ids
            RETURN DISTINCT related,
                   [rel IN relationships(path) | type(rel)] AS relations,
                   length(path) AS distance
            ORDER BY distance ASC
            LIMIT $limit
        """
        async with self.driver.session() as session:
            result = await session.run(
                query,
                seed_ids=seed_version_ids,
                repository_id=repository_id,
                sha=sha,
                limit=limit,
            )
            records = await result.data()

        contexts = []
        for record in records:
            node = dict(record["related"])
            contexts.append(
                GraphContext(
                    symbol_id=node["symbol_id"],
                    path=node["path"],
                    qualified_name=node["qualified_name"],
                    kind=node["kind"],
                    signature=node["signature"],
                    content=node["content"],
                    relation="->".join(record["relations"]),
                    distance=int(record["distance"]),
                )
            )
        return contexts
