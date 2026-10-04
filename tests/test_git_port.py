"""``GitPort`` against real Git in temp directories: the controller's whole use of Git."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from git_helpers import commit as make_commit
from git_helpers import init_repo, require_git

from engineering_team.execution.backend import CommandRecord, CommandSpec
from engineering_team.git.port import DEFAULT_EMAIL, DEFAULT_NAME, GitError, GitPort
from engineering_team.runtime.context import RunContext
from engineering_team.settings import GitSettings, load_settings

MakeContext = Callable[..., RunContext]
EMPTY_TREE_HASHES = {"4b825dc642cb6eb9a060e54bf8d69288fbee4904"}


@pytest.fixture(autouse=True)
def needs_git() -> None:
    require_git()


def port_for(ctx: RunContext) -> GitPort:
    return ctx.git


def raw(root: Path, *args: str) -> str:
    """Ask Git directly (not through the port), with a clean environment."""

    env = {"PATH": __import__("os").environ["PATH"], "GIT_CONFIG_GLOBAL": "/dev/null"}
    done = subprocess.run(
        ["git", *args], cwd=root, env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@pytest.fixture
def ctx(make_context: MakeContext) -> RunContext:
    return make_context()


@pytest.fixture
def repo(ctx: RunContext) -> RunContext:
    ctx.workspace.write_file("README.md", "# demo\n")
    assert ctx.git.init() is True
    return ctx


# -- is this a repository ---------------------------------------------------------------------


def test_a_plain_directory_is_not_a_repository(ctx: RunContext) -> None:
    assert port_for(ctx).available() is True
    assert ctx.git.is_repo() is False
    with pytest.raises(GitError, match="not a Git repository"):
        ctx.git.status()


def test_a_project_inside_someone_elses_repository_is_not_ours_to_touch(
    make_context: MakeContext, tmp_path: Path
) -> None:
    init_repo(tmp_path)  # the project directory (tmp_path/project) sits inside this repo
    ctx = make_context()
    ctx.workspace.write_file("a.txt", "x\n")

    assert ctx.git.is_repo() is False
    with pytest.raises(GitError, match="not a Git repository"):
        ctx.git.checkpoint("nope")
    # The outer repository was not touched.
    assert raw(tmp_path, "status", "--porcelain", "--untracked-files=no") == ""


def test_git_not_being_installed_is_an_error_with_a_fix(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)

    assert ctx.git.available() is False
    with pytest.raises(GitError, match="git is not installed"):
        ctx.git.init()


# -- init, checkpoints, status ----------------------------------------------------------------


def test_init_makes_a_repository_with_an_initial_commit_and_leaves_the_controller_state_out(
    repo: RunContext,
) -> None:
    root = repo.workspace.root

    assert repo.git.is_repo() and repo.git.current_branch() == "main"
    head = repo.git.head()
    assert head and raw(root, "rev-parse", "HEAD") == head
    assert raw(root, "log", "--format=%s") == "Initial commit"
    assert raw(root, "ls-files") == "README.md"  # the project is in; .engineering-team is not
    assert repo.git.is_dirty() is False
    exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".engineering-team/" in exclude and "node_modules/" in exclude


def test_init_on_an_existing_repository_does_nothing(repo: RunContext) -> None:
    head = repo.git.head()

    assert repo.git.init() is False
    assert repo.git.head() == head


def test_a_checkpoint_commits_everything_as_the_engineering_team(repo: RunContext) -> None:
    repo.workspace.write_file("src/app.py", "print('hi')\n")
    assert repo.git.is_dirty() is True
    assert "?? src/" in repo.git.status()

    sha = repo.git.checkpoint("stage(plan): design the notes app")

    root = repo.workspace.root
    assert sha == repo.git.head() and repo.git.is_dirty() is False
    assert raw(root, "log", "-1", "--format=%s") == "stage(plan): design the notes app"
    assert raw(root, "log", "-1", "--format=%an <%ae>|%cn <%ce>") == (
        f"{DEFAULT_NAME} <{DEFAULT_EMAIL}>|{DEFAULT_NAME} <{DEFAULT_EMAIL}>"
    )
    assert "src/app.py" in raw(root, "show", "--name-only", "--format=", "HEAD")


def test_a_stage_that_changed_nothing_still_gets_its_commit(repo: RunContext) -> None:
    before = repo.git.head()

    sha = repo.git.checkpoint("stage(spec): no files")

    assert sha != before and raw(repo.workspace.root, "rev-list", "--count", "HEAD") == "2"


def test_the_author_is_configurable(make_context: MakeContext) -> None:
    settings = load_settings().model_copy(
        update={"git": GitSettings(author_name="Bot", author_email="bot@example.org")}
    )
    ctx = make_context(settings=settings)
    ctx.workspace.write_file("a.txt", "x\n")
    ctx.git.init()

    assert raw(ctx.workspace.root, "log", "-1", "--format=%an <%ae>") == "Bot <bot@example.org>"


def test_status_shows_modified_added_and_untracked_files(repo: RunContext) -> None:
    repo.workspace.write_file("README.md", "# changed\n")
    repo.workspace.write_file("new.txt", "n\n")

    lines = repo.git.status().splitlines()

    assert " M README.md" in lines and "?? new.txt" in lines
    assert not any(".engineering-team" in line for line in lines)


# -- branches and worktrees -------------------------------------------------------------------


def test_branches_are_created_and_checked_out(repo: RunContext) -> None:
    repo.git.create_branch("feature/notes-cli")

    assert repo.git.current_branch() == "feature/notes-cli"
    repo.git.create_branch("side", checkout=False)
    assert repo.git.current_branch() == "feature/notes-cli"
    assert "side" in raw(repo.workspace.root, "branch", "--format=%(refname:short)").split()


@pytest.mark.parametrize("name", ["", "-d", "a b", "x..y", "/lead", "bad~name", "a\nb"])
def test_unsafe_branch_names_are_refused(repo: RunContext, name: str) -> None:
    with pytest.raises(GitError, match="branch name"):
        repo.git.create_branch(name)


def test_a_worktree_can_be_added_and_removed(repo: RunContext, tmp_path: Path) -> None:
    target = tmp_path / "lane-1"

    repo.git.worktree_add(target, "lane-1")

    assert (target / "README.md").is_file()
    assert "[lane-1]" in raw(repo.workspace.root, "worktree", "list")
    repo.git.worktree_remove(target)
    assert not target.exists()
    assert "[lane-1]" not in raw(repo.workspace.root, "worktree", "list")


# -- diffs, patches, and the tree hash --------------------------------------------------------


def test_diff_covers_edits_and_new_files_without_touching_the_index(repo: RunContext) -> None:
    base = repo.git.head()
    repo.workspace.write_file("README.md", "# demo\nmore\n")
    repo.workspace.write_file("new.txt", "fresh\n")
    status_before = repo.git.status()

    diff = repo.git.diff(base)
    stat = repo.git.diff_stat(base)

    assert "+more" in diff and "+fresh" in diff and "new.txt" in diff
    assert "2 files changed" in stat and "README.md" in stat
    assert repo.git.status() == status_before  # nothing was staged
    assert raw(repo.workspace.root, "diff", "--cached", "--name-only") == ""


def test_the_diff_of_a_project_with_no_commit_is_against_the_empty_tree(
    ctx: RunContext,
) -> None:
    ctx.workspace.write_file("a.txt", "one\n")
    raw(ctx.workspace.root, "init", "-q")

    assert "+one" in ctx.git.diff(None)


def test_an_exported_patch_applies_to_the_base_and_reproduces_the_work(
    repo: RunContext, tmp_path: Path
) -> None:
    base = repo.git.head()
    repo.workspace.write_file("README.md", "# demo\nedited\n")
    repo.workspace.write_file("src/new.py", "VALUE = 1\n")
    (repo.workspace.root / "logo.bin").write_bytes(bytes(range(256)))
    destination = tmp_path / "out" / "work.patch"

    repo.git.export_patch(destination, base)

    assert destination.is_file() and destination.stat().st_size > 0
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "README.md").write_text("# demo\n", encoding="utf-8")
    raw(clone, "init", "-q")
    raw(clone, "apply", str(destination))
    assert (clone / "README.md").read_text(encoding="utf-8") == "# demo\nedited\n"
    assert (clone / "src" / "new.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (clone / "logo.bin").read_bytes() == bytes(range(256))


def test_the_tree_hash_follows_the_content_and_ignores_controller_state(
    repo: RunContext,
) -> None:
    first = repo.git.tree_hash()
    (repo.workspace.root / ".engineering-team" / "tmp").mkdir(parents=True, exist_ok=True)
    (repo.workspace.root / ".engineering-team" / "tmp" / "x").write_text("noise", encoding="utf-8")
    assert repo.git.tree_hash() == first
    head = repo.git.head()

    repo.workspace.write_file("README.md", "# changed\n")

    assert repo.git.tree_hash() != first
    assert repo.git.head() == head and repo.git.status().strip() == "M README.md"
    repo.git.checkpoint("save")
    assert repo.git.tree_hash() == raw(repo.workspace.root, "rev-parse", "HEAD^{tree}")


def test_an_edit_right_after_a_checkpoint_is_always_seen(repo: RunContext) -> None:
    """Git's index keeps whole-second stat data and uses its own timestamp to know which entries
    must be rehashed ("racily clean"). A copied index must keep that timestamp, or an edit of
    the same size within the second of the last commit sometimes looks like no edit at all.
    The window is a matter of timing, so try many times."""

    for attempt in range(12):
        name = f"f{attempt}.txt"
        repo.workspace.write_file(name, "one\n")
        repo.git.checkpoint(f"add {name}")
        repo.workspace.write_file(name, "two\n")  # the same size, moments later

        assert "+two" in repo.git.diff(None, (name,)), attempt
        assert repo.git.tree_hash() != raw(repo.workspace.root, "rev-parse", "HEAD^{tree}")


def test_the_empty_project_hashes_to_the_empty_tree(ctx: RunContext) -> None:
    raw(ctx.workspace.root, "init", "-q")

    assert ctx.git.tree_hash() in EMPTY_TREE_HASHES


# -- hard rules -------------------------------------------------------------------------------


def test_hooks_and_repository_configured_commands_never_run(repo: RunContext) -> None:
    root = repo.workspace.root
    marker = root.parent / "hook-ran"
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho ran > {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    raw(root, "config", "core.fsmonitor", f"touch {marker}")
    raw(root, "config", "diff.external", f"touch {marker}")
    raw(root, "config", "core.hooksPath", str(root / ".git" / "hooks"))
    repo.workspace.write_file("a.txt", "x\n")

    repo.git.checkpoint("stage(x): y")
    repo.git.status()
    repo.git.diff(None)

    assert not marker.exists()


def test_the_port_has_no_way_to_push_fetch_or_touch_remotes_or_force(repo: RunContext) -> None:
    names = {name for name in dir(repo.git) if not name.startswith("_")}

    assert not {
        n for n in names if any(w in n for w in ("push", "fetch", "pull", "remote", "force"))
    }


class Recorder:
    """A backend that remembers what it was asked to run, then runs it."""

    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.specs: list[CommandSpec] = []

    def run(self, spec: CommandSpec) -> CommandRecord:
        self.specs.append(spec)
        return self.inner.run(spec)  # type: ignore[attr-defined, no-any-return]

    def start(self, spec: CommandSpec) -> object:
        raise AssertionError("git never starts a long-lived process")


def test_every_git_command_is_a_fixed_argv_with_a_locked_down_environment(
    repo: RunContext,
) -> None:
    recorder = Recorder(repo.backend)
    port = GitPort(repo.workspace, recorder, repo.settings.git, repo.events)  # type: ignore[arg-type]
    repo.workspace.write_file("a.txt", "x\n")

    port.status()
    port.checkpoint("m")
    port.diff(None)

    assert recorder.specs
    for spec in recorder.specs:
        assert spec.argv[0] == "git" and spec.cwd == repo.workspace.root
        for needle in ("core.hooksPath=/dev/null", "core.fsmonitor=false", "--no-pager"):
            assert needle in spec.argv
        assert "credential.helper=" in spec.argv
        assert spec.env["GIT_TERMINAL_PROMPT"] == "0"
        assert (
            spec.env["GIT_CONFIG_NOSYSTEM"] == "1" and spec.env["GIT_CONFIG_GLOBAL"] == "/dev/null"
        )
        assert spec.network is False
        assert "push" not in spec.argv and "fetch" not in spec.argv


# -- reading history --------------------------------------------------------------------------


@pytest.fixture
def history(ctx: RunContext) -> RunContext:
    root = ctx.workspace.root
    init_repo(root)
    make_commit(root, {"a.py": "x = 1\n", "b.py": "y = 1\n"}, "add a and b", "Ana", 5)
    make_commit(root, {"a.py": "x = 2\n"}, "change a", "Bo", 3)
    make_commit(root, {"b.py": "y = 2\n", "c.py": "z = 1\n"}, "change b, add c", "Ana", 1)
    return ctx


def test_log_show_and_blame_are_bounded_text(history: RunContext) -> None:
    git = history.git

    log = git.log(max_count=2)
    assert "change b, add c" in log and "change a" in log and "add a and b" not in log
    assert "Bo" in git.log(path="a.py")
    shown = git.show("HEAD~1")
    assert "change a" in shown and "+x = 2" in shown
    blame = git.blame("a.py")
    assert "Bo" in blame and "x = 2" in blame
    assert "Ana" in git.blame("b.py", start=1, end=1)


def test_history_search_finds_when_text_was_introduced(history: RunContext) -> None:
    by_string = history.git.search("x = 2", mode="string")
    by_regex = history.git.search(r"z = [0-9]", mode="regex")

    assert "change a" in by_string and "add a and b" not in by_string
    assert "change b, add c" in by_regex


def test_diff_between_refs_is_a_range_not_the_work_tree(history: RunContext) -> None:
    history.workspace.write_file("a.py", "x = 99\n")  # uncommitted: must not appear

    diff = history.git.diff_refs("HEAD~2", "HEAD~1")

    assert "+x = 2" in diff and "x = 99" not in diff
    assert "a.py" in history.git.diff_refs("HEAD~2", "HEAD", stat=True)


@pytest.mark.parametrize("ref", ["--output=/tmp/x", "-p", "HEAD;ls", "a b", ""])
def test_refs_that_could_be_flags_or_shell_are_refused(history: RunContext, ref: str) -> None:
    with pytest.raises(GitError, match="ref"):
        history.git.show(ref)


def test_agents_cannot_aim_git_at_its_own_metadata(history: RunContext) -> None:
    with pytest.raises(ValueError, match=r"\.git"):
        history.git.log(path=".git/config")
    with pytest.raises(ValueError, match="engineering-team"):
        history.git.blame(".engineering-team/runs/x")


# -- finding where a branch left its base, and undoing what a failed attempt did -----------------


def test_the_merge_base_is_where_the_branch_left_the_other_one(repo: RunContext) -> None:
    root = repo.workspace.root
    base = raw(root, "rev-parse", "HEAD")
    raw(root, "checkout", "-q", "-b", "feature")
    repo.workspace.write_file("a.txt", "a\n")
    repo.git.checkpoint("on feature")
    raw(root, "checkout", "-q", "main")
    repo.workspace.write_file("b.txt", "b\n")
    repo.git.checkpoint("on main")
    raw(root, "checkout", "-q", "feature")

    assert repo.git.merge_base("main") == base
    assert repo.git.resolves("main") and repo.git.resolves("HEAD")
    assert not repo.git.resolves("no-such-branch")
    with pytest.raises(GitError, match="not a usable ref"):
        repo.git.merge_base("--output=x")


def test_restore_puts_back_what_changed_since_a_commit_and_removes_what_was_added(
    repo: RunContext,
) -> None:
    repo.workspace.write_file("keep.txt", "one\n")
    repo.workspace.write_file("gone.txt", "two\n")
    sha = repo.git.checkpoint("base")
    repo.workspace.write_file("keep.txt", "CHANGED\n")
    repo.workspace.write_file("new.txt", "new\n")
    (repo.workspace.root / "gone.txt").unlink()

    restored = repo.git.restore(sha)

    assert sorted(restored) == ["gone.txt", "keep.txt", "new.txt"]
    assert (repo.workspace.root / "keep.txt").read_text() == "one\n"
    assert (repo.workspace.root / "gone.txt").read_text() == "two\n"
    assert not (repo.workspace.root / "new.txt").exists()
    assert repo.git.changes(sha) == [] and repo.git.restore(sha) == []
    assert raw(repo.workspace.root, "rev-parse", "HEAD") == sha  # no history was made or lost
