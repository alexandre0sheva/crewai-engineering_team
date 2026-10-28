"""Fewer re-sent files and fewer failed tool calls: the read cache and clearer tool errors."""

from __future__ import annotations

from conftest import Toolbox

from engineering_team.board.models import CONTROLLER
from engineering_team.tools import build_tools


def test_a_file_read_again_unchanged_is_not_sent_again(toolbox: Toolbox) -> None:
    toolbox.write("src/app.py", "VALUE = 1\n")

    first = toolbox("Read Project File", path="src/app.py")
    again = toolbox("Read Project File", path="./src/app.py")

    assert first == "VALUE = 1\n"
    assert again.startswith("src/app.py is unchanged since you read it (read 1 of this session)")
    assert "Read File Range" in again
    assert toolbox("Read File Range", path="src/app.py").endswith("VALUE = 1")  # never cached


def test_a_changed_file_is_sent_again(toolbox: Toolbox) -> None:
    toolbox.write("src/app.py", "VALUE = 1\n")
    toolbox("Read Project File", path="src/app.py")

    toolbox("Write Project File", path="src/app.py", content="VALUE = 2\n")

    assert toolbox("Read Project File", path="src/app.py") == "VALUE = 2\n"
    assert "unchanged" in toolbox("Read Project File", path="src/app.py")


def test_the_read_cache_belongs_to_one_session(make_toolbox) -> None:  # type: ignore[no-untyped-def]
    first = make_toolbox("one")
    first.write("a.txt", "a\n")
    first("Read Project File", path="a.txt")

    other_session = Toolbox(first.ctx, build_tools(first.ctx, lazy=False))

    assert other_session("Read Project File", path="a.txt") == "a\n"


def test_a_patch_with_bare_hunk_headers_is_placed_by_its_context(toolbox: Toolbox) -> None:
    toolbox.write("app.py", "def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    patch = "--- a/app.py\n+++ b/app.py\n@@ def b\n def b():\n-    return 2\n+    return 3\n"

    result = toolbox("Apply Patch", patch=patch)

    assert result.startswith("Applied changes to 1 file(s)"), result
    assert toolbox.read("app.py").endswith("def b():\n    return 3\n")


def test_a_bare_hunk_must_match_exactly_one_place(toolbox: Toolbox) -> None:
    toolbox.write("app.py", "x = 1\ny = 2\nx = 1\n")

    ambiguous = toolbox("Apply Patch", patch="--- a/app.py\n+++ b/app.py\n@@\n-x = 1\n+x = 3\n")
    empty = toolbox("Apply Patch", patch="--- a/app.py\n+++ b/app.py\n@@\n+z = 0\n")

    assert ambiguous.startswith("ERROR: Hunk 1 for app.py has a bare '@@' header and matches 2")
    assert "has no context lines" in empty
    assert toolbox.read("app.py") == "x = 1\ny = 2\nx = 1\n"


def test_an_absolute_path_inside_the_workspace_is_accepted(toolbox: Toolbox) -> None:
    toolbox.write("src/app.py", "VALUE = 1\n")
    root = toolbox.workspace.root

    assert toolbox("Read File Range", path=str(root / "src" / "app.py")).endswith("VALUE = 1")
    assert not toolbox("List Project Files", path=str(root)).startswith("ERROR")
    outside = toolbox("Read File Range", path="/etc/hosts")
    assert outside.startswith("ERROR: Path must stay inside the project workspace: /etc/hosts.")
    assert "relative to the project root" in outside


def test_a_command_outside_the_allowlist_names_the_allowed_ones(toolbox: Toolbox) -> None:
    result = toolbox("Run Project Command", command="pwd")

    assert result.startswith("ERROR: Command 'pwd' is not allowed. Allowed: bun, cargo,")
    assert "python" in result and "shell builtins" in result


def test_moving_a_card_to_the_state_it_is_in_is_not_an_error(make_toolbox) -> None:  # type: ignore[no-untyped-def]
    box = make_toolbox(agent="backend")
    card = box.ctx.board.create_card(
        "API", kind="work_package", assignee="backend", stage="Build", status="ready"
    )
    box.ctx.board.move(card.id, "in_progress", actor=CONTROLLER)

    result = box("Move Card", card_id=card.id, status="in_progress")

    assert result == f"{card.id} is already in_progress; nothing to change."
