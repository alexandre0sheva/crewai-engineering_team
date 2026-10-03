"""The deterministic repository analysis: what it finds, and that it never changes anything."""

from __future__ import annotations

from pathlib import Path

import pytest
from git_helpers import git, require_git
from repo_fixtures import NODE_APP, PYTHON_APP, make_repo, snapshot, write_tree

from engineering_team.modes.profile_render import render_profile
from engineering_team.modes.repo_analyzer import MAX_FILES, analyze_repo
from engineering_team.modes.repo_profile import RepoProfile


def commands(profile: RepoProfile, kind: str) -> list[str]:
    return [c.command for c in profile.commands if c.kind == kind]


def test_a_python_app_is_profiled(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "notes", PYTHON_APP)

    profile = analyze_repo(root)

    assert profile.name == "notes" and profile.primary_language == "Python"
    python = next(s for s in profile.languages if s.language == "Python")
    assert python.files == 4 and python.lines > 10
    (stack,) = profile.stacks
    assert (stack.language, stack.manager, stack.test, stack.lint) == (
        "python", "pip", "pytest", "ruff",
    )  # fmt: skip
    assert "pyproject.toml" in profile.manifests and "requirements.txt" in profile.manifests
    assert "Makefile" in profile.manifests
    assert profile.test_dirs == ["tests"] and profile.test_files == 1
    assert profile.ci == [".github/workflows/ci.yml"]
    assert profile.git.is_repo is False
    assert [(e.path, e.kind) for e in profile.entrypoints] == [("app/main.py", "cli")]
    assert "console script 'notes'" in profile.entrypoints[0].evidence
    kinds = {(c.path, c.kind) for c in profile.conventions}
    assert {
        ("README.md", "docs"),
        ("CONTRIBUTING.md", "docs"),
        (".editorconfig", "editor"),
    } <= kinds
    assert ("pyproject.toml", "lint") in kinds


def test_commands_come_from_the_project_first_then_from_its_tools(tmp_path: Path) -> None:
    profile = analyze_repo(write_tree(tmp_path / "notes", PYTHON_APP))

    assert commands(profile, "test") == ["make test", "python -m pytest"]
    assert commands(profile, "lint") == ["make lint", "python -m ruff check ."]
    assert commands(profile, "setup") == ["python -m pip install -r requirements.txt"]
    first = profile.commands_of("test")[0]
    assert first.source == "Makefile target 'test'" and first.argv == ["make", "test"]
    assert profile.commands_of("test")[1].source == "detected: python (pip)"


def test_a_node_app_uses_its_package_scripts(tmp_path: Path) -> None:
    profile = analyze_repo(write_tree(tmp_path / "shop", NODE_APP))

    assert commands(profile, "setup") == ["npm ci"]  # there is a package-lock.json
    assert commands(profile, "test")[0] == "npm test"
    assert commands(profile, "lint")[0] == "npm run lint"
    assert commands(profile, "build")[0] == "npm run build"
    assert commands(profile, "run") == ["npm run start"]
    assert profile.commands_of("lint")[0].source == "package.json script 'lint'"
    assert "npx jest" in commands(profile, "test")[1]
    paths = {(e.path, e.kind) for e in profile.entrypoints}
    assert ("bin/shop.js", "cli") in paths and ("src/index.js", "main") in paths
    assert profile.test_files == 1 and profile.stacks[0].language == "javascript"
    assert {c.path for c in profile.conventions} >= {".eslintrc.json", ".prettierrc", "README.md"}


MANIFESTS = [
    pytest.param(
        {"go.mod": "module example.com/x\n\ngo 1.22\n"},
        {"setup": "go mod download", "test": "go test ./...", "build": "go build ./..."},
        id="go",
    ),
    pytest.param(
        {"Cargo.toml": '[package]\nname = "x"\nversion = "0.1.0"\n'},
        {"setup": "cargo fetch", "test": "cargo test", "lint": "cargo clippy"},
        id="cargo",
    ),
    pytest.param(
        {"pom.xml": "<project/>\n", "mvnw": "#!/bin/sh\n"},
        {"test": "mvn -B test", "build": "./mvnw -B compile"},
        id="maven",
    ),
    pytest.param(
        {"build.gradle": "plugins {}\n"},
        {"test": "gradle test", "build": "gradle build -x test"},
        id="gradle",
    ),
    pytest.param(
        {"app.csproj": "<Project/>\n"},
        {"setup": "dotnet restore", "test": "dotnet test", "build": "dotnet build --nologo"},
        id="dotnet",
    ),
    pytest.param(
        {"Gemfile": "gem 'rspec'\n"},
        {"setup": "bundle install", "test": "bundle exec rspec"},
        id="ruby",
    ),
    pytest.param(
        {"composer.json": '{"require-dev": {"phpunit/phpunit": "^10"}}\n'},
        {"setup": "composer install", "test": "vendor/bin/phpunit"},
        id="php",
    ),
    pytest.param(
        {"package.json": '{"scripts": {"test": "vitest run"}}\n', "pnpm-lock.yaml": ""},
        {"setup": "pnpm install", "test": "pnpm test"},
        id="pnpm",
    ),
    pytest.param(
        {"setup.py": "", "tox.ini": "[tox]\n", "tests/test_a.py": "def test_a(): pass\n"},
        {"test": "tox"},
        id="tox",
    ),
    pytest.param(
        {"Makefile": "check:\n\t./run-tests\nbuild:\n\tcc main.c\n.PHONY: check\n"},
        {"test": "make check", "build": "make build"},
        id="makefile-only",
    ),
]


