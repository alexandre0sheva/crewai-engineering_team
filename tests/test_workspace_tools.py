from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team.tools.workspace_tools import ProjectWorkspace, WorkspaceError


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

    result = workspace.run_command("python check.py")

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

    result = workspace.run_command("python environment_check.py")

    assert "missing" in result
    assert "do-not-inherit" not in result


def test_shell_and_inline_execution_are_rejected(workspace: ProjectWorkspace) -> None:
    with pytest.raises(WorkspaceError):
        workspace.run_command("python check.py | make")
    with pytest.raises(WorkspaceError):
        workspace.run_command("python -c \"print('unsafe')\"")


def test_workspace_root_cannot_be_deleted(workspace: ProjectWorkspace) -> None:
    with pytest.raises(WorkspaceError):
        workspace.delete_path(".")
