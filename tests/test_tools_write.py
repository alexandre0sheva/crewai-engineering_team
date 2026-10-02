from __future__ import annotations

from collections.abc import Callable

from conftest import Toolbox

from engineering_team.tools import WriteScope

APP = "def greet():\n    return 'hi'\n\n\ndef bye():\n    return 'bye'\n"

DIFF = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def greet():
-    return 'hi'
+    name = 'world'
+    return f'hi {name}'
@@ -5,2 +6,2 @@
 def bye():
-    return 'bye'
+    return 'goodbye'
"""


def test_apply_patch_applies_a_multi_hunk_unified_diff(toolbox: Toolbox) -> None:
    toolbox.write("app.py", APP)

    result = toolbox("Apply Patch", patch=DIFF)

    assert toolbox.read("app.py") == (
        "def greet():\n    name = 'world'\n    return f'hi {name}'\n\n\n"
        "def bye():\n    return 'goodbye'\n"
    )
    assert result.splitlines()[0] == "Applied changes to 1 file(s):"
    assert "modified app.py (+3 -2, new lines 1-3, 6-7)" in result


def test_a_bad_hunk_leaves_every_file_untouched(toolbox: Toolbox) -> None:
    toolbox.write("app.py", APP)
    toolbox.write("other.py", "x = 1\n")
    patch = (
        "--- a/other.py\n+++ b/other.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
        "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n def greet():\n"
        "-    return 'WRONG'\n+    return 'x'\n"
    )

    result = toolbox("Apply Patch", patch=patch)

    assert result.startswith("ERROR: Hunk 1 for app.py does not match")
    assert "nothing was changed" in result
    assert toolbox.read("other.py") == "x = 1\n" and toolbox.read("app.py") == APP


def test_hunks_apply_where_the_line_numbers_have_drifted(toolbox: Toolbox) -> None:
    toolbox.write("app.py", "# new header\n# another\n" + APP)

    toolbox("Apply Patch", patch=DIFF)

    assert "return 'goodbye'" in toolbox.read("app.py")


def test_a_patch_can_create_and_delete_files(toolbox: Toolbox) -> None:
    toolbox.write("old.txt", "bye\n")
    patch = (
        "--- /dev/null\n+++ b/docs/new.md\n@@ -0,0 +1,2 @@\n+# Title\n+body\n"
        "--- a/old.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n"
    )

    result = toolbox("Apply Patch", patch=patch)

    assert toolbox.read("docs/new.md") == "# Title\nbody\n"
    assert not (toolbox.workspace.root / "old.txt").exists()
    assert "created docs/new.md (+2 -0, new lines 1-2)" in result and "deleted old.txt" in result


def test_patch_errors_explain_the_format(toolbox: Toolbox) -> None:
    assert "No file sections found" in toolbox("Apply Patch", patch="not a diff")
    missing = toolbox("Apply Patch", patch="--- a/nope.py\n+++ b/nope.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert "does not exist" in missing and "Find Files" in missing
    assert "exactly one of" in toolbox("Apply Patch")


def test_edits_apply_atomically_and_chain_within_a_file(toolbox: Toolbox) -> None:
    toolbox.write("app.py", APP)
    toolbox.write("cfg.txt", "debug=false\n")

    result = toolbox(
        "Apply Patch",
        edits=[
            {"path": "app.py", "old": "'hi'", "new": "'hello'"},
            {"path": "app.py", "old": "'hello'", "new": "'hello!'"},  # sees the first edit
            {"path": "cfg.txt", "old": "false", "new": "true"},
        ],
    )

    assert "return 'hello!'" in toolbox.read("app.py") and toolbox.read("cfg.txt") == "debug=true\n"
    assert "modified app.py (+1 -1, new lines 2)" in result


def test_an_edit_with_the_wrong_occurrence_count_changes_nothing(toolbox: Toolbox) -> None:
    toolbox.write("app.py", APP)
    toolbox.write("cfg.txt", "a\n")

    result = toolbox(
        "Apply Patch",
        edits=[
            {"path": "cfg.txt", "old": "a", "new": "b"},
            {"path": "app.py", "old": "return", "new": "yield"},  # appears twice
        ],
    )

    assert "Edit 2: expected 1 occurrence(s)" in result and "found 2" in result
    assert toolbox.read("cfg.txt") == "a\n" and toolbox.read("app.py") == APP
    ok = toolbox(
        "Apply Patch",
        edits=[{"path": "app.py", "old": "return", "new": "yield", "expected_replacements": 2}],
    )
    assert ok.startswith("Applied") and toolbox.read("app.py").count("yield") == 2


def test_apply_patch_respects_write_scope_for_every_file(
    make_toolbox: Callable[..., Toolbox],
) -> None:
    box = make_toolbox(write_scope=WriteScope(allow=("src/api/**",)))
    box.write("src/api/a.py", "a = 1\n")
    box.write("src/web/b.py", "b = 1\n")

    result = box(
        "Apply Patch",
        edits=[
            {"path": "src/api/a.py", "old": "1", "new": "2"},
            {"path": "src/web/b.py", "old": "1", "new": "2"},
        ],
    )

    assert result.startswith("ERROR:") and "outside your write scope" in result
    assert box.read("src/api/a.py") == "a = 1\n" and box.read("src/web/b.py") == "b = 1\n"


def test_a_failed_write_rolls_back_files_already_written(toolbox: Toolbox) -> None:
    toolbox.write("a.txt", "changed\n")
    (toolbox.workspace.root / "blocked").write_text("a file where a directory is needed\n")
    patch = (
        "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-changed\n+again\n"
        "--- /dev/null\n+++ b/blocked/new.txt\n@@ -0,0 +1 @@\n+x\n"
    )

    failed = toolbox("Apply Patch", patch=patch)

    assert failed.startswith("ERROR:")
    assert toolbox.read("a.txt") == "changed\n"  # a.txt was written first, then restored


def test_move_and_copy_paths(toolbox: Toolbox) -> None:
    toolbox.write("src/a.py", "a\n")
    toolbox.write("src/pkg/b.py", "b\n")

    assert (
        toolbox("Move Path", source="src/a.py", destination="lib/a.py")
        == "Moved src/a.py to lib/a.py."
    )
    assert (
        toolbox("Copy Path", source="src/pkg", destination="copy/pkg")
        == "Copied src/pkg to copy/pkg."
    )

    root = toolbox.workspace.root
    assert (root / "lib/a.py").is_file() and not (root / "src/a.py").exists()
    assert (root / "copy/pkg/b.py").is_file() and (root / "src/pkg/b.py").is_file()


def test_move_and_copy_refuse_overwrites_cycles_and_missing_sources(toolbox: Toolbox) -> None:
    toolbox.write("a.txt", "a\n")
    toolbox.write("b.txt", "b\n")
    toolbox.write("dir/x.txt", "x\n")

    assert "already exists" in toolbox("Move Path", source="a.txt", destination="b.txt")
    assert toolbox.read("b.txt") == "b\n"
    assert toolbox("Move Path", source="a.txt", destination="b.txt", overwrite=True).startswith(
        "Moved"
    )
    assert "into itself" in toolbox("Move Path", source="dir", destination="dir/sub")
    assert "does not exist" in toolbox("Copy Path", source="ghost", destination="x")
    assert toolbox("Move Path", source=".", destination="elsewhere").startswith("ERROR:")


def test_move_checks_scope_on_both_ends(make_toolbox: Callable[..., Toolbox]) -> None:
    box = make_toolbox(write_scope=WriteScope(allow=("mine/**",)))
    box.write("mine/a.txt", "a\n")
    box.write("theirs/b.txt", "b\n")

    assert box("Move Path", source="mine/a.txt", destination="theirs/a.txt").startswith("ERROR:")
    assert box("Move Path", source="theirs/b.txt", destination="mine/b.txt").startswith("ERROR:")
    assert box("Move Path", source="mine/a.txt", destination="mine/sub/a.txt").startswith("Moved")
    assert box("Make Directory", path="theirs/new").startswith("ERROR:")
    assert box("Make Directory", path="mine/new/deep") == "Directory ready: mine/new/deep"


def test_move_and_copy_cannot_touch_protected_paths(toolbox: Toolbox) -> None:
    toolbox.write("a.txt", "a\n")

    assert toolbox("Move Path", source="a.txt", destination=".git/a.txt").startswith("ERROR:")
    assert toolbox("Copy Path", source="a.txt", destination="../out.txt").startswith("ERROR:")
    assert toolbox("Move Path", source=".engineering-team/owner.json", destination="o").startswith(
        "ERROR:"
    )


def test_make_directory_is_idempotent_and_refuses_files(toolbox: Toolbox) -> None:
    toolbox.write("file.txt", "x")

    assert toolbox("Make Directory", path="a/b") == "Directory ready: a/b"
    assert toolbox("Make Directory", path="a/b") == "Directory ready: a/b"
    assert "exists and is a file" in toolbox("Make Directory", path="file.txt")


def test_workspace_changes_reports_added_modified_deleted_with_a_diff(
    make_context: Callable[..., object], tmp_path
) -> None:
    from engineering_team.runtime.context import RunContext
    from engineering_team.settings import load_settings
    from engineering_team.tools import build_tools
    from engineering_team.tools.workspace import ProjectWorkspace

    workspace = ProjectWorkspace.create(tmp_path / "resume")
    workspace.write_file("keep.txt", "same\n")
    workspace.write_file("edit.txt", "one\ntwo\nthree\n")
    workspace.write_file("gone.txt", "bye\n")
    ctx = RunContext.create(load_settings(), workspace)  # snapshots the workspace here
    box = {tool.name: tool for tool in build_tools(ctx)}
    assert box["Workspace Changes"].run() == "No changes since the run started (under .)."

    workspace.write_file("edit.txt", "one\n2\nthree\n")
    workspace.write_file("new/added.txt", "fresh\n")
    workspace.delete_path("gone.txt")
    report = box["Workspace Changes"].run()

    assert report.splitlines()[0] == "1 added, 1 modified, 1 deleted (under .)"
    assert {"A new/added.txt", "M edit.txt", "D gone.txt"} <= set(report.splitlines())
    assert "-two" in report and "+2" in report and "+fresh" in report and "-bye" in report
    assert "keep.txt" not in report
    assert (
        box["Workspace Changes"].run(path="new").splitlines()[0].startswith("1 added, 0 modified")
    )


def test_workspace_changes_truncates_long_diffs(toolbox: Toolbox) -> None:
    toolbox.write("big.txt", "".join(f"new {n}\n" for n in range(500)))

    report = toolbox("Workspace Changes", max_diff_lines=20)

    assert "diff truncated at 20" in report