@pytest.mark.parametrize(("files", "expected"), MANIFESTS)
def test_detected_commands_per_toolchain(
    tmp_path: Path, files: dict[str, str], expected: dict[str, str]
) -> None:
    profile = analyze_repo(write_tree(tmp_path / "p", files))

    for kind, command in expected.items():
        assert command in commands(profile, kind), (kind, commands(profile, kind))


def test_a_monorepo_lists_each_projects_commands_in_its_directory(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "mono", {f"api/{k}": v for k, v in PYTHON_APP.items()})
    write_tree(root, {f"web/{k}": v for k, v in NODE_APP.items()})

    profile = analyze_repo(root)

    assert {s.directory for s in profile.stacks} == {"api", "web"}
    assert {(c.directory, c.command) for c in profile.commands_of("test")} >= {
        ("api", "make test"),
        ("web", "npm test"),
    }
    assert "2 projects found" in " ".join(profile.notes)


def test_a_directory_without_a_manifest_says_so(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "loose", {"run.sh": "echo hi\n", "notes.txt": "x\n"})

    profile = analyze_repo(root)

    assert profile.stacks == [] and profile.commands == []
    assert any("No project manifest" in note for note in profile.notes)


def test_gitignored_and_heavy_files_are_not_counted(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path / "p",
        {
            **PYTHON_APP,
            ".gitignore": "generated/\n",
            "generated/big.py": "x = 1\n" * 500,
            "node_modules/dep/index.js": "x\n" * 500,
            ".engineering-team/runs/x/events.jsonl": "{}\n",
        },
    )

    profile = analyze_repo(root)

    assert next(s for s in profile.languages if s.language == "Python").files == 4
    assert profile.files < 20


def test_a_broken_manifest_is_a_note_not_a_crash(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "p", {"package.json": "{not json", "src/a.js": "1\n"})

    profile = analyze_repo(root)

    assert any("package.json is not valid JSON" in note for note in profile.notes)


def test_the_profile_is_the_same_every_time(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "p", NODE_APP)

    assert analyze_repo(root).model_dump() == analyze_repo(root).model_dump()


def test_analysing_changes_nothing(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "notes")
    before, entries = snapshot(root), sorted(p.name for p in root.iterdir())

    analyze_repo(root)

    assert snapshot(root) == before
    assert sorted(p.name for p in root.iterdir()) == entries  # no .engineering-team either
    assert not (root / ".git" / "info" / "exclude").read_text().count("engineering-team")


def test_a_missing_directory_is_a_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is not a directory"):
        analyze_repo(tmp_path / "nope")


def test_the_walk_stops_at_the_file_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("engineering_team.modes.repo_analyzer.MAX_FILES", 5)
    root = write_tree(tmp_path / "p", {f"f{n}.py": "x = 1\n" for n in range(12)})

    profile = analyze_repo(root)

    assert profile.truncated and profile.files == 5 and MAX_FILES == 20_000
    assert "lower bounds" in " ".join(profile.notes)


# -- Git --------------------------------------------------------------------------------------


def test_a_clean_repository_reports_its_branch_and_head(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "notes", branch="trunk")

    state = analyze_repo(root).git

    assert state.is_repo and state.branch == "trunk" and len(state.head) == 12
    assert not state.dirty and state.changed_files == 0 and not state.linked_worktree


def test_a_dirty_repository_counts_changed_and_untracked_files(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "notes")
    (root / "app" / "store.py").write_text("changed = True\n")
    (root / "scratch.txt").write_text("new\n")

    state = analyze_repo(root).git

    assert state.dirty and (state.changed_files, state.untracked_files) == (1, 1)


def test_the_controllers_own_state_does_not_make_a_repository_dirty(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "notes")
    (root / ".engineering-team" / "runs").mkdir(parents=True)
    (root / ".engineering-team" / "runs" / "x.json").write_text("{}")

    assert analyze_repo(root).git.dirty is False


def test_a_subdirectory_of_a_repository_is_a_plain_directory_that_names_the_repository(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "mono", {**{f"svc/{k}": v for k, v in PYTHON_APP.items()}})

    profile = analyze_repo(repo / "svc")

    assert profile.git.is_repo is False and profile.git.enclosing == str(repo.resolve())
    assert any("inside the Git repository" in note for note in profile.notes)


def test_a_linked_worktree_is_recognised(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "notes")
    git(repo, "worktree", "add", "-q", "-b", "other", str(tmp_path / "linked"))

    state = analyze_repo(tmp_path / "linked").git

    assert state.is_repo and state.linked_worktree and state.branch == "other"


# -- the text ---------------------------------------------------------------------------------


def test_the_rendered_profile_states_the_facts(tmp_path: Path) -> None:
    require_git()
    text = render_profile(analyze_repo(make_repo(tmp_path / "notes")))

    assert text.startswith("Project: notes") and "Git repository (branch main" in text
    assert "test      make test  (Makefile target 'test')" in text
    assert "Entry points:" in text and "CI: .github/workflows/ci.yml" in text
