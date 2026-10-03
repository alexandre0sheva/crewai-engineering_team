"""The shapes of what the deterministic repository analysis finds (``RepoProfile``).

Everything here is derived from files and Git by code, never by a model, so a profile costs
nothing and always says the same thing about the same tree. The analyzer
(``modes/repo_analyzer.py``) builds it; the baseline, the codebase map, and (from T26) the
repository modes read it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from engineering_team.contracts import Contract

CommandKind = Literal["setup", "test", "lint", "typecheck", "format", "build", "run"]
EntrypointKind = Literal["cli", "server", "script", "main", "web"]
ConventionKind = Literal[
    "docs", "assistant", "editor", "lint", "format", "typecheck", "hooks", "ci"
]


class LanguageStat(Contract):
    language: str
    files: int = 0
    lines: int = 0
    bytes: int = 0


class StackInfo(Contract):
    """One project directory and the tools it uses (``devtools.detect.Stack``, as data)."""

    directory: str
    language: str
    manager: str
    test: str | None = None
    lint: str | None = None
    typecheck: str | None = None
    format: str | None = None
    build: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DetectedCommand(Contract):
    """A command the project itself implies. ``source`` says where it came from, so a person can
    check it (``package.json script 'test'``, ``Makefile target 'check'``, ``detected: pytest``).
    ``argv`` is what the controller would run; it is never run by the analyzer."""

    kind: CommandKind
    directory: str = "."
    command: str
    argv: list[str] = Field(default_factory=list)
    source: str = ""


class Entrypoint(Contract):
    path: str
    kind: EntrypointKind = "main"
    evidence: str = ""


class ConventionFile(Contract):
    path: str
    kind: ConventionKind
    detail: str = ""  # what in the file, e.g. "[tool.ruff]" in pyproject.toml


class GitState(Contract):
    """What Git says about the directory. ``is_repo`` is true only when the directory itself is
    the top level of a repository; ``enclosing`` names the repository a plain subdirectory sits
    in (the analyzer then treats it as a non-repository)."""

    is_repo: bool = False
    branch: str = ""
    head: str = ""
    dirty: bool = False
    changed_files: int = 0
    untracked_files: int = 0
    linked_worktree: bool = False
    enclosing: str | None = None
    available: bool = True  # False: the git program is not installed


class RepoProfile(Contract):
    name: str
    root: str
    files: int = 0
    lines: int = 0
    bytes: int = 0
    truncated: bool = False  # the walk stopped at the file cap; counts are lower bounds
    languages: list[LanguageStat] = Field(default_factory=list)
    stacks: list[StackInfo] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    commands: list[DetectedCommand] = Field(default_factory=list)
    entrypoints: list[Entrypoint] = Field(default_factory=list)
    test_dirs: list[str] = Field(default_factory=list)
    test_files: int = 0
    ci: list[str] = Field(default_factory=list)
    conventions: list[ConventionFile] = Field(default_factory=list)
    git: GitState = Field(default_factory=GitState)
    notes: list[str] = Field(default_factory=list)

    @property
    def primary_language(self) -> str:
        return self.languages[0].language if self.languages else "unknown"

    def commands_of(self, kind: CommandKind) -> list[DetectedCommand]:
        return [c for c in self.commands if c.kind == kind]
