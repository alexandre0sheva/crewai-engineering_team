"""Reading a stack trace: the error, its frames, and the project files they point at.

``fix --trace-file`` hands the debugger a trace; the controller parses it first (no model) so the
suspects it names are files that exist in the project, found even when the trace came from
another checkout, a CI machine, or an installed copy. Python, Node, and Java traces are
understood; anything else is passed on as text and parses to ``None``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engineering_team.tools.workspace import CONTROLLER_DIRECTORY

MAX_FRAMES = 40
MAX_SUSPECTS = 8
MAX_MESSAGE = 500
# Frames in code that is not the project's: installed packages, the runtime, test runners.
FOREIGN = ("site-packages", "dist-packages", "node_modules", "/lib/python", "<frozen", "node:")
SKIPPED_DIRECTORIES = {".git", "node_modules", CONTROLLER_DIRECTORY, "__pycache__", ".venv"}

PYTHON_START = re.compile(r"^Traceback \(most recent call last\):\s*$")
PYTHON_FRAME = re.compile(r'^\s+File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<fn>.+))?$')
PYTHON_ERROR = re.compile(r"^(?P<error>[A-Za-z_][\w.]*)(?::\s?(?P<message>.*))?$")
NODE_FRAME = re.compile(r"^\s+at (?:(?P<fn>.+?) \()?(?P<file>[^\s()]+?):(?P<line>\d+):\d+\)?\s*$")
NODE_ERROR = re.compile(
    r"^\s*(?:Uncaught |Unhandled )?(?P<error>[A-Za-z_$][\w$.]*(?:Error|Exception)):\s?"
    r"(?P<message>.*)$"
)
JAVA_FRAME = re.compile(
    r"^\s+at (?:[\w.$]+/)?(?P<fn>[\w.$<>]+)\((?P<file>[\w$]+\.\w+):(?P<line>\d+)\)"
)
JAVA_ERROR = re.compile(
    r'^(?:Exception in thread "[^"]*" )?(?P<error>[\w.$]+(?:Exception|Error|Throwable)[\w$]*)'
    r"(?::\s?(?P<message>.*))?$"
)


class Frame(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file: str
    line: int | None = None
    function: str = ""


class ParsedTrace(BaseModel):
    """A stack trace, frames innermost first (where it was raised comes first)."""

    model_config = ConfigDict(extra="ignore")

    language: Literal["python", "node", "java"]
    error: str
    message: str = ""
    frames: list[Frame] = Field(default_factory=list)

    def render(self, suspects: list[str] | None = None) -> str:
        head = f"{self.error}: {self.message}".rstrip(": ")
        lines = [f"{self.language} trace, innermost frame first", head]
        for frame in self.frames[:12]:
            where = f"{frame.file}:{frame.line}" if frame.line else frame.file
            lines.append(f"  at {frame.function or '?'} ({where})")
        if suspects:
            lines.append(f"Project files in the trace: {', '.join(suspects)}")
        return "\n".join(lines)


def parse_trace(text: str) -> ParsedTrace | None:
    """The first trace found in ``text`` (a log may hold anything around it), or ``None``."""

    lines = text.splitlines()
    return _python(lines) or _java(lines) or _node(lines)


def _clip(message: str | None) -> str:
    return (message or "").strip()[:MAX_MESSAGE]


def _python(lines: list[str]) -> ParsedTrace | None:
    starts = [i for i, line in enumerate(lines) if PYTHON_START.match(line)]
    if not starts:
        return None
    frames: list[Frame] = []
    index = starts[-1] + 1  # chained exceptions: the last block is the one that was not handled
    error, message = "", ""
    while index < len(lines):
        line = lines[index]
        if match := PYTHON_FRAME.match(line):
            frames.append(
                Frame(
                    file=match["file"],
                    line=int(match["line"]),
                    function=(match["fn"] or "").strip(),
                )
            )
        elif line.strip() and not line.startswith((" ", "\t")):
            found = PYTHON_ERROR.match(line.strip())
            if found:
                error, message = found["error"], _clip(found["message"])
            break
        index += 1
    if not error:
        return None
    return ParsedTrace(
        language="python", error=error, message=message, frames=frames[::-1][:MAX_FRAMES]
    )


def _java(lines: list[str]) -> ParsedTrace | None:
    frames: list[Frame] = []
    error, message = "", ""
    for line in lines:
        if frame := JAVA_FRAME.match(line):
            if not error:
                continue  # frames before any exception line are not a trace
            frames.append(Frame(file=frame["file"], line=int(frame["line"]), function=frame["fn"]))
        elif not error and (found := JAVA_ERROR.match(line.strip())):
            error, message = found["error"], _clip(found["message"])
    if not error or not frames:
        return None
    return ParsedTrace(language="java", error=error, message=message, frames=frames[:MAX_FRAMES])


def _node(lines: list[str]) -> ParsedTrace | None:
    frames: list[Frame] = []
    error, message = "", ""
    for index, line in enumerate(lines):
        if not (frame := NODE_FRAME.match(line)):
            continue
        if not error:
            before = next((lines[i] for i in range(index - 1, -1, -1) if lines[i].strip()), "")
            found = NODE_ERROR.match(before)
            if not found:
                continue
            error, message = found["error"], _clip(found["message"])
        if frame["file"].startswith("node:"):
            continue  # the runtime's own frames say nothing about the project
        frames.append(
            Frame(
                file=frame["file"],
                line=int(frame["line"]),
                function=(frame["fn"] or "").strip(),
            )
        )
    if not error or not frames:
        return None
    return ParsedTrace(language="node", error=error, message=message, frames=frames[:MAX_FRAMES])


# -- the project files a trace points at ----------------------------------------------------------


def suspect_files(trace: ParsedTrace | None, root: Path) -> list[str]:
    """Project files (relative to ``root``, innermost first) that the frames of ``trace`` name."""

    if trace is None:
        return []
    found: list[str] = []
    for frame in trace.frames:
        if any(marker in frame.file for marker in FOREIGN):
            continue
        name = _java_file(root, frame) if trace.language == "java" else _located(root, frame.file)
        if name and name not in found:
            found.append(name)
    return found[:MAX_SUSPECTS]


def _located(root: Path, name: str) -> str | None:
    """``name`` as a file under ``root``, however much of its leading path differs."""

    parts = PurePosixPath(name.replace("\\", "/")).parts
    parts = tuple(part for part in parts if part not in ("/", ".", ""))
    for start in range(len(parts)):
        relative = PurePosixPath(*parts[start:])
        if CONTROLLER_DIRECTORY in relative.parts or ".." in relative.parts:
            return None
        if (root / relative).is_file():
            return relative.as_posix()
    return None


def _java_file(root: Path, frame: Frame) -> str | None:
    """A Java frame names a class and a bare file: find ``pkg/dir/File.java`` in the project."""

    package = frame.function.rsplit(".", 1)[0].replace("$", ".").split(".")  # drop the method
    stem = frame.file.removesuffix(".java").removesuffix(".kt")
    wanted = "/".join([*package[:-1], frame.file]) if package else frame.file
    for current, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIRECTORIES]
        if frame.file in names:
            path = (Path(current) / frame.file).relative_to(root).as_posix()
            if path.endswith(wanted) or stem == package[-1]:
                return path
    return None
