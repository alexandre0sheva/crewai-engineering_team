from __future__ import annotations

import pytest
from conftest import Toolbox


def test_read_file_range_numbers_lines_and_says_where_to_continue(toolbox: Toolbox) -> None:
    toolbox.write("app.py", "".join(f"line {n}\n" for n in range(1, 101)))

    result = toolbox("Read File Range", path="app.py", start_line=10, max_lines=5)

    lines = result.splitlines()
    assert lines[0] == "app.py lines 10-14 of 100"
    assert lines[1].strip().startswith("10\tline 10") and lines[5].strip().startswith("14\tline 14")
    assert "86 more line(s); continue with start_line=15" in result


def test_read_file_range_errors_name_the_fix(toolbox: Toolbox) -> None:
    toolbox.write("short.txt", "a\nb\n")
    toolbox.write("src/config.py", "x = 1\n")
    (toolbox.workspace.root / "blob.bin").write_bytes(b"\x00\x01binary")

    assert "has 2 line(s); start_line=9 is past the end" in toolbox(
        "Read File Range", path="short.txt", start_line=9
    )
    missing = toolbox("Read File Range", path="src/confg.py")
    assert missing.startswith("ERROR:") and "Did you mean: src/config.py" in missing
    assert "binary" in toolbox("Read File Range", path="blob.bin")
    assert "is a directory" in toolbox("Read File Range", path="src")


def test_read_project_file_points_oversized_files_at_ranges(toolbox: Toolbox) -> None:
    toolbox.write("big.txt", "x" * 300_000)

    result = toolbox("Read Project File", path="big.txt")

    assert result.startswith("ERROR:") and "Read File Range" in result


def test_read_many_files_batches_ranges_and_reports_bad_paths_inline(toolbox: Toolbox) -> None:
    toolbox.write("a.py", "".join(f"a{n}\n" for n in range(1, 50)))
    toolbox.write("b.txt", "hello\n")

    result = toolbox("Read Many Files", files=["a.py:3-5", "b.txt", "nope.txt"])

    assert "=== a.py lines 3-5 of 49 ===" in result and "a4" in result and "a6" not in result
    assert "=== b.txt lines 1-1 of 1 ===" in result and "hello" in result
    assert "=== nope.txt ===\nERROR: File not found" in result


def test_read_many_files_enforces_a_total_size_cap(toolbox: Toolbox) -> None:
    for number in range(8):
        toolbox.write(f"f{number}.txt", ("y" * 99 + "\n") * 390)

    result = toolbox("Read Many Files", files=[f"f{number}.txt" for number in range(8)])

    assert len(result) < 70_000
    assert "skipped: total size limit reached" in result or "truncated" in result


def test_file_info_reports_size_lines_and_binary(toolbox: Toolbox) -> None:
    toolbox.write("src/app.py", "a = 1\nb = 2\n")
    (toolbox.workspace.root / "logo.png").write_bytes(b"\x89PNG\x00\x00")

    text = toolbox("File Info", path="src/app.py")
    binary = toolbox("File Info", path="logo.png")

    assert "type: file" in text and "lines: 2" in text and "binary: no" in text
    assert "outline supported" in text
    assert "binary: yes" in binary
    assert "entries: 1" in toolbox("File Info", path="src")


def test_project_tree_annotates_directories_and_skips_heavy_ones(toolbox: Toolbox) -> None:
    toolbox.write("src/a.py", "x" * 2048)
    toolbox.write("src/pkg/b.py", "y")
    toolbox.write("node_modules/dep/index.js", "ignored")
    toolbox.write(".gitignore", "*.log\n")
    toolbox.write("debug.log", "ignored")

    tree = toolbox("Project Tree", path=".", max_depth=3)

    assert tree.splitlines()[0].startswith(".: 3 file(s)")  # src/a.py, src/pkg/b.py, .gitignore
    assert "src/  (2 files, 2.0K)" in tree
    assert "  pkg/  (1 files, 1B)" in tree
    assert "node_modules" not in tree and "debug.log" not in tree


def test_project_tree_depth_limit_hides_deeper_levels(toolbox: Toolbox) -> None:
    toolbox.write("a/b/c/deep.txt", "x")

    shallow = toolbox("Project Tree", path=".", max_depth=2)

    assert "b/" in shallow and "c/" not in shallow


def test_command_logs_are_readable_but_other_controller_state_is_not(toolbox: Toolbox) -> None:
    toolbox.write("hi.py", "print('hello log')\n")
    result = toolbox("Run Project Command", command="python hi.py")
    log_path = result.rsplit("full log: ", 1)[1].split()[0]

    log = toolbox("Read File Range", path=log_path)

    assert "hello log" in log
    assert toolbox("Read File Range", path=".engineering-team/owner.json").startswith("ERROR:")
    assert toolbox("Write Project File", path=log_path, content="x").startswith("ERROR:")


@pytest.mark.parametrize("tool", ["Read File Range", "File Info"])
def test_paths_cannot_escape_the_workspace(toolbox: Toolbox, tool: str) -> None:
    assert toolbox(tool, path="../outside.txt").startswith("ERROR:")
