from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team.execution.local import LocalBackend
from engineering_team.tools.commands import run_command
from engineering_team.tools.workspace import ProjectWorkspace, WorkspaceError


def run(workspace: ProjectWorkspace, command: str, **options: object) -> str:
    backend = LocalBackend(workspace.root / ".engineering-team" / "logs")
    return run_command(workspace, command, backend=backend, **options)  # type: ignore[arg-type]


@pytest.fixture
def workspace(tmp_path: Path) -> ProjectWorkspace:
    return ProjectWorkspace.create(tmp_path / "project")


def test_nested_files_can_be_written_read_and_replaced(workspace: ProjectWorkspace) -> None:
    result = workspace.write_file("src/example/app.py", "value = 1\n")

    assert result == "Wrote 10 bytes to src/example/app.py."
    assert workspace.read_file("src/example/app.py") == "value = 1\n"
    workspace.replace_in_file("src/example/app.py", "1", "2")
    assert workspace.read_file("src/example/app.py") == "value = 2\n"


@pytest.mark.parametrize("path", ["../outside.txt", "/tmp/outside.txt", ".git/config"])
def test_paths_cannot_escape_the_workspace(workspace: ProjectWorkspace, path: str) -> None:
    with pytest.raises(WorkspaceError):
        workspace.write_file(path, "unsafe")


def test_symlink_escape_is_rejected(workspace: ProjectWorkspace, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace.root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceError):
        workspace.write_file("link/escaped.txt", "unsafe")


def test_file_listing_ignores_dependency_directories(workspace: ProjectWorkspace) -> None:
    workspace.write_file("src/app.py", "print('ok')\n")
    workspace.write_file("node_modules/pkg/index.js", "ignored\n")

    listing = workspace.list_files()

    assert "src/app.py" in listing
    assert "node_modules" not in listing


def test_command_reports_stdout_stderr_and_exit_code(workspace: ProjectWorkspace) -> None:
    workspace.write_file(
        "check.py",
        "import sys\nprint('hello')\nprint('warning', file=sys.stderr)\n",
    )

    result = run(workspace, "python check.py")

    assert "Exit code: 0" in result
    assert "hello" in result
    assert "warning" in result


def test_project_commands_do_not_inherit_api_keys(
    workspace: ProjectWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-inherit")
    workspace.write_file(
        "environment_check.py",
        "import os\nprint(os.getenv('OPENAI_API_KEY', 'missing'))\n",
    )

    result = run(workspace, "python environment_check.py")

    assert "missing" in result
    assert "do-not-inherit" not in result


def test_shell_and_inline_execution_are_rejected(workspace: ProjectWorkspace) -> None:
    with pytest.raises(WorkspaceError):
        run(workspace, "python check.py | make")
    with pytest.raises(WorkspaceError):
        run(workspace, "python -c \"print('unsafe')\"")


def test_workspace_root_cannot_be_deleted(workspace: ProjectWorkspace) -> None:
    with pytest.raises(WorkspaceError):
        workspace.delete_path(".")


@pytest.fixture
def protected_workspace(workspace: ProjectWorkspace) -> ProjectWorkspace:
    """A workspace with real Git metadata and controller state plus aliases to both."""

    (workspace.root / ".git").mkdir()
    (workspace.root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (workspace.root / ".engineering-team").mkdir()
    (workspace.root / ".engineering-team" / "owner.json").write_text("{}\n", encoding="utf-8")
    (workspace.root / "gitlink").symlink_to(".git", target_is_directory=True)
    (workspace.root / "statelink").symlink_to(".engineering-team", target_is_directory=True)
    return workspace


ALIASED_TARGETS = ["gitlink/config", "statelink/owner.json", ".GIT/config", ".Engineering-Team/x"]


@pytest.mark.parametrize("path", ALIASED_TARGETS)
def test_aliases_of_protected_storage_cannot_be_read_written_replaced_or_deleted(
    protected_workspace: ProjectWorkspace, path: str
) -> None:
    with pytest.raises(WorkspaceError):
        protected_workspace.read_file(path)
    with pytest.raises(WorkspaceError):
        protected_workspace.write_file(path, "unsafe")
    with pytest.raises(WorkspaceError):
        protected_workspace.replace_in_file(path, "[core]", "[evil]")
    with pytest.raises(WorkspaceError):
        protected_workspace.delete_path(path)

    assert (protected_workspace.root / ".git" / "config").read_text(encoding="utf-8") == "[core]\n"
    assert (protected_workspace.root / ".engineering-team" / "owner.json").exists()


@pytest.mark.parametrize("directory", ["gitlink", "statelink", ".git", ".engineering-team"])
def test_protected_directories_cannot_be_listed_or_used_as_command_cwd(
    protected_workspace: ProjectWorkspace, directory: str
) -> None:
    with pytest.raises(WorkspaceError):
        protected_workspace.list_files(directory)
    with pytest.raises(WorkspaceError):
        run(protected_workspace, "python --version", relative_cwd=directory)


def test_protected_directories_are_hidden_from_the_project_listing(
    protected_workspace: ProjectWorkspace,
) -> None:
    listing = protected_workspace.list_files()

    assert ".git" not in listing
    assert ".engineering-team" not in listing


def test_a_symlink_inside_protected_storage_cannot_be_unlinked_through_an_alias(
    protected_workspace: ProjectWorkspace,
) -> None:
    inner = protected_workspace.root / ".git" / "inner-link"
    inner.symlink_to("config")

    with pytest.raises(WorkspaceError):
        protected_workspace.delete_path("gitlink/inner-link")

    assert inner.is_symlink()


def test_deleting_a_symlink_removes_only_the_link(protected_workspace: ProjectWorkspace) -> None:
    protected_workspace.delete_path("gitlink")

    assert not (protected_workspace.root / "gitlink").exists()
    assert (protected_workspace.root / ".git" / "config").exists()


def test_absolute_command_arguments_cannot_name_protected_storage(
    protected_workspace: ProjectWorkspace,
) -> None:
    protected_workspace.write_file("tool.py", "print('ok')\n")
    target = protected_workspace.root / ".git" / "config"

    with pytest.raises(WorkspaceError):
        run(protected_workspace, f"python tool.py {target}")


def test_agent_scratch_space_remains_available(workspace: ProjectWorkspace) -> None:
    workspace.write_file(".engineering-team/tmp/scratch.txt", "ok\n")

    assert workspace.read_file(".engineering-team/tmp/scratch.txt") == "ok\n"


def test_extra_commands_come_from_the_workspace_configuration(tmp_path: Path) -> None:
    plain = ProjectWorkspace.create(tmp_path / "plain")
    extended = ProjectWorkspace.create(tmp_path / "extended", extra_commands=["Echo", " "])

    with pytest.raises(WorkspaceError, match="command_allowlist"):
        run(plain, "echo hello")
    assert "hello" in run(extended, "echo hello")


def test_environment_passthrough_is_limited_to_the_configured_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x")
    monkeypatch.setenv("OTHER_TOKEN", "must-not-leak")
    open_workspace = ProjectWorkspace.create(tmp_path / "w", env_passthrough=["DATABASE_URL"])
    open_workspace.write_file(
        "show.py",
        "import os\nprint(os.getenv('DATABASE_URL'), os.getenv('OTHER_TOKEN'))\n",
    )

    result = run(open_workspace, "python show.py")

    assert "sqlite:///x None" in result
    assert "must-not-leak" not in result
