"""The set of source files the code intelligence tools look at, read once per call."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from engineering_team.codeintel.definitions import Definition, definitions_in
from engineering_team.codeintel.languages import language_of
from engineering_team.tools.ignore import IgnoreRules, iter_files, read_text_or_none
from engineering_team.tools.workspace import ProjectWorkspace

MAX_FILES = 5000
MAX_FILE_BYTES = 200_000
_GO_MODULE = re.compile(r"^module\s+(\S+)", re.MULTILINE)


@dataclass(frozen=True)
class SourceFile:
    path: str  # relative to the workspace root, POSIX separators
    language: str
    text: str


@dataclass
class SourceIndex:
    """Every readable source file of a workspace (``.gitignore`` and heavy directories honoured).

    The whole project is always indexed, even when a tool is scoped to a subdirectory: reverse
    dependencies and references live outside the directory being asked about.
    """

    files: dict[str, SourceFile]
    go_modules: dict[str, str] = field(default_factory=dict)  # directory of go.mod -> module path
    truncated: bool = False
    _definitions: dict[str, list[Definition]] = field(default_factory=dict, repr=False)

    @classmethod
    def build(cls, workspace: ProjectWorkspace) -> SourceIndex:
        files: dict[str, SourceFile] = {}
        go_modules: dict[str, str] = {}
        truncated = False
        for path in iter_files(workspace, rules=IgnoreRules.for_workspace(workspace)):
            relative = workspace.relative_name(path)
            if path.name == "go.mod":
                manifest = read_text_or_none(path, limit=MAX_FILE_BYTES) or ""
                if match := _GO_MODULE.search(manifest):
                    directory = PurePosixPath(relative).parent.as_posix()
                    go_modules["" if directory == "." else directory] = match.group(1)
                continue
            language = language_of(relative)
            if language is None:
                continue
            if len(files) >= MAX_FILES:
                truncated = True
                break
            text = read_text_or_none(path, limit=MAX_FILE_BYTES)
            if text is not None:
                files[relative] = SourceFile(relative, language, text)
        return cls(files, go_modules, truncated)

    def definitions(self, path: str) -> list[Definition]:
        if path not in self._definitions:
            source = self.files.get(path)
            self._definitions[path] = definitions_in(path, source.text) if source else []
        return self._definitions[path]

    def all_definitions(self) -> list[Definition]:
        return [definition for path in self.files for definition in self.definitions(path)]
