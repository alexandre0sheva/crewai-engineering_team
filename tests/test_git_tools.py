"""The read-only ``git_read`` tools: Git Info, Git History Search, Git Diff Between Refs."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from conftest import Toolbox
from git_helpers import commit, init_repo, require_git

from engineering_team.testing import ScriptedLLM, ToolCall, run_agent_task
from engineering_team.tools import build_tools

MakeToolbox = Callable[..., Toolbox]


@pytest.fixture(autouse=True)
def needs_git() -> None:
    require_git()


@pytest.fixture
def box(make_toolbox: MakeToolbox) -> Toolbox:
    box = make_toolbox(groups=["git_read"])
    root = box.workspace.root
    init_repo(root)
    commit(root, {"a.py": "x = 1\n", "b.py": "y = 1\n"}, "add a and b", "Ana", 5)
    commit(root, {"a.py": "x = 2\n"}, "change a", "Bo", 3)
    commit(root, {"b.py": "y = 2\n", "c.py": "z = 1\n"}, "change b, add c", "Ana", 1)
    return box


def test_the_group_has_exactly_the_three_read_only_tools(make_toolbox: MakeToolbox) -> None:
    from engineering_team.tools import CATALOGUE

    group = [spec for spec in CATALOGUE if spec.group == "git_read"]

    assert [spec.name for spec in group] == [
        "Git Info",
        "Git History Search",
        "Git Diff Between Refs",
    ]
    assert all(
        spec.read_only and not spec.needs_network and not spec.needs_command_gate for spec in group
    )
    assert {tool.name for tool in make_toolbox(groups=["git_read"]).tools.values()} == {
        spec.name for spec in group
    }


def test_git_info_status_log_show_and_blame(box: Toolbox) -> None:
    (box.workspace.root / "a.py").write_text("x = 3\n", encoding="utf-8")
    box.write("new.txt", "n\n")

    status = box("Git Info", action="status")
    assert status.startswith("Branch:")
    assert " M a.py" in status and "?? new.txt" in status

    log = box("Git Info", action="log", max_count=2)
    assert log.startswith("2 commit(s)") and "change b, add c" in log and "add a and b" not in log
    assert "Bo" in box("Git Info", action="log", path="a.py")

    shown = box("Git Info", action="show", ref="HEAD~1")
    assert "change a" in shown and "+x = 2" in shown

    blame = box("Git Info", action="blame", path="b.py", start_line=1, end_line=1)
    assert "Ana" in blame and "y = 2" in blame

    diff = box("Git Info", action="diff")
    assert "+x = 3" in diff and "+n" in diff  # uncommitted edits and new files


def test_git_info_explains_a_bad_call(box: Toolbox) -> None:
    assert box("Git Info", action="push").startswith("ERROR: action must be one of")
    assert box("Git Info", action="blame").startswith("ERROR:") and "path" in box(
        "Git Info", action="blame"
    )
    assert box("Git Info", action="show", ref="--output=/tmp/x").startswith("ERROR:")
    assert "usable ref" in box("Git Info", action="show", ref="--output=/tmp/x")
    assert box("Git Info", action="log", path=".git/config").startswith("ERROR:")
    assert "not allowed" in box("Git Info", action="log", path=".git/config")


def test_git_info_in_a_project_without_a_repository_says_so(make_toolbox: MakeToolbox) -> None:
    empty = make_toolbox("plain", groups=["git_read"])

    result = empty("Git Info", action="status")

    assert result.startswith("ERROR:") and "not a Git repository" in result


def test_history_search_finds_when_text_changed_and_a_files_history(box: Toolbox) -> None:
    found = box("Git History Search", pattern="x = 2")
    assert "change a" in found and "add a and b" not in found
    assert (
        box("Git History Search", pattern="z = [0-9]", mode="regex").count("change b, add c") == 1
    )

    history = box("Git History Search", path="a.py")
    assert history.startswith("History of a.py") and "Last changed" in history
    assert "Bo" in history and "change a" in history

    assert box("Git History Search", pattern="nothing-like-this").startswith("No commit")
    assert box("Git History Search").startswith("ERROR:")  # needs a pattern or a path


def test_diff_between_refs_is_a_range_and_can_be_a_summary(box: Toolbox) -> None:
    box.write("a.py", "x = 99\n")  # uncommitted: not part of a ref-to-ref diff

    diff = box("Git Diff Between Refs", first="HEAD~2", second="HEAD~1")
    stat = box("Git Diff Between Refs", first="HEAD~2", second="HEAD", stat=True)

    assert "+x = 2" in diff and "x = 99" not in diff
    assert "a.py" in stat and "b.py" in stat and "file" in stat
    assert box("Git Diff Between Refs", first="HEAD~2", second="nonexistent").startswith("ERROR:")


def test_long_output_is_cut_and_says_how_to_narrow(box: Toolbox) -> None:
    box.write("big.txt", "line\n" * 60_000)
    commit(box.workspace.root, {}, "big", "Ana", 0)

    shown = box("Git Info", action="show", ref="HEAD")

    assert "truncated" in shown and len(shown) < 31_000


def test_an_agent_can_read_history_to_answer_a_question(
    make_context: Callable[..., object],
) -> None:
    ctx = make_context()  # type: ignore[operator]
    root = ctx.workspace.root
    init_repo(root)
    commit(root, {"a.py": "x = 1\n"}, "add a", "Ana", 5)
    commit(root, {"a.py": "x = 2\n"}, "tune the constant", "Bo", 1)
    llm = ScriptedLLM(
        [
            ToolCall("Git History Search", {"path": "a.py"}),
            "Bo last changed a.py ('tune the constant').",
        ]
    )

    result = run_agent_task(ctx, llm, tools=build_tools(ctx, groups=["git_read"]), max_iter=5)

    assert result.startswith("Bo last changed a.py")
    llm.assert_exhausted()
    assert "tune the constant" in llm.calls[1].prompt  # the agent saw the history it asked for
