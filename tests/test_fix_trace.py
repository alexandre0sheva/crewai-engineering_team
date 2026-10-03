"""The stack-trace parser behind ``fix --trace-file``: Python, Node, and Java traces."""

from __future__ import annotations

from pathlib import Path

from repo_fixtures import write_tree

from engineering_team.modes.trace import parse_trace, suspect_files

PYTHON_TRACE = """\
2026-10-03 12:00:01 INFO starting
Traceback (most recent call last):
  File "/home/dev/shop/app/main.py", line 12, in main
    print(add(load(), "x"))
  File "/home/dev/shop/app/store.py", line 4, in add
    return items[0] + text
  File "/usr/lib/python3.12/site-packages/lib/util.py", line 9, in helper
    raise KeyError("boom")
IndexError: list index out of range
"""

CHAINED_PYTHON = """\
Traceback (most recent call last):
  File "app/a.py", line 1, in first
    raise ValueError("inner")
ValueError: inner

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "app/b.py", line 2, in second
    raise RuntimeError("outer")
RuntimeError: outer
"""

NODE_TRACE = """\
TypeError: Cannot read properties of undefined (reading 'length')
    at total (/work/shop/src/cart.js:12:15)
    at Object.<anonymous> (/work/shop/src/cart.test.js:5:3)
    at Module._compile (node:internal/modules/cjs/loader:1105:14)
    at /work/shop/node_modules/jest/runner.js:44:9
"""

JAVA_TRACE = """\
Exception in thread "main" java.lang.NullPointerException: Cannot invoke "String.length()"
\tat com.shop.Cart.total(Cart.java:12)
\tat com.shop.Main.main(Main.java:5)
\tat java.base/java.lang.Thread.run(Thread.java:833)
Caused by: java.io.IOException: disk
\tat com.shop.Store.load(Store.java:30)
"""

TREE = {
    "app/main.py": "x = 1\n",
    "app/store.py": "y = 1\n",
    "src/cart.js": "z = 1\n",
    "src/cart.test.js": "z = 1\n",
    "src/main/java/com/shop/Cart.java": "class Cart {}\n",
    "src/main/java/com/shop/Main.java": "class Main {}\n",
}


def test_a_python_traceback_gives_the_error_and_the_frames_innermost_first() -> None:
    trace = parse_trace(PYTHON_TRACE)

    assert trace is not None and trace.language == "python"
    assert (trace.error, trace.message) == ("IndexError", "list index out of range")
    assert [(f.file, f.line, f.function) for f in trace.frames][:2] == [
        ("/usr/lib/python3.12/site-packages/lib/util.py", 9, "helper"),
        ("/home/dev/shop/app/store.py", 4, "add"),
    ]


def test_a_chained_python_traceback_is_read_from_its_last_block() -> None:
    trace = parse_trace(CHAINED_PYTHON)

    assert trace is not None
    assert (trace.error, trace.message) == ("RuntimeError", "outer")
    assert [f.file for f in trace.frames] == ["app/b.py"]


def test_a_node_stack_gives_the_error_and_skips_node_internals() -> None:
    trace = parse_trace(NODE_TRACE)

    assert trace is not None and trace.language == "node"
    assert trace.error == "TypeError"
    assert trace.message == "Cannot read properties of undefined (reading 'length')"
    assert [(f.file, f.line, f.function) for f in trace.frames] == [
        ("/work/shop/src/cart.js", 12, "total"),
        ("/work/shop/src/cart.test.js", 5, "Object.<anonymous>"),
        ("/work/shop/node_modules/jest/runner.js", 44, ""),
    ]


def test_a_java_stack_gives_the_exception_and_its_frames() -> None:
    trace = parse_trace(JAVA_TRACE)

    assert trace is not None and trace.language == "java"
    assert trace.error == "java.lang.NullPointerException"
    assert trace.message.startswith("Cannot invoke")
    assert [(f.function, f.file, f.line) for f in trace.frames][:2] == [
        ("com.shop.Cart.total", "Cart.java", 12),
        ("com.shop.Main.main", "Main.java", 5),
    ]


def test_text_that_is_not_a_trace_parses_to_nothing() -> None:
    assert parse_trace("the button does nothing when I click it") is None
    assert parse_trace("") is None


def test_suspects_are_project_files_found_even_when_the_trace_came_from_another_checkout(
    tmp_path: Path,
) -> None:
    root = write_tree(tmp_path / "shop", TREE)

    python = suspect_files(parse_trace(PYTHON_TRACE), root)
    node = suspect_files(parse_trace(NODE_TRACE), root)
    java = suspect_files(parse_trace(JAVA_TRACE), root)

    assert python == ["app/store.py", "app/main.py"]  # not the installed library
    assert node == ["src/cart.js", "src/cart.test.js"]  # not node_modules
    assert java == [
        "src/main/java/com/shop/Cart.java",
        "src/main/java/com/shop/Main.java",
    ]
    assert suspect_files(None, root) == []


def test_a_trace_renders_as_a_short_brief_with_the_project_files_marked() -> None:
    trace = parse_trace(PYTHON_TRACE)
    assert trace is not None

    text = trace.render(["app/store.py"])

    assert "IndexError: list index out of range" in text
    assert "Project files in the trace: app/store.py" in text
