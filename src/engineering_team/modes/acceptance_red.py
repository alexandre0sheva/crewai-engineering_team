"""The red-on-baseline rule of the ``acceptance`` stage: failing for the right reason.

An acceptance test is written before the change exists, so on the baseline it must fail, and the
failure must be the missing behaviour: an assertion about the wrong result, or an import of the
symbol the change will add. A test that passes proves nothing; a test that cannot run (a syntax
error, a missing fixture, a dependency the project does not have, a command that does not start)
fails for a reason the implementation will not change, so its later pass would prove nothing
either. These rules read only the controller's own run of the test.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

from engineering_team.contracts import CheckResult
from engineering_team.modes.fix_repro import NOT_STARTED, PYTEST_NOT_A_FAILURE
from engineering_team.modes.policies import is_test_path

# The test does not compile: no implementation fixes that.
BROKEN = re.compile(r"\b(SyntaxError|IndentationError|TabError)\b|\bsyntax error\b")
NO_FIXTURE = re.compile(r"fixture '[^']+' not found")
MISSING_MODULE = re.compile(r"No module named '([\w.]+)'|Cannot find module '([^']+)'")
# Errors on a symbol the change will add (an import that fails, an attribute or name not there).
MISSING_SYMBOL = re.compile(
    r"\b(ImportError|ModuleNotFoundError|AttributeError|NameError|ReferenceError)\b|"
    r"cannot import name|is not defined|has no attribute|undefined: |Cannot find module|"
    r"is not a function|cannot find symbol|has no exported member"
)


def project_module(root: Path, planned: Sequence[str]) -> Callable[[str], bool]:
    """Whether a module a test fails to import is the project's (or one the plan adds): its first
    part is a directory or a source file of the project, or the first part of a planned path."""

    starts = {PurePosixPath(path).parts[0].split(".")[0] for path in planned if path.strip("/")}

    def known(name: str) -> bool:
        first = re.split(r"[./\\]", name.lstrip("./@"), maxsplit=1)[0]
        if not first:
            return name.startswith(".")  # a relative import inside the project
        return (
            first in starts
            or (root / first).is_dir()
            or (root / "src" / first).is_dir()
            or any((root / f"{first}{suffix}").is_file() for suffix in (".py", ".js", ".ts"))
        )

    return known


def red_problem(
    argv: Sequence[str], result: CheckResult, *, known_module: Callable[[str], bool]
) -> str | None:
    """Why ``result`` is not a failure for the right reason on the baseline; ``None`` when it is."""

    if result.status == "passed":
        return "it passes on the baseline, before any change, so it proves nothing about the change"
    if result.status != "failed":
        return result.hint or "the controller could not run it"
    if "timed out" in result.summary:
        return f"it {result.summary}, which shows nothing"
    code, text = result.exit_code, result.log_tail or ""
    if code in NOT_STARTED:
        return f"its command could not be started (exit {code}): check its name and path"
    if BROKEN.search(text):
        return "the test itself does not compile (a syntax error), which no implementation fixes"
    if NO_FIXTURE.search(text):
        return "it uses a fixture that does not exist"
    for match in MISSING_MODULE.finditer(text):
        name = match.group(1) or match.group(2)
        if not known_module(name):
            return (
                f"it cannot import {name!r}, which is neither the project's nor in the plan: a "
                "missing dependency or a typo, not the missing feature"
            )
    is_pytest = any(Path(a).name in ("pytest", "py.test") for a in argv)
    if is_pytest and code in (4, 5):
        return f"{PYTEST_NOT_A_FAILURE[code]} (exit {code}), which is not a failing test"
    if is_pytest and code == 2 and not MISSING_SYMBOL.search(text):
        return "pytest could not collect it, and not because a symbol of the change is missing"
    return None


def command_problem(argv: Sequence[str], files: Sequence[str]) -> str | None:
    """Why a test's command does not run that test alone (it must name one of its files, their
    module or their directory, so a suite that already fails on the baseline is not mistaken for
    it); ``None`` when it does."""

    if not argv:
        return "it has no command"
    names: set[str] = set()
    for name in files:
        path = PurePosixPath(name)
        names |= {name, path.stem, ".".join(path.with_suffix("").parts)}
        if path.suffix == ".go" and path.parent != PurePosixPath("."):
            names.add(path.parent.as_posix())  # Go runs a package's tests, by directory
    if any(name in token for token in argv[1:] for name in names if name):
        return None
    return (
        f"its command `{' '.join(argv)}` does not name its test files ({', '.join(files)}): run "
        "only this test, not the whole suite"
    )


def file_problem(files: Sequence[str], existing: set[str], exists: Callable[[str], bool]) -> str:
    """Why a test's files are not acceptable (empty when they are): each must be a new test file."""

    if not files:
        return "it names no test file"
    if bad := [name for name in files if not is_test_path(name)]:
        return f"these are not test files: {', '.join(bad)}"
    if old := [name for name in files if name in existing]:
        return f"it changes existing files ({', '.join(old)}): put acceptance tests in new files"
    if missing := [name for name in files if not exists(name)]:
        return f"these files do not exist: {', '.join(missing)}"
    return ""
