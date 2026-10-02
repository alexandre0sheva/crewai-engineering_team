from __future__ import annotations

from pathlib import Path

import pytest
from polyglot_repo import line_of, write_polyglot

from engineering_team.codeintel.imports import ImportGraph
from engineering_team.codeintel.index import SourceIndex
from engineering_team.codeintel.references import Reference, find_references
from engineering_team.codeintel.related import is_test_path, related_tests
from engineering_team.tools.workspace import ProjectWorkspace


@pytest.fixture
def index(tmp_path: Path) -> SourceIndex:
    workspace = ProjectWorkspace.create(tmp_path / "repo")
    write_polyglot(workspace.root)
    return SourceIndex.build(workspace)


def _kinds(references: list[Reference]) -> list[tuple[str, int, str]]:
    return [(ref.path, ref.line, ref.kind) for ref in references]


def test_references_are_classified_as_definition_import_call_or_other(
    index: SourceIndex,
) -> None:
    found = _kinds(find_references(index, "parse_config"))

    assert found == [
        ("app/cli.py", line_of("app/cli.py", "config.parse_config"), "call"),
        ("app/config.py", line_of("app/config.py", "def parse_config"), "definition"),
        ("app/main.py", 1, "import"),
        ("app/main.py", line_of("app/main.py", "settings = parse_config"), "call"),
        ("tests/test_config.py", 1, "import"),
        ("tests/test_config.py", line_of("tests/test_config.py", "assert parse_config"), "call"),
    ]  # ``test_parse_config`` is a different word and is not a reference


def test_typescript_and_go_references(index: SourceIndex) -> None:
    ts = _kinds(find_references(index, "fetchUser"))
    go = _kinds(find_references(index, "ParseConfig"))

    assert ("web/src/api.ts", line_of("web/src/api.ts", "function fetchUser"), "definition") in ts
    assert ("web/src/api.ts", line_of("web/src/api.ts", "return fetchUser"), "call") in ts
    assert ("web/src/ui.ts", 1, "import") in ts
    assert ("web/src/api.spec.ts", 1, "import") in ts
    assert (
        "web/src/api.spec.ts",
        line_of("web/src/api.spec.ts", 'describe("fetchUser"'),
        "other",
    ) in ts
    assert ("svc/server.go", line_of("svc/server.go", "func ParseConfig"), "definition") in go
    assert ("cmd/main.go", line_of("cmd/main.go", "svc.ParseConfig"), "call") in go
    assert ("svc/server_test.go", line_of("svc/server_test.go", 'ParseConfig("x")'), "call") in go


def test_comments_are_other_even_when_they_look_like_calls(tmp_path: Path) -> None:
    workspace = ProjectWorkspace.create(tmp_path / "repo")
    workspace.write_file("app/notes.py", "# parse_config(x) was removed\n")

    found = find_references(SourceIndex.build(workspace), "parse_config")

    assert [(ref.path, ref.kind) for ref in found if ref.path == "app/notes.py"] == [
        ("app/notes.py", "other")
    ]


def test_references_can_be_limited_to_a_directory(index: SourceIndex) -> None:
    found = find_references(index, "parse_config", path_prefix="tests")

    assert {ref.path for ref in found} == {"tests/test_config.py"}


def test_multiline_python_imports_count_as_imports(tmp_path: Path) -> None:
    workspace = ProjectWorkspace.create(tmp_path / "repo")
    workspace.write_file("m.py", "from app.config import (\n    other,\n    parse_config,\n)\n")

    found = find_references(SourceIndex.build(workspace), "parse_config")

    assert _kinds(found) == [("m.py", 3, "import")]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_config.py", True),
        ("pkg/config_test.py", True),
        ("web/src/api.spec.ts", True),
        ("web/src/api.test.tsx", True),
        ("svc/server_test.go", True),
        ("src/main/BillingTest.java", True),
        ("src/Shop.Tests/CartTests.cs", True),
        ("spec/shop_spec.rb", True),
        ("tests/helpers/util.py", True),
        ("tests/conftest.py", False),
        ("app/config.py", False),
        ("web/src/api.ts", False),
    ],
)
def test_test_files_are_recognised_by_name_and_location(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected


def test_related_tests_for_a_file_combine_imports_names_and_mentions(index: SourceIndex) -> None:
    graph = ImportGraph(index)

    python = related_tests(index, graph, "app/config.py")
    ts = related_tests(index, graph, "web/src/api.ts")
    go = related_tests(index, graph, "svc/server.go")

    assert [test.path for test in python] == ["tests/test_config.py"]
    assert python[0].reasons[0] == "imports app/config.py"
    assert "name matches" in python[0].reasons
    assert [test.path for test in ts] == ["web/src/api.spec.ts"]
    assert [test.path for test in go] == ["svc/server_test.go"]
    assert any(reason.startswith("mentions") for reason in go[0].reasons)


def test_related_tests_for_a_symbol_are_the_tests_that_mention_it(index: SourceIndex) -> None:
    graph = ImportGraph(index)

    found = related_tests(index, graph, "parse_config")

    assert [test.path for test in found] == ["tests/test_config.py"]
    assert found[0].reasons == ["mentions parse_config"]
    assert related_tests(index, graph, "no_such_symbol") == []
