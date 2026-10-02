from __future__ import annotations

import json
from pathlib import Path

from polyglot_repo import write_polyglot

from engineering_team.codeintel import dependencies as deps
from engineering_team.codeintel.dependencies import Dependency, inspect_dependencies
from engineering_team.tools.workspace import ProjectWorkspace


def _summary(found: list[Dependency]) -> list[tuple[str, str, str]]:
    return [(d.name, d.declared, d.kind) for d in found]


def test_pyproject_reads_project_extras_groups_and_poetry_tables() -> None:
    text = """
[project]
dependencies = ["requests>=2.31", "Pydantic[email]==2.7.1 ; python_version>'3.9'", "rich"]
[project.optional-dependencies]
cli = ["click>=8"]
[dependency-groups]
dev = ["pytest>=8", {include-group = "lint"}]
lint = ["ruff"]
[tool.poetry.dependencies]
python = "^3.11"
httpx = "^0.27"
flask = {version = "^3.0", extras = ["async"]}
[tool.poetry.group.test.dependencies]
hypothesis = "^6"
"""

    assert _summary(deps.parse_pyproject(text)) == [
        ("requests", ">=2.31", "runtime"),
        ("Pydantic", "==2.7.1", "runtime"),
        ("rich", "*", "runtime"),
        ("click", ">=8", "optional: cli"),
        ("pytest", ">=8", "group: dev"),
        ("ruff", "*", "group: lint"),
        ("httpx", "^0.27", "runtime"),
        ("flask", "^3.0", "runtime"),
        ("hypothesis", "^6", "group: test"),
    ]


def test_requirements_files_skip_options_comments_and_includes() -> None:
    text = """# pinned
-r base.txt
--index-url https://example.invalid/simple
requests==2.31.0  # http
numpy>=1.26,<3
-e .
git+https://example.invalid/x.git#egg=x
mylib @ https://example.invalid/mylib.zip
"""

    assert _summary(deps.parse_requirements(text)) == [
        ("requests", "==2.31.0", "runtime"),
        ("numpy", ">=1.26,<3", "runtime"),
        ("mylib", "@ https://example.invalid/mylib.zip", "runtime"),
    ]


def test_package_json_reads_every_dependency_table() -> None:
    text = json.dumps(
        {
            "dependencies": {"react": "18.2.0"},
            "devDependencies": {"typescript": "~5.4.0"},
            "peerDependencies": {"react-dom": ">=18"},
            "optionalDependencies": {"fsevents": "^2"},
        }
    )

    assert _summary(deps.parse_package_json(text)) == [
        ("react", "18.2.0", "runtime"),
        ("typescript", "~5.4.0", "dev"),
        ("react-dom", ">=18", "peer"),
        ("fsevents", "^2", "optional"),
    ]


def test_go_mod_marks_indirect_requirements() -> None:
    text = (
        "module x\n\ngo 1.22\n\nrequire github.com/a/b v1.2.3\n\n"
        "require (\n\tgolang.org/x/text v0.14.0 // indirect\n\tgithub.com/c/d v0.1.0\n)\n"
    )

    assert _summary(deps.parse_go_mod(text)) == [
        ("github.com/a/b", "v1.2.3", "runtime"),
        ("golang.org/x/text", "v0.14.0", "indirect"),
        ("github.com/c/d", "v0.1.0", "runtime"),
    ]


def test_cargo_toml_reads_strings_tables_and_dev_dependencies() -> None:
    text = """
[dependencies]
serde = "1.0"
tokio = { version = "1", features = ["full"] }
local = { path = "../local" }
[dev-dependencies]
insta = "1.3"
[build-dependencies]
cc = "1"
"""

    assert _summary(deps.parse_cargo_toml(text)) == [
        ("serde", "1.0", "runtime"),
        ("tokio", "1", "runtime"),
        ("local", "path ../local", "runtime"),
        ("insta", "1.3", "dev"),
        ("cc", "1", "build"),
    ]


def test_pom_xml_reads_group_artifact_version_and_scope() -> None:
    text = """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <dependencies>
    <dependency><groupId>org.slf4j</groupId><artifactId>slf4j-api</artifactId><version>2.0.9</version></dependency>
    <dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>${junit.version}</version><scope>test</scope></dependency>
  </dependencies>
</project>"""

    assert _summary(deps.parse_pom(text)) == [
        ("org.slf4j:slf4j-api", "2.0.9", "runtime"),
        ("junit:junit", "${junit.version}", "test"),
    ]


def test_malformed_manifests_yield_nothing_instead_of_raising() -> None:
    assert deps.parse_pyproject("[project") == []
    assert deps.parse_package_json("{") == []
    assert deps.parse_pom("<project>") == []
    assert deps.parse_cargo_toml("= =") == []


def test_inspect_reads_manifests_below_a_path_with_locked_versions(tmp_path: Path) -> None:
    workspace = ProjectWorkspace.create(tmp_path / "repo")
    write_polyglot(workspace.root)
    workspace.write_file(
        "uv.lock",
        '[[package]]\nname = "requests"\nversion = "2.32.3"\n\n'
        '[[package]]\nname = "pydantic"\nversion = "2.7.1"\n',
    )
    workspace.write_file(
        "web/package-lock.json",
        json.dumps({"packages": {"": {}, "node_modules/react": {"version": "18.2.0"}}}),
    )

    manifests = inspect_dependencies(workspace)

    by_path = {manifest.path: manifest for manifest in manifests}
    assert set(by_path) == {"pyproject.toml", "web/package.json", "go.mod"}
    python = {dep.name: dep for dep in by_path["pyproject.toml"].dependencies}
    assert python["requests"].locked == "2.32.3"
    assert python["click"].locked is None
    assert by_path["pyproject.toml"].lockfile == "uv.lock"
    node = {dep.name: dep for dep in by_path["web/package.json"].dependencies}
    assert node["react"].locked == "18.2.0"
    assert node["left-pad"].locked is None
    assert [dep.name for dep in by_path["go.mod"].dependencies] == [
        "github.com/gorilla/mux",
        "golang.org/x/text",
    ]
    assert [m.path for m in inspect_dependencies(workspace, "web")] == ["web/package.json"]


def test_the_report_counts_first_and_lists_versions(tmp_path: Path) -> None:
    workspace = ProjectWorkspace.create(tmp_path / "repo")
    write_polyglot(workspace.root)

    text = deps.format_dependencies(inspect_dependencies(workspace), limit=60)

    lines = text.splitlines()
    assert lines[0] == "3 manifest(s), 9 declared dependencies"
    assert "pyproject.toml (Python): 4 dependencies" in lines
    assert "  requests >=2.31" in lines
    assert "  click >=8  (optional: cli)" in lines
    assert "go.mod (Go): 2 dependencies" in lines
    assert "  golang.org/x/text v0.14.0  (indirect)" in lines
    assert "not checked offline" in lines[-1]


def test_a_workspace_without_manifests_says_where_to_look(tmp_path: Path) -> None:
    workspace = ProjectWorkspace.create(tmp_path / "empty")

    assert "No dependency manifests" in deps.format_dependencies([], limit=60)
    assert inspect_dependencies(workspace) == []
