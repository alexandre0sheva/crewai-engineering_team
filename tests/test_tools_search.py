from __future__ import annotations

import os
import time

from conftest import Toolbox


def _project(box: Toolbox) -> None:
    box.write(
        "src/app.py",
        "def main():\n    print('Hello')\n    helper()\n\n\ndef helper():\n    return 1\n",
    )
    box.write("src/util.py", "from app import helper\n\nVALUE = helper()\n")
    box.write("tests/test_app.py", "def test_main():\n    assert True  # hello\n")
    box.write("web/app.js", "export function render() {\n  return 'hello';\n}\n")
    box.write(".gitignore", "ignored/\n*.log\n")
    box.write("ignored/secret.py", "hello = 1\n")
    box.write("run.log", "hello\n")
    box.write("node_modules/pkg/index.js", "hello\n")
    (box.workspace.root / "data.bin").write_bytes(b"\x00hello\x00")


def test_search_reports_counts_then_file_line_matches(toolbox: Toolbox) -> None:
    _project(toolbox)

    result = toolbox("Search Project Files", pattern="helper")

    lines = result.splitlines()
    assert lines[0] == "4 match(es) in 2 file(s) for 'helper'"
    assert "src/app.py:3:     helper()" in lines
    assert "src/util.py:3: VALUE = helper()" in lines


def test_search_respects_gitignore_heavy_dirs_and_binaries(toolbox: Toolbox) -> None:
    _project(toolbox)

    result = toolbox("Search Project Files", pattern="hello")

    assert "ignored/" not in result and "run.log" not in result
    assert "node_modules" not in result and "data.bin" not in result
    assert "src/app.py:2" in result and "web/app.js:2" in result


def test_search_case_modes(toolbox: Toolbox) -> None:
    _project(toolbox)

    smart_lower = toolbox("Search Project Files", pattern="hello")  # matches 'Hello' too
    smart_upper = toolbox("Search Project Files", pattern="Hello")  # capitals: case-sensitive
    sensitive = toolbox("Search Project Files", pattern="hello", case="sensitive")

    assert "src/app.py:2" in smart_lower
    assert "src/app.py:2" in smart_upper and "web/app.js" not in smart_upper
    assert "src/app.py" not in sensitive and "web/app.js:2" in sensitive


def test_search_regex_include_exclude_and_context(toolbox: Toolbox) -> None:
    _project(toolbox)

    only_py = toolbox("Search Project Files", pattern=r"def \w+\(", regex=True, include=["*.py"])
    no_tests = toolbox(
        "Search Project Files", pattern="def ", exclude=["tests/**"], include=["*.py"]
    )
    with_context = toolbox("Search Project Files", pattern="helper()", path="src/app.py", context=1)

    assert "src/app.py:1:" in only_py and "tests/test_app.py:1:" in only_py
    assert "tests/" not in no_tests
    assert "src/app.py-2-     print('Hello')" in with_context  # context uses '-' separators
    assert "src/app.py:3:" in with_context


def test_search_caps_results_and_says_how_to_narrow(toolbox: Toolbox) -> None:
    toolbox.write("many.txt", "".join(f"needle {n}\n" for n in range(100)))

    result = toolbox("Search Project Files", pattern="needle", max_results=10)

    assert result.splitlines()[0].startswith("10 match(es) in 1 file(s)")
    assert "stopped at max_results=10" in result
    assert result.count("many.txt:") == 10


def test_search_errors_and_empty_results_give_hints(toolbox: Toolbox) -> None:
    _project(toolbox)

    bad = toolbox("Search Project Files", pattern="(", regex=True)
    empty = toolbox("Search Project Files", pattern="zzz-not-there")

    assert bad.startswith("ERROR:") and "regex=false" in bad
    assert empty.startswith("0 matches") and "case=insensitive" in empty
    assert toolbox("Search Project Files", pattern="x", case="loud").startswith("ERROR:")


def test_find_files_matches_globs_and_sorts(toolbox: Toolbox) -> None:
    _project(toolbox)
    old = toolbox.workspace.root / "src" / "app.py"
    os.utime(old, (time.time() - 1000, time.time() - 1000))

    by_name = toolbox("Find Files", pattern="*.py")
    recent = toolbox("Find Files", pattern="*.py", sort="recent")
    nested = toolbox("Find Files", pattern="src/**/*.py")

    assert by_name.splitlines()[0] == "3 file(s) match '*.py' (sorted by name)"
    assert [row.split()[0] for row in by_name.splitlines()[1:]] == [
        "src/app.py",
        "src/util.py",
        "tests/test_app.py",
    ]
    assert recent.splitlines()[-1].startswith("src/app.py")  # the old one is last
    assert nested.splitlines()[0].startswith("2 file(s)")
    assert "ignored/" not in toolbox("Find Files", pattern="*.py")
    assert toolbox("Find Files", pattern="*.zzz").startswith("0 files")


