"""What a maintenance recipe may change, measured from Git by the controller.

A recipe names *policies* (``add-tests`` must change only tests, ``upgrade-deps`` only manifests
and lockfiles); the verify stage evaluates them against the files changed since the team started
and records one required check per policy (``policy:<name>``), so a violation is handed to the
repair agent like any failing check ("revert these files") and keeps the run from being
``verified``. The same classification feeds the named write scopes (``write_scope: tests``) that
stop an agent's file tools from writing outside its lane: the scope refuses the write, the policy
catches whatever got around it (a command that edits a file).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from engineering_team.git.port import Change
from engineering_team.settings import Settings
from engineering_team.tools.scope import compile_glob
from engineering_team.verification.criteria import is_test_file

TEST_SUPPORT = ("conftest.py", "setup.py.test")
TEST_DIRECTORIES = ("fixtures", "testdata", "__snapshots__", "__mocks__", "mocks", "snapshots")
DOC_SUFFIXES = (".md", ".rst", ".adoc", ".markdown")
DOC_DIRECTORIES = ("docs", "doc")
DOC_NAMES = ("readme", "changelog", "contributing", "authors", "history", "news", "codeowners")
MANIFEST_FILES = (
    "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock",
    "pdm.lock", "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
    "pnpm-lock.yaml", "bun.lockb", "bun.lock", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock",
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle.lockfile", "Gemfile", "Gemfile.lock",
    "composer.json", "composer.lock", "packages.lock.json", "Directory.Packages.props",
)  # fmt: skip
MANIFEST_NAMES = frozenset(name.lower() for name in MANIFEST_FILES)
MANIFEST_PATTERNS = ("requirements*.txt", "constraints*.txt", "*.csproj", "*.fsproj")

# The named write scopes: gitignore-style globs (``tools/scope.py``), each matching at any depth.
SCOPES: dict[str, tuple[str, ...]] = {
    "tests": (
        "tests", "test", "spec", "specs", "__tests__", "e2e", "test_*.py", "*_test.py",
        "*_test.go", "*.test.*", "*.spec.*", "conftest.py", "*Test.java", "*Tests.java",
        "*Test.kt", "*_spec.rb", "*Test.php", *TEST_DIRECTORIES,
    ),
    "docs": ("docs", "doc", *(f"*{s}" for s in DOC_SUFFIXES), "README*", "CHANGELOG*"),
    "manifests": (*MANIFEST_FILES, *MANIFEST_PATTERNS),
}  # fmt: skip


def is_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        is_test_file(path)
        or parts[-1] in TEST_SUPPORT
        or any(part in TEST_DIRECTORIES for part in parts[:-1])
        or path.endswith(".snap")
    )


def is_doc_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        pure.suffix.lower() in DOC_SUFFIXES
        or any(part in DOC_DIRECTORIES for part in pure.parts[:-1])
        or pure.stem.lower() in DOC_NAMES
    )


def is_manifest_path(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.lower() in MANIFEST_NAMES or any(
        compile_glob(pattern).fullmatch(name) for pattern in MANIFEST_PATTERNS
    )


@dataclass(frozen=True)
class PolicyResult:
    name: str
    violations: list[str]

    @property
    def ok(self) -> bool:
        return not self.violations


Policy = Callable[[Sequence[Change], Settings], list[str]]


def _outside(check: Callable[[str], bool], what: str) -> Policy:
    def policy(changes: Sequence[Change], _settings: Settings) -> list[str]:
        bad = [c for c in changes if not check(c.path)]
        return [f"{c.path} ({c.status}): not {what}" for c in bad]

    return policy


def _tests_untouched(changes: Sequence[Change], _settings: Settings) -> list[str]:
    return [
        f"{c.path}: an existing test was {'deleted' if c.status == 'D' else 'changed'}"
        for c in changes
        if is_test_path(c.path) and c.status != "A"
    ]


def _manifests_untouched(changes: Sequence[Change], _settings: Settings) -> list[str]:
    return [f"{c.path}: a dependency manifest or lockfile changed" for c in changes
            if is_manifest_path(c.path)]  # fmt: skip


def _diff_size(changes: Sequence[Change], settings: Settings) -> list[str]:
    limit = settings.maintain.max_refactor_lines
    total = sum(c.lines for c in changes)
    if total <= limit:
        return []
    return [
        f"{total} lines changed; a refactor may change at most {limit} "
        "(maintain.max_refactor_lines): split it into smaller steps"
    ]


POLICIES: dict[str, Policy] = {
    "tests_only": _outside(is_test_path, "a test file (this task may not change the code)"),
    "docs_only": _outside(is_doc_path, "documentation (this task may not change the code)"),
    "manifests_only": _outside(
        is_manifest_path, "a dependency manifest or lockfile (this task only changes those)"
    ),
    "tests_untouched": _tests_untouched,
    "manifests_untouched": _manifests_untouched,
    "diff_size": _diff_size,
}
POLICY_NAMES = tuple(POLICIES)


def evaluate(
    names: Sequence[str], changes: Sequence[Change], settings: Settings
) -> list[PolicyResult]:
    """One result per policy name, in order."""

    return [PolicyResult(name, POLICIES[name](changes, settings)) for name in names]
