"""Triage's deterministic signals: repository, traces, failing tests, references, paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from git_helpers import init_repo

from engineering_team.intake.signals import (
    collect,
    existing_path,
    failing_tests,
    is_question,
    keyword_hits,
    mentioned_paths,
)


def project(root: Path) -> Path:
    for relative, body in {
        "app/main.py": "def main():\n    return 1\n",
        "app/util.py": "X = 1\n",
        "tests/test_main.py": "def test_main():\n    assert True\n",
        "pyproject.toml": "[project]\nname = 'demo'\n",
    }.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


# -- the repository --------------------------------------------------------------------------


def test_no_directory_means_no_repository() -> None:
    signals = collect("Build me a small CLI app that keeps notes.")

    assert signals.repo is None
    assert not signals.git_repo and not signals.existing_code
    assert signals.paths == []


def test_an_empty_directory_has_no_existing_code(tmp_path: Path) -> None:
    signals = collect("Write a converter.", tmp_path)

    assert signals.repo == str(tmp_path.resolve())
    assert not signals.existing_code and not signals.git_repo


def test_a_project_without_git_has_code_languages_and_tests(tmp_path: Path) -> None:
    signals = collect("Fix it.", project(tmp_path))

    assert signals.existing_code and not signals.git_repo
    assert signals.repo_files == 4
    assert signals.languages[0] == "Python"
    assert signals.test_files == 1
    assert any("tests" in line for line in signals.summary)


def test_a_git_repository_is_recognised(tmp_path: Path) -> None:
    init_repo(project(tmp_path))

    assert collect("Fix it.", tmp_path).git_repo


def test_a_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a directory"):
        collect("Fix it.", tmp_path / "nowhere")


# -- what the request contains --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        'Traceback (most recent call last):\n  File "a.py", line 3, in f\nKeyError: 1',
        "TypeError: x is undefined\n    at total (/app/src/cart.js:12:5)\n    at main (/i.js:3:1)",
        "panic: runtime error: index out of range\n\ngoroutine 1 [running]:\nmain.main()",
        'Exception in thread "main" java.lang.NullPointerException\n    at A.run(A.java:10)',
        "thread 'main' panicked at src/main.rs:4:5:\nattempt to divide by zero",
    ],
    ids=["python", "node", "go", "java", "rust"],
)
def test_stack_traces_of_several_languages_are_found(text: str) -> None:
    assert collect(text).stack_trace


def test_an_error_message_alone_is_not_a_stack_trace() -> None:
    assert not collect("I get KeyError: 'name' when I run it.").stack_trace


def test_failing_tests_are_named_by_node_id_failed_lines_and_failing_names() -> None:
    text = (
        "tests/test_cart.py::test_empty_cart[0] fails\n"
        "FAILED tests/test_report.py::test_total - assert 1 == 2\n"
        "--- FAIL: TestParse (0.00s)\n"
        "test_rounding is broken since the upgrade\n"
        "test_helpers.py is fine\n"
    )

    assert failing_tests(text) == [
        "tests/test_cart.py::test_empty_cart[0]",
        "tests/test_report.py::test_total",
        "TestParse",
        "test_rounding",
    ]


def test_a_test_name_without_a_failure_word_is_not_failing() -> None:
    assert failing_tests("Rename test_rounding to test_round_half_even.") == []


@pytest.mark.parametrize(
    ("text", "refs"),
    [
        ("diff --git a/x.py b/x.py\n@@ -1 +1 @@\n-a\n+b", ["diff"]),
        ("Please look over PR #17.", ["pull_request"]),
        ("See https://github.com/o/r/pull/42 for the change.", ["pull_request"]),
        ("Review the work on the feature/export branch.", ["branch"]),
        ("What changed against main since Monday?", ["branch"]),
        ("It broke in commit 3f2a9c1d.", ["commit"]),
        ("Add a login page.", []),
    ],
)
def test_references_to_a_change_are_found(text: str, refs: list[str]) -> None:
    assert collect(text).refs == refs
    assert collect(text).diff_ref is bool(refs)


@pytest.mark.parametrize(
    ("text", "question"),
    [
        ("Where does the app read its settings?", True),
        ("Explain how the cache is invalidated.", True),
        ("Is there a way to run only one test", True),
        ("The parser drops the last line?", True),
        ("Can you add a --json flag?", False),
        ("Please add a --json flag?", False),
        ("Add a --json flag.", False),
        ("", False),
    ],
)
def test_the_question_form(text: str, question: bool) -> None:
    assert is_question(text) is question


def test_keyword_groups_list_the_words_that_matched() -> None:
    hits = keyword_hits("Upgrade requests, refactor the client and add unit tests. Docs too.")

    assert hits["upgrade_deps"] == ["upgrade"]
    assert hits["refactor"] == ["refactor"]
    assert hits["tests"] == ["add unit tests"]
    assert hits["docs"] == ["docs"]
    assert "security_audit" not in hits


def test_security_sensitive_words_are_collected() -> None:
    assert collect("Store the passwords and the session tokens.").risk_terms == [
        "passwords",
        "session",
        "tokens",
    ]


# -- paths -----------------------------------------------------------------------------------


def test_only_paths_that_exist_inside_the_repository_are_kept(tmp_path: Path) -> None:
    root = project(tmp_path / "repo")
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    text = (
        "See `app/main.py:12`, ./app/util.py and tests/, not app/missing.py, "
        "../secret.txt, /etc/passwd, https://example.com/app/main.py or .git/config."
    )

    assert mentioned_paths(text, root.resolve()) == ["app/main.py", "app/util.py", "tests"]


def test_a_diffs_a_and_b_prefixes_are_dropped(tmp_path: Path) -> None:
    root = project(tmp_path).resolve()

    assert existing_path("a/app/main.py", root) == "app/main.py"
    assert existing_path("b/app/util.py", root) == "app/util.py"
    assert existing_path("a/app/nothing.py", root) is None