def test_project_outline_lists_symbols_for_several_languages(toolbox: Toolbox) -> None:
    toolbox.write(
        "svc/models.py",
        "MAX = 3\n\nclass User:\n    def save(self): ...\n\n    async def load(self): ...\n\n"
        "async def fetch(): ...\n",
    )
    toolbox.write(
        "svc/api.ts",
        "export interface Item { id: number }\nexport class Store {}\n"
        "export const add = async (a: number) => a;\nexport function remove() {}\n"
        "export type Id = string;\n",
    )
    toolbox.write(
        "svc/main.go", "type Server struct {}\nfunc (s *Server) Start() {}\nfunc main() {}\n"
    )
    toolbox.write("svc/lib.rs", "pub struct Config {}\npub fn run() {}\nimpl Config {}\n")
    toolbox.write("svc/App.java", "public class App {\n  void run() {}\n}\ninterface Port {}\n")

    result = toolbox("Project Outline", path="svc")

    assert result.splitlines()[0] == "5 source file(s) with symbols under svc"
    assert "class User  L3" in result and "method User.save  L4" in result
    assert "method User.load  L6" in result and "function fetch  L8" in result
    assert "const MAX  L1" in result
    assert "interface Item  L1" in result and "class Store  L2" in result
    assert (
        "function add  L3" in result and "function remove  L4" in result and "type Id  L5" in result
    )
    assert "struct Server  L1" in result and "function Start  L2" in result
    assert "struct Config  L1" in result and "fn run  L2" in result
    assert "class App  L1" in result and "interface Port  L4" in result


def test_project_outline_survives_python_syntax_errors(toolbox: Toolbox) -> None:
    toolbox.write("broken.py", "def ok():\n    pass\n\ndef broken(:\n")

    assert "function ok  L1" in toolbox("Project Outline", path="broken.py")


def test_repo_map_ranks_by_how_often_symbols_are_referenced(toolbox: Toolbox) -> None:
    toolbox.write("core/engine.py", "class Engine:\n    pass\n\ndef start_engine():\n    pass\n")
    toolbox.write("core/rare.py", "def seldom_used():\n    pass\n")
    for number in range(5):
        toolbox.write(
            f"app/feature_{number}.py",
            "from core.engine import Engine, start_engine\n\n"
            f"def feature_{number}():\n    return Engine(), start_engine()\n",
        )
    toolbox.write("zzz/unreferenced.py", "def lonely():\n    pass\n")

    result = toolbox("Repo Map", path=".", size=2000)

    order = [line for line in result.splitlines() if line and not line.startswith(" ")]
    assert order[1] == "core/engine.py"  # referenced by five other files
    assert order.index("core/engine.py") < order.index("core/rare.py")
    assert "class Engine  L1  (used in 5 files)" in result
    assert "function start_engine  L4  (used in 5 files)" in result


def test_repo_map_respects_the_token_budget(toolbox: Toolbox) -> None:
    for number in range(60):
        toolbox.write(
            f"pkg/mod_{number}.py", "".join(f"def func_{number}_{n}(): ...\n" for n in range(10))
        )

    small = toolbox("Repo Map", size=400)

    assert len(small) < 2400
    assert "more file(s) not shown" in small


def test_a_5000_file_tree_is_navigable_without_reading_whole_files(toolbox: Toolbox) -> None:
    root = toolbox.workspace.root
    for package in range(50):
        directory = root / "src" / f"pkg_{package:02d}"
        directory.mkdir(parents=True)
        for module in range(100):
            body = f"def pkg_{package:02d}_func_{module:03d}():\n    return {module}\n\n" + "".join(
                f"# filler line {n}\n" for n in range(30)
            )
            (directory / f"mod_{module:03d}.py").write_text(body, encoding="utf-8")
    (root / "src" / "pkg_07" / "mod_042.py").write_text(
        "def needle_function():\n    return 'found me'\n" + "# filler\n" * 200, encoding="utf-8"
    )
    (root / "src" / "pkg_08" / "mod_000.py").write_text(
        "from pkg_07.mod_042 import needle_function\n\nneedle_function()\n", encoding="utf-8"
    )
    started = time.monotonic()

    tree = toolbox("Project Tree", path=".", max_depth=2)
    repo = toolbox("Repo Map", size=800)
    hits = toolbox("Search Project Files", pattern="def needle_function")
    piece = toolbox("Read File Range", path="src/pkg_07/mod_042.py", start_line=1, max_lines=2)

    assert "5000 file(s)" in tree.splitlines()[0] or "5001 file(s)" in tree.splitlines()[0]
    assert "src/pkg_07/mod_042.py" in repo  # the referenced module outranks 4,999 others
    assert hits.splitlines()[0].startswith("1 match(es) in 1 file(s)")
    assert "return 'found me'" in piece and "# filler" not in piece
    # Orientation costs a few thousand characters, not the megabytes of the tree.
    assert len(tree) + len(repo) + len(hits) + len(piece) < 12_000
    assert time.monotonic() - started < 30
