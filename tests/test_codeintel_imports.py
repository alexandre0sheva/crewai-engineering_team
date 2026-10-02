from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from polyglot_repo import FILES

from engineering_team.codeintel.imports import ImportGraph
from engineering_team.codeintel.index import SourceIndex
from engineering_team.tools.workspace import ProjectWorkspace

MakeGraph = Callable[[Mapping[str, str]], ImportGraph]


@pytest.fixture
def make_graph(tmp_path: Path) -> MakeGraph:
    def build(files: Mapping[str, str]) -> ImportGraph:
        workspace = ProjectWorkspace.create(tmp_path / "graph")
        for relative, content in files.items():
            workspace.write_file(relative, content)
        return ImportGraph(SourceIndex.build(workspace))

    return build


def _targets(graph: ImportGraph, path: str) -> list[tuple[str, ...]]:
    return [ref.targets for ref in graph.imports_of(path)]


def _importers(graph: ImportGraph, path: str) -> list[str]:
    return sorted({importer for importer, _ in graph.importers_of(path)})


def test_python_imports_resolve_to_project_files(make_graph: MakeGraph) -> None:
    graph = make_graph(FILES)

    assert _targets(graph, "app/main.py") == [("app/config.py",)]
    assert _targets(graph, "app/cli.py") == [("app/config.py",)]  # a submodule, not __init__
    assert _importers(graph, "app/config.py") == [
        "app/cli.py",
        "app/main.py",
        "tests/test_config.py",
    ]
    assert _importers(graph, "app/cli.py") == ["tests/test_cli.py"]


def test_python_relative_absolute_and_external_imports(make_graph: MakeGraph) -> None:
    graph = make_graph(
        {
            "pkg/__init__.py": "",
            "pkg/a.py": (
                "from .b import x\nfrom . import c\nimport pkg.d\nimport os\nimport requests\n"
            ),
            "pkg/b.py": "x = 1\n",
            "pkg/c.py": "",
            "pkg/d.py": "",
            "src/lib/__init__.py": "",
            "src/lib/core.py": "VALUE = 1\n",
            "tool.py": "from lib.core import VALUE\n",
        }
    )

    assert _targets(graph, "pkg/a.py")[:3] == [("pkg/b.py",), ("pkg/c.py",), ("pkg/d.py",)]
    external = [ref.spec for ref in graph.imports_of("pkg/a.py") if not ref.targets]
    assert external == ["os", "requests"]
    assert _targets(graph, "tool.py") == [("src/lib/core.py",)]  # src/ is not a package


def test_typescript_imports_requires_and_dynamic_imports(make_graph: MakeGraph) -> None:
    graph = make_graph(
        {
            **FILES,
            "web/src/lazy.ts": 'export const load = () => import("./api");\n',
            "web/src/legacy.js": 'const api = require("./api");\nconst fs = require("fs");\n',
            "web/src/index.ts": 'export * from "./api";\n',
            "web/src/main.ts": 'import { render } from "./ui";\nimport "./index";\n',
            "web/src/multi.ts": 'import {\n  fetchUser,\n  formatUser,\n} from "./api";\n',
        }
    )

    assert _targets(graph, "web/src/ui.ts") == [("web/src/api.ts",)]
    assert _importers(graph, "web/src/api.ts") == [
        "web/src/api.spec.ts",
        "web/src/index.ts",
        "web/src/lazy.ts",
        "web/src/legacy.js",
        "web/src/multi.ts",
        "web/src/ui.ts",
    ]
    assert [ref.spec for ref in graph.imports_of("web/src/legacy.js") if not ref.targets] == ["fs"]
    assert _targets(graph, "web/src/main.ts") == [("web/src/ui.ts",), ("web/src/index.ts",)]
    assert graph.imports_of("web/src/multi.ts")[0].line == 1


def test_go_imports_resolve_a_package_to_its_files_via_go_mod(make_graph: MakeGraph) -> None:
    graph = make_graph(FILES)

    refs = graph.imports_of("cmd/main.go")
    assert [ref.targets for ref in refs if ref.targets] == [("svc/server.go",)]
    assert [ref.spec for ref in refs if not ref.targets] == ["fmt"]
    assert _importers(graph, "svc/server.go") == ["cmd/main.go"]


@pytest.mark.parametrize(
    ("files", "importer", "expected"),
    [
        (
            {
                "src/a/Billing.java": "package a;\npublic class Billing {}\n",
                "src/b/Shop.java": "package b;\nimport a.Billing;\nimport java.util.List;\n",
            },
            "src/b/Shop.java",
            ["src/a/Billing.java"],
        ),
        (
            {
                "Shop/Cart.cs": "namespace Shop.Core;\npublic class Cart {}\n",
                "Shop/Program.cs": "using System;\nusing Shop.Core;\n",
            },
            "Shop/Program.cs",
            ["Shop/Cart.cs"],
        ),
        (
            {
                "src/lib.rs": "mod util;\nmod net;\n",
                "src/util.rs": "pub fn f() {}\n",
                "src/net/mod.rs": "pub fn g() {}\n",
                "src/main.rs": "use crate::util::f;\n",
            },
            "src/lib.rs",
            ["src/util.rs", "src/net/mod.rs"],
        ),
        (
            {
                "src/main.rs": "use crate::util::f;\n",
                "src/util.rs": "pub fn f() {}\n",
            },
            "src/main.rs",
            ["src/util.rs"],
        ),
        (
            {
                "lib/shop.rb": "require_relative 'cart'\nrequire 'json'\n",
                "lib/cart.rb": "class Cart; end\n",
            },
            "lib/shop.rb",
            ["lib/cart.rb"],
        ),
        (
            {
                "src/Shop/Cart.php": "<?php\nnamespace Shop;\nclass Cart {}\n",
                "src/index.php": "<?php\nuse Shop\\Cart;\nrequire 'helpers.php';\n",
                "src/helpers.php": "<?php\n",
            },
            "src/index.php",
            ["src/Shop/Cart.php", "src/helpers.php"],
        ),
    ],
)
def test_other_languages_resolve_project_imports(
    make_graph: MakeGraph, files: dict[str, str], importer: str, expected: list[str]
) -> None:
    graph = make_graph(files)

    resolved = [target for ref in graph.imports_of(importer) for target in ref.targets]

    assert resolved == expected
    for target in expected:
        assert importer in _importers(graph, target)


def test_an_unknown_file_has_no_imports(make_graph: MakeGraph) -> None:
    graph = make_graph(FILES)

    assert graph.imports_of("missing.py") == []
    assert graph.importers_of("missing.py") == []
