from codereviewer.github import RepositorySource
from codereviewer.indexer import PythonAstIndexer


def test_python_ast_indexer_builds_structural_relations() -> None:
    sources = [
        RepositorySource(
            "app/service.py",
            """class BaseService:
    pass

class Repository:
    def save(self):
        return True

class PaymentService(BaseService):
    def create(self):
        repository = Repository()
        return repository.save()
""",
        ),
        RepositorySource(
            "tests/test_service.py",
            """def test_create():
    service = PaymentService()
    assert service.create()
""",
        ),
    ]

    parsed = PythonAstIndexer().parse_repository(sources)
    symbols = {symbol.qualified_name for symbol in parsed.symbols}
    relations = {
        (relation.source_id, relation.target_id, relation.relation) for relation in parsed.relations
    }

    assert "PaymentService.create" in symbols
    assert (
        "app/service.py:PaymentService",
        "app/service.py:BaseService",
        "EXTENDS",
    ) in relations
    assert (
        "app/service.py:PaymentService.create",
        "app/service.py:Repository.save",
        "CALLS",
    ) in relations
    assert any(relation == "TESTS" for _, _, relation in relations)


def test_syntax_error_is_skipped() -> None:
    source = RepositorySource("broken.py", "def broken(:")
    assert PythonAstIndexer().parse_file(source) == []
