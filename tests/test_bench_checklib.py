"""``checklib.expect_tests_kept``: a check may require the original tests to survive, never that
the files stay byte-for-byte untouched (a new test belongs next to the ones it extends, and an
existing one may gain an assertion for the case that was fixed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team.bench.checklib import expect_tests_kept

ORIGINAL = """import unittest


class TestTotals(unittest.TestCase):
    def test_sum(self):
        self.assertEqual(sum([1, 2]), 3)


def helper():
    return 1
"""


def tree(root: Path, text: str) -> Path:
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_totals.py").write_text(text, encoding="utf-8")
    return root / "tests"


def kept(tmp_path: Path, mine: str | None) -> None:
    original = tree(tmp_path / "fixture", ORIGINAL)
    workspace = tmp_path / "workspace"
    if mine is not None:
        tree(workspace, mine)
    else:
        workspace.mkdir()
    expect_tests_kept(original, workspace / "tests")


def test_a_new_test_in_the_same_file_and_class_is_allowed(tmp_path: Path) -> None:
    added = ORIGINAL.replace(
        "\n\ndef helper", "\n    def test_new(self):\n        self.assertTrue(True)\n\n\ndef helper"
    )
    added = "import decimal\n" + added + "\n\ndef another():\n    return 2\n"

    kept(tmp_path, added)


def test_a_changed_test_is_reported_by_name(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match=r"TestTotals\.test_sum was changed"):
        kept(tmp_path, ORIGINAL.replace("3)", "4)"))


def test_a_removed_test_or_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match=r"TestTotals\.test_sum was removed"):
        kept(tmp_path / "a", ORIGINAL.replace("test_sum", "test_other"))
    with pytest.raises(AssertionError, match="was deleted"):
        kept(tmp_path / "b", None)


def test_a_changed_helper_is_reported(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="helper was changed"):
        kept(tmp_path, ORIGINAL.replace("return 1", "return 2"))


def test_a_test_may_gain_an_assertion_but_not_lose_or_change_one(tmp_path: Path) -> None:
    extended = ORIGINAL.replace(
        "        self.assertEqual(sum([1, 2]), 3)\n",
        "        self.assertEqual(sum([]), 0)\n"
        "        self.assertEqual(sum([1, 2]), 3)\n"
        "        self.assertEqual(sum([-1, 1]), 0)\n",
    )
    kept(tmp_path / "a", extended)
    with pytest.raises(AssertionError, match=r"test_sum was changed"):
        kept(tmp_path / "b", extended.replace("sum([1, 2]), 3", "sum([1, 2]), 4"))
    with pytest.raises(AssertionError, match=r"test_sum was changed"):
        kept(
            tmp_path / "c",
            ORIGINAL.replace("        self.assertEqual(sum([1, 2]), 3)\n", "        pass\n"),
        )


def test_a_test_whose_signature_or_decorator_changed_is_reported(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match=r"test_sum was changed"):
        kept(
            tmp_path / "a", ORIGINAL.replace("def test_sum(self):", "def test_sum(self, extra=1):")
        )
    with pytest.raises(AssertionError, match=r"test_sum was changed"):
        kept(
            tmp_path / "b",
            ORIGINAL.replace(
                "    def test_sum(self):",
                "    @unittest.skip('later')\n    def test_sum(self):",
            ),
        )
