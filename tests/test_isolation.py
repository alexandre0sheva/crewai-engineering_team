"""Isolation policies: the team gets a branch, a worktree, or a copy, and the original stays put."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from git_helpers import git, require_git
from repo_fixtures import NODE_APP, PYTHON_APP, make_repo, snapshot, write_tree

from engineering_team.git.port import GitPort
from engineering_team.modes.isolation import (
    WORKTREES,
    IsolationError,
    branch_name,
    isolate,
    read_isolation,
)
from engineering_team.modes.repo_analyzer import analyze_repo, standalone_git
from engineering_team.workspaces import prepare_workspace

RUN = "20261003-101500-abc123"


def out(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def dirty(root: Path) -> None:
    (root / "app" / "store.py").write_text("def add(items, text):\n    return items  # wip\n")
    (root / "scratch.txt").write_text("untracked\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "notes")


# -- branch mode ------------------------------------------------------------------------------


def test_a_clean_repository_gets_a_branch_and_keeps_its_files(repo: Path) -> None:
    before = snapshot(repo)

    iso = isolate(repo, run_id=RUN, slug="Add search!")

    assert iso.mode == "branch" and iso.workspace == repo.resolve() and iso.source == repo.resolve()
    assert iso.branch == f"engineering-team/{RUN}-add-search"
    assert iso.base_branch == "main" and iso.base_commit == out(repo, "rev-parse", "HEAD")
    assert out(repo, "branch", "--show-current") == iso.branch
    assert snapshot(repo) == before  # files untouched (mtimes too)
    assert out(repo, "rev-parse", "main") == iso.base_commit  # the user's branch did not move


def test_the_state_directory_is_excluded_without_touching_gitignore(repo: Path) -> None:
    (repo / ".gitignore").write_text("*.log\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ignore logs")
    gitignore = (repo / ".gitignore").read_bytes()

    isolate(repo, run_id=RUN, slug="x")

    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert ".engineering-team/" in exclude.splitlines()
    assert (repo / ".gitignore").read_bytes() == gitignore
    assert out(repo, "status", "--porcelain") == ""  # the state it wrote does not show up


def test_the_workspace_is_marked_adopted_and_the_isolation_is_recorded(repo: Path) -> None:
    iso = isolate(repo, run_id=RUN, slug="x")

    owner = (repo / ".engineering-team" / "owner.json").read_text()
    assert '"adopted": true' in owner and str(repo.resolve()) in owner
    assert read_isolation(repo) == iso


def test_the_branch_name_is_safe_whatever_the_slug(repo: Path) -> None:
    assert (
        branch_name(RUN, "  ../../Weird  slug; rm -rf  ")
        == f"engineering-team/{RUN}-weird-slug-rm-rf"
    )
    assert branch_name(RUN, "???") == f"engineering-team/{RUN}-work"
    assert len(branch_name(RUN, "a" * 200)) < 100


# -- worktree mode ----------------------------------------------------------------------------


def test_a_dirty_repository_gets_a_worktree_and_stays_byte_identical(repo: Path) -> None:
    dirty(repo)
    before, head = snapshot(repo), out(repo, "rev-parse", "HEAD")

    iso = isolate(repo, run_id=RUN, slug="fix")

    assert iso.mode == "worktree" and iso.workspace == repo.resolve() / WORKTREES / RUN
    assert snapshot(repo) == before  # the dirty edit, the untracked file, every mtime
    assert (
        out(repo, "branch", "--show-current") == "main" and out(repo, "rev-parse", "HEAD") == head
    )
    work = iso.workspace
    assert (work / "app" / "store.py").read_text() == PYTHON_APP["app/store.py"]  # committed text
    assert not (work / "scratch.txt").exists()  # uncommitted work is not in the worktree
    assert out(work, "branch", "--show-current") == iso.branch
    assert any("uncommitted changes are not in the worktree" in n for n in iso.notes)


def test_worktree_can_be_asked_for_on_a_clean_tree(repo: Path) -> None:
    before = snapshot(repo)

    iso = isolate(repo, run_id=RUN, slug="x", mode="worktree")

    assert iso.mode == "worktree" and snapshot(repo) == before
    assert out(repo, "branch", "--show-current") == "main"
    assert "worktree" in iso.describe() and iso.branch in iso.describe()


def test_the_team_can_commit_in_a_worktree_without_moving_the_users_branch(repo: Path) -> None:
    dirty(repo)
    iso = isolate(repo, run_id=RUN, slug="fix")
    head = out(repo, "rev-parse", "main")

    with standalone_git(iso.workspace) as port:
        assert port.is_repo() and port.current_branch() == iso.branch
        (iso.workspace / "NEW.txt").write_text("by the team\n")
        sha = port.checkpoint("stage(implement): add NEW.txt")
        port.exclude_controller_state()  # worktree-aware: the exclude file is the common one

    assert out(repo, "rev-parse", "main") == head
    assert out(repo, "rev-parse", iso.branch) == sha
    assert out(iso.workspace, "show", "--stat", "--format=%s", "HEAD").startswith(
        "stage(implement)"
    )
    assert out(repo, "status", "--porcelain").count(".engineering-team") == 0


def test_the_worktree_is_an_owned_workspace_the_pipeline_can_open(repo: Path) -> None:
    dirty(repo)
    iso = isolate(repo, run_id=RUN, slug="x")

    profile = analyze_repo(iso.workspace)

    assert profile.git.is_repo and profile.git.linked_worktree and not profile.git.dirty


# -- the dirty-tree rules ---------------------------------------------------------------------


def test_branch_mode_on_a_dirty_tree_is_refused_without_allow_dirty(repo: Path) -> None:
    dirty(repo)
    before = snapshot(repo)

    with pytest.raises(IsolationError, match="uncommitted changes .*--allow-dirty"):
        isolate(repo, run_id=RUN, slug="x", mode="branch")

    assert snapshot(repo) == before and out(repo, "branch", "--show-current") == "main"
    assert out(repo, "branch", "--list", "engineering-team/*") == ""


def test_allow_dirty_works_in_place_on_a_new_branch(repo: Path) -> None:
    dirty(repo)

    for mode in ("branch", "auto"):
        with standalone_git(repo) as port:
            port.exclude_controller_state()
        iso = isolate(repo, run_id=f"{RUN}-{mode}", slug="x", mode=mode, allow_dirty=True)  # type: ignore[arg-type]
        assert iso.mode == "branch" and iso.workspace == repo.resolve()
        assert any("uncommitted changes are in the working tree" in n for n in iso.notes)
        git(repo, "checkout", "-q", "main")


def test_a_repository_without_commits_is_refused(tmp_path: Path) -> None:
    require_git()
    root = write_tree(tmp_path / "empty", {"a.py": "x = 1\n"})
    git(root, "init", "-q", "-b", "main")

    with pytest.raises(IsolationError, match="no commits yet"):
        isolate(root, run_id=RUN, slug="x")


def test_a_subdirectory_of_a_repository_is_refused_with_the_top_level_named(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "mono", {f"svc/{k}": v for k, v in PYTHON_APP.items()})

    with pytest.raises(IsolationError, match="inside the Git repository at .*mono"):
        isolate(repo / "svc", run_id=RUN, slug="x")


def test_a_broken_dot_git_is_refused(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "odd", {"a.py": "x = 1\n"})
    (root / ".git").write_text("gitdir: /nonexistent\n")

    with pytest.raises(IsolationError, match="not a usable repository"):
        isolate(root, run_id=RUN, slug="x")


# -- copy mode --------------------------------------------------------------------------------


def test_a_directory_without_git_is_copied_and_the_original_is_untouched(tmp_path: Path) -> None:
    source = write_tree(tmp_path / "shop", NODE_APP)
    write_tree(source, {".venv/bin/python": "x", "pkg/__pycache__/a.pyc": "x"})
    before = snapshot(source)

    iso = isolate(source, run_id=RUN, slug="x", workspace_root=tmp_path / "ws")

    assert iso.mode == "copy" and iso.workspace == (tmp_path / "ws" / "shop").resolve()
    assert iso.branch is None and iso.base_commit is None
    assert snapshot(source) == before and not (source / ".engineering-team").exists()
    copied = snapshot(iso.workspace)
    assert {k for k in before if not k.startswith((".venv", "pkg/__pycache__"))} == set(copied)
    assert all(copied[k][0] == before[k][0] for k in copied)
    assert not (iso.workspace / ".git").exists()
    assert any("--init-git" in n for n in iso.notes)


def test_init_git_commits_the_import_in_the_copy_only(tmp_path: Path) -> None:
    require_git()
    source = write_tree(tmp_path / "shop", NODE_APP)
    before = snapshot(source)

    iso = isolate(source, run_id=RUN, slug="x", workspace_root=tmp_path / "ws", init_git=True)

    assert not (source / ".git").exists() and snapshot(source) == before
    assert iso.base_commit == out(iso.workspace, "rev-parse", "HEAD")
    assert out(iso.workspace, "ls-files").splitlines().count("package.json") == 1
    assert ".engineering-team/" in (iso.workspace / ".git/info/exclude").read_text()


def test_the_copy_will_not_overwrite_or_nest(tmp_path: Path) -> None:
    source = write_tree(tmp_path / "shop", NODE_APP)
    (tmp_path / "ws" / "shop").mkdir(parents=True)

    with pytest.raises(IsolationError, match="already exists"):
        isolate(source, run_id=RUN, slug="x", workspace_root=tmp_path / "ws")
    with pytest.raises(IsolationError, match="contain one another"):
        isolate(source, run_id=RUN, slug="x", workspace_root=source, name="inner")


def test_a_directory_that_is_too_large_is_not_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("engineering_team.modes.isolation.MAX_COPY_BYTES", 10)
    source = write_tree(tmp_path / "shop", NODE_APP)

    with pytest.raises(IsolationError, match="too large to copy"):
        isolate(source, run_id=RUN, slug="x", workspace_root=tmp_path / "ws")

    assert not (tmp_path / "ws").exists()


def test_branch_and_worktree_need_a_repository(tmp_path: Path) -> None:
    source = write_tree(tmp_path / "shop", NODE_APP)

    for mode in ("branch", "worktree"):
        with pytest.raises(IsolationError, match="not a Git repository"):
            isolate(source, run_id=RUN, slug="x", mode=mode)  # type: ignore[arg-type]


def test_nonsense_targets_are_refused(tmp_path: Path) -> None:
    with pytest.raises(IsolationError, match="is not a directory"):
        isolate(tmp_path / "missing", run_id=RUN, slug="x")
    with pytest.raises(IsolationError, match="too broad"):
        isolate(Path(Path.home().anchor), run_id=RUN, slug="x")


# -- never a remote -----------------------------------------------------------------------------


def test_no_remote_is_contacted_or_changed(repo: Path) -> None:
    git(repo, "remote", "add", "origin", "https://invalid.invalid/notes.git")
    config = (repo / ".git" / "config").read_text()

    isolate(repo, run_id=RUN, slug="x", mode="worktree")  # tests block network connections
    isolate(repo, run_id=RUN + "b", slug="x", mode="branch")

    assert out(repo, "remote", "-v").count("invalid.invalid") == 2
    assert "refs/remotes" not in out(repo, "for-each-ref")
    assert not hasattr(GitPort, "push") and not hasattr(GitPort, "fetch")
    assert '[remote "origin"]' in config and 'branch "engineering-team' not in config


# -- adopting in place (`new --adopt`) ---------------------------------------------------------


def test_adopt_lets_the_team_work_in_a_foreign_directory_and_marks_it(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "ws" / "legacy", NODE_APP)

    workspace = prepare_workspace("legacy", tmp_path / "ws", adopt=True)

    assert workspace.root == root.resolve()
    assert '"adopted": true' in (root / ".engineering-team" / "owner.json").read_text()


def test_an_adopted_directory_is_not_reset_without_force(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "ws" / "legacy", NODE_APP)
    prepare_workspace("legacy", tmp_path / "ws", adopt=True)

    with pytest.raises(ValueError, match="adopted from an existing directory"):
        prepare_workspace("legacy", tmp_path / "ws", reset=True)

    assert (root / "package.json").is_file()
    prepare_workspace("legacy", tmp_path / "ws", reset=True, force_reset=True)
    assert not (root / "package.json").exists()


def test_adopting_a_git_repository_in_place_is_refused(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "ws" / "legacy")
    before = snapshot(root)

    with pytest.raises(ValueError, match="is a Git repository"):
        prepare_workspace("legacy", tmp_path / "ws", adopt=True)

    assert snapshot(root) == before and not (root / ".engineering-team").exists()


def test_a_foreign_directory_still_needs_the_adopt_flag(tmp_path: Path) -> None:
    write_tree(tmp_path / "ws" / "legacy", NODE_APP)

    with pytest.raises(ValueError, match="pass --adopt"):
        prepare_workspace("legacy", tmp_path / "ws")
