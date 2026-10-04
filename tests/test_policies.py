"""What a maintenance recipe may change: the path classes, the policies, and the write scopes."""

from __future__ import annotations

import pytest

from engineering_team.git.port import Change
from engineering_team.modes.policies import (
    POLICY_NAMES,
    SCOPES,
    evaluate,
    is_doc_path,
    is_manifest_path,
    is_test_path,
)
from engineering_team.settings import load_settings
from engineering_team.tools.scope import WriteScope


def change(path: str, status: str = "M", added: int = 3, removed: int = 1) -> Change:
    return Change(status, path, added, removed)


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_store.py", "app/test_x.py", "src/cart.test.js", "src/__tests__/a.ts",
        "pkg/store_test.go", "conftest.py", "tests/fixtures/data.json", "src/Cart.spec.ts",
        "app/__snapshots__/a.snap", "src/test/java/com/shop/CartTest.java",
    ],
)  # fmt: skip
def test_test_paths(path: str) -> None:
    assert is_test_path(path)


@pytest.mark.parametrize("path", ["app/store.py", "src/index.js", "README.md", "latest.py"])
def test_code_is_not_a_test_path(path: str) -> None:
    assert not is_test_path(path)


@pytest.mark.parametrize("path", ["README.md", "docs/guide/a.png", "CHANGELOG", "x/notes.rst"])
def test_doc_paths(path: str) -> None:
    assert is_doc_path(path)


@pytest.mark.parametrize("path", ["app/store.py", "requirements.txt", "docs_tool.py"])
def test_other_files_are_not_docs(path: str) -> None:
    assert not is_doc_path(path)


@pytest.mark.parametrize(
    "path",
    ["pyproject.toml", "uv.lock", "svc/package.json", "requirements-dev.txt", "Cargo.lock",
     "app/App.csproj", "go.sum"],
)  # fmt: skip
def test_manifest_paths(path: str) -> None:
    assert is_manifest_path(path)


def test_a_source_file_is_not_a_manifest() -> None:
    assert not is_manifest_path("app/package_info.py")
    assert not is_manifest_path("notes.txt")


def run(name: str, *changes: Change, **settings: object) -> list[str]:
    config = load_settings(overrides=settings) if settings else load_settings()
    return evaluate([name], list(changes), config)[0].violations


def test_tests_only_names_every_file_that_is_not_a_test() -> None:
    found = run("tests_only", change("tests/test_a.py", "A"), change("app/store.py"))

    assert len(found) == 1 and found[0].startswith("app/store.py (M): not a test file")


def test_docs_only_and_manifests_only() -> None:
    assert run("docs_only", change("README.md"), change("docs/a.md", "A")) == []
    assert len(run("docs_only", change("app/store.py"))) == 1
    assert run("manifests_only", change("pyproject.toml"), change("uv.lock")) == []
    assert len(run("manifests_only", change("app/store.py"), change("pyproject.toml"))) == 1


def test_tests_untouched_allows_new_tests_but_not_changing_or_deleting_old_ones() -> None:
    found = run(
        "tests_untouched",
        change("tests/test_new.py", "A"),
        change("tests/test_old.py", "M"),
        change("tests/test_gone.py", "D"),
        change("app/store.py"),
    )

    assert sorted(found) == [
        "tests/test_gone.py: an existing test was deleted",
        "tests/test_old.py: an existing test was changed",
    ]


def test_a_refactor_that_changes_too_many_lines_is_refused() -> None:
    big = change("app/a.py", added=900, removed=0)

    over = run("diff_size", big)
    under = run("diff_size", change("app/a.py", added=5, removed=5))

    assert (
        len(over) == 1
        and "900 lines changed" in over[0]
        and "maintain.max_refactor_lines" in over[0]
    )
    assert under == []
    assert run("diff_size", big, **{"maintain.max_refactor_lines": 5000}) == []


def test_manifests_untouched_flags_lockfiles() -> None:
    assert len(run("manifests_untouched", change("uv.lock"), change("app/a.py"))) == 1


def test_every_policy_is_exercised() -> None:
    assert set(POLICY_NAMES) == {
        "tests_only", "docs_only", "manifests_only", "tests_untouched", "manifests_untouched",
        "diff_size",
    }  # fmt: skip


def test_the_named_write_scopes_permit_their_lane_and_refuse_the_code() -> None:
    tests = WriteScope(allow=SCOPES["tests"])
    docs = WriteScope(allow=SCOPES["docs"])
    manifests = WriteScope(allow=SCOPES["manifests"])

    assert tests.permits("tests/test_a.py") and tests.permits("src/cart.test.js")
    assert not tests.permits("app/store.py")
    assert docs.permits("docs/guide.md") and docs.permits("README.md")
    assert not docs.permits("app/store.py") and not docs.permits("requirements.txt")
    assert manifests.permits("pyproject.toml") and manifests.permits("svc/package-lock.json")
    assert manifests.permits("requirements-dev.txt") and manifests.permits("Cargo.lock")
    assert not manifests.permits("app/store.py")
