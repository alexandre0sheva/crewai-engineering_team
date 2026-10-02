from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering_team.workspaces import (
    OWNER_MARKER,
    prepare_workspace,
    reset_refusal,
)


def _owned_project(root: Path, name: str = "demo") -> Path:
    workspace = prepare_workspace(name, root)
    (workspace.root / "keep.txt").write_text("generated", encoding="utf-8")
    return workspace.root


def test_new_projects_get_an_owner_marker(tmp_path: Path) -> None:
    project = prepare_workspace("demo", tmp_path / "ws").root

    marker = json.loads((project / OWNER_MARKER).read_text(encoding="utf-8"))
    assert marker["tool"] == "engineering-team"
    assert {"version", "created"} <= marker.keys()


def test_resuming_an_owned_project_keeps_its_files(tmp_path: Path) -> None:
    project = _owned_project(tmp_path / "ws")

    resumed = prepare_workspace("demo", tmp_path / "ws")

    assert resumed.root == project
    assert (project / "keep.txt").read_text(encoding="utf-8") == "generated"


def test_reset_deletes_an_owned_project(tmp_path: Path) -> None:
    project = _owned_project(tmp_path / "ws")

    prepare_workspace("demo", tmp_path / "ws", reset=True)

    assert not (project / "keep.txt").exists()
    assert (project / OWNER_MARKER).is_file()


def test_reset_refuses_a_directory_this_tool_did_not_create(tmp_path: Path) -> None:
    """Regression: --reset used to rmtree any <root>/<slug> without an ownership check."""

    foreign = tmp_path / "ws" / "demo"
    foreign.mkdir(parents=True)
    (foreign / "precious.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="not created by engineering-team"):
        prepare_workspace("demo", tmp_path / "ws", reset=True)

    assert (foreign / "precious.txt").read_text(encoding="utf-8") == "keep"


def test_force_reset_can_delete_a_foreign_directory(tmp_path: Path) -> None:
    foreign = tmp_path / "ws" / "demo"
    foreign.mkdir(parents=True)
    (foreign / "scratch.txt").write_text("remove me", encoding="utf-8")

    prepare_workspace("demo", tmp_path / "ws", reset=True, force_reset=True)

    assert not (foreign / "scratch.txt").exists()


def test_a_foreign_non_empty_directory_is_not_adopted_silently(tmp_path: Path) -> None:
    foreign = tmp_path / "ws" / "demo"
    foreign.mkdir(parents=True)
    (foreign / "existing.py").write_text("print('mine')", encoding="utf-8")

    with pytest.raises(ValueError, match="will not be modified"):
        prepare_workspace("demo", tmp_path / "ws")

    assert not (foreign / ".engineering-team").exists()


def test_an_empty_existing_directory_can_be_used(tmp_path: Path) -> None:
    (tmp_path / "ws" / "demo").mkdir(parents=True)

    assert prepare_workspace("demo", tmp_path / "ws").root.is_dir()


def test_projects_created_by_0_1_0_are_recognised_and_upgraded(tmp_path: Path) -> None:
    legacy = tmp_path / "ws" / "demo"
    (legacy / ".engineering-team").mkdir(parents=True)
    (legacy / ".engineering-team" / "run.json").write_text("{}", encoding="utf-8")
    (legacy / "notes.py").write_text("print('old run')", encoding="utf-8")

    prepare_workspace("demo", tmp_path / "ws")
    prepare_workspace("demo", tmp_path / "ws", reset=True)

    assert (legacy / OWNER_MARKER).is_file()
    assert not (legacy / "notes.py").exists()


def test_a_symlinked_project_directory_is_never_used(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "data.txt").write_text("keep", encoding="utf-8")
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "demo").symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        prepare_workspace("demo", tmp_path / "ws", reset=True, force_reset=True)

    assert (target / "data.txt").exists()


def test_project_path_that_is_a_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "demo").write_text("a file", encoding="utf-8")

    with pytest.raises(ValueError, match="not a directory"):
        prepare_workspace("demo", tmp_path / "ws")


# --- targets that can never be reset, even with --force-reset --------------------------


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/", "filesystem root"),
        ("home/user", "home directory"),
        ("home", "home directory"),
        ("work/repo", "working directory"),
        ("work/repo/sub", None),
        ("work", "working directory"),
        ("opt/tools/engineering-team", "installation"),
        ("srv/elsewhere", None),
    ],
)
def test_reset_refusal_rules(tmp_path: Path, target: str, expected: str | None) -> None:
    def place(relative: str) -> Path:
        return Path("/") if relative == "/" else tmp_path / relative

    reason = reset_refusal(
        place(target),
        cwd=place("work/repo"),
        home=place("home/user"),
        package_file=place("opt/tools/engineering-team/src/engineering_team/workspaces.py"),
    )

    if expected is None:
        assert reason is None
    else:
        assert reason is not None and expected in reason


def test_force_reset_still_refuses_the_current_directory(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "ws" / "demo"
    project.mkdir(parents=True)
    (project / "keep.txt").write_text("keep", encoding="utf-8")
    monkeypatch.chdir(project)

    with pytest.raises(ValueError, match="working directory"):
        prepare_workspace("demo", tmp_path / "ws", reset=True, force_reset=True)

    assert (project / "keep.txt").exists()


def test_force_reset_still_refuses_the_orchestrator_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project directory that contains this very package must never be deleted."""

    from engineering_team import workspaces

    project = tmp_path / "ws" / "demo"
    fake_package = project / "src" / "engineering_team"
    fake_package.mkdir(parents=True)
    (fake_package / "workspaces.py").write_text("# fake", encoding="utf-8")
    monkeypatch.setattr(workspaces, "__file__", str(fake_package / "workspaces.py"))

    with pytest.raises(ValueError, match="installation"):
        prepare_workspace("demo", tmp_path / "ws", reset=True, force_reset=True)

    assert (fake_package / "workspaces.py").exists()
