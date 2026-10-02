"""Atomic multi-file edits: unified diffs and exact-replacement edit lists.

Both forms are validated completely, in memory, before anything is written, so a bad hunk
leaves the tree untouched. Writes then go through temp files and ``os.replace``, with a
rollback if one fails partway.
"""

from __future__ import annotations

import difflib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from engineering_team.tools.support import ToolError
from engineering_team.tools.workspace import MAX_WRITE_BYTES, ProjectWorkspace

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
DEV_NULL = "/dev/null"
MAX_EDITS = 200


@dataclass
class Hunk:
    old_start: int
    lines: list[tuple[str, str]] = field(default_factory=list)  # (" ", "-", "+"), text

    @property
    def old(self) -> list[str]:
        return [text for tag, text in self.lines if tag in " -"]

    @property
    def new(self) -> list[str]:
        return [text for tag, text in self.lines if tag in " +"]


@dataclass
class FilePatch:
    path: str
    hunks: list[Hunk] = field(default_factory=list)
    creates: bool = False
    deletes: bool = False


@dataclass
class Change:
    """The planned outcome for one file."""

    path: str
    new_text: str | None  # None deletes the file
    existed: bool
    added: int = 0
    removed: int = 0
    ranges: list[tuple[int, int]] = field(default_factory=list)  # new 1-based inclusive lines


def _strip_prefix(raw: str) -> str:
    name = raw.split("\t")[0].strip()
    if name == DEV_NULL:
        return name
    return name[2:] if name.startswith(("a/", "b/")) else name


def parse_unified_diff(patch: str) -> list[FilePatch]:
    """Parse a unified diff (``--- a/x`` / ``+++ b/x`` / ``@@`` hunks) into per-file patches."""

    files: list[FilePatch] = []
    lines = patch.replace("\r\n", "\n").split("\n")
    index = 0
    current: FilePatch | None = None
    hunk: Hunk | None = None
    old_name = ""
    while index < len(lines):
        line = lines[index]
        if (
            line.startswith("--- ")
            and index + 1 < len(lines)
            and lines[index + 1].startswith("+++ ")
        ):
            old_name, new_name = _strip_prefix(line[4:]), _strip_prefix(lines[index + 1][4:])
            target = old_name if new_name == DEV_NULL else new_name
            current = FilePatch(target, creates=old_name == DEV_NULL, deletes=new_name == DEV_NULL)
            files.append(current)
            hunk = None
            index += 2
            continue
        if line.startswith("@@"):
            match = HUNK_HEADER.match(line)
            if current is None or match is None:
                raise ToolError(
                    f"Malformed hunk header: {line!r}. Use '@@ -start,count +start,count @@' "
                    "after '--- a/path' and '+++ b/path' lines."
                )
            hunk = Hunk(int(match.group(1)))
            current.hunks.append(hunk)
        elif hunk is not None and line[:1] in (" ", "-", "+"):
            hunk.lines.append((line[0], line[1:]))
        elif hunk is not None and line == "":
            hunk.lines.append((" ", ""))  # a blank context line whose space was stripped
        elif hunk is not None and line.startswith("\\"):
            pass  # "\ No newline at end of file"
        elif line.startswith(("diff ", "index ", "new file", "deleted file", "similarity")):
            hunk = None
        index += 1
    if not files:
        raise ToolError(
            "No file sections found in the patch. A unified diff starts with '--- a/path' and "
            "'+++ b/path', then '@@ -a,b +c,d @@' hunks. Use the edits form for simple "
            "replacements."
        )
    for file in files:
        _trim_trailing_blank_context(file)
        if not file.hunks:
            raise ToolError(f"The patch for {file.path} has no hunks.")
    return files


def _trim_trailing_blank_context(file: FilePatch) -> None:
    """Drop the empty context line a trailing newline in the patch text produces."""

    if file.hunks:
        last = file.hunks[-1]
        while last.lines and last.lines[-1] == (" ", ""):
            last.lines.pop()


def _locate(lines: list[str], block: list[str], expected: int) -> int | None:
    """Index where ``block`` matches ``lines``: exact first, then ignoring trailing space."""

    if not block:
        return min(max(expected, 0), len(lines))
    for normalise in (lambda s: s, lambda s: s.rstrip()):
        wanted = [normalise(item) for item in block]
        spots = [
            start
            for start in range(len(lines) - len(block) + 1)
            if [normalise(item) for item in lines[start : start + len(block)]] == wanted
        ]
        if spots:
            return min(spots, key=lambda start: abs(start - expected))
    return None


def _apply_hunks(path: str, text: str, hunks: list[Hunk]) -> Change:
    had_final_newline = text.endswith("\n") or text == ""
    lines = text.split("\n")[:-1] if text.endswith("\n") else text.split("\n") if text else []
    change = Change(path, None, existed=True)
    offset = 0
    for number, hunk in enumerate(hunks, start=1):
        expected = hunk.old_start - 1 + offset
        start = _locate(lines, hunk.old, expected)
        if start is None:
            first = next((item for item in hunk.old if item.strip()), "")
            raise ToolError(
                f"Hunk {number} for {path} does not match the file (looked for the block "
                f"starting {first!r}). Read the current lines with Read File Range and "
                "regenerate the patch; nothing was changed."
            )
        lines[start : start + len(hunk.old)] = hunk.new
        offset += len(hunk.new) - len(hunk.old)
        change.added += sum(1 for tag, _ in hunk.lines if tag == "+")
        change.removed += sum(1 for tag, _ in hunk.lines if tag == "-")
        if hunk.new:
            change.ranges.append((start + 1, start + len(hunk.new)))
    result = "\n".join(lines)
    change.new_text = result + ("\n" if lines and had_final_newline else "")
    return change


def plan_unified_diff(workspace: ProjectWorkspace, patch: str) -> list[Change]:
    changes: list[Change] = []
    seen: set[str] = set()
    for file in parse_unified_diff(patch):
        if file.path in seen:
            raise ToolError(f"{file.path} appears twice in the patch; merge its hunks into one.")
        seen.add(file.path)
        target = workspace.resolve(file.path)
        exists = target.is_file()
        if file.creates:
            if exists:
                raise ToolError(f"{file.path} already exists, but the patch creates it.")
            new_lines = [text for hunk in file.hunks for tag, text in hunk.lines if tag == "+"]
            changes.append(
                Change(
                    file.path,
                    "\n".join(new_lines) + "\n" if new_lines else "",
                    False,
                    added=len(new_lines),
                    ranges=[(1, len(new_lines))] if new_lines else [],
                )
            )
            continue
        if not exists:
            raise ToolError(
                f"{file.path} does not exist, so the patch cannot modify it. Check the path "
                "with Find Files, or create the file with '--- /dev/null'."
            )
        text = workspace.read_file(file.path)
        if file.deletes:
            changes.append(Change(file.path, None, True, removed=len(text.splitlines())))
            continue
        changes.append(_apply_hunks(file.path, text, file.hunks))
    return changes


def plan_edits(workspace: ProjectWorkspace, edits: Sequence[Mapping[str, object]]) -> list[Change]:
    """Plan ``{path, old, new, expected_replacements}`` edits; edits to one file chain in order."""

    if not edits:
        raise ToolError("edits is empty; pass at least one {path, old, new} edit.")
    if len(edits) > MAX_EDITS:
        raise ToolError(f"Too many edits ({len(edits)}); the limit is {MAX_EDITS} per call.")
    working: dict[str, str] = {}
    originals: dict[str, str] = {}
    for number, edit in enumerate(edits, start=1):
        path, old, new = edit.get("path"), edit.get("old"), edit.get("new")
        if not isinstance(path, str) or not isinstance(old, str) or not isinstance(new, str):
            raise ToolError(
                f"Edit {number} needs string 'path', 'old', and 'new' fields "
                "(optional integer 'expected_replacements')."
            )
        if not old:
            raise ToolError(f"Edit {number}: 'old' cannot be empty.")
        expected = edit.get("expected_replacements", 1)
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            raise ToolError(f"Edit {number}: expected_replacements must be an integer >= 1.")
        workspace.resolve(path)
        if path not in working:
            if not workspace.resolve(path).is_file():
                raise ToolError(
                    f"Edit {number}: {path} does not exist. Use Find Files to locate it."
                )
            originals[path] = working[path] = workspace.read_file(path)
        actual = working[path].count(old)
        if actual != expected:
            raise ToolError(
                f"Edit {number}: expected {expected} occurrence(s) of the old text in {path}, "
                f"found {actual}. Read the file again and make 'old' unique (add surrounding "
                "lines) or set expected_replacements. Nothing was changed."
            )
        working[path] = working[path].replace(old, new)
    changes = []
    for path, new_text in working.items():
        change = Change(path, new_text, True)
        matcher = difflib.SequenceMatcher(
            None, originals[path].splitlines(), new_text.splitlines(), autojunk=False
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            change.removed += i2 - i1
            change.added += j2 - j1
            if j2 > j1:
                change.ranges.append((j1 + 1, j2))
        changes.append(change)
    return changes


def commit(workspace: ProjectWorkspace, changes: list[Change]) -> None:
    """Write every change, restoring all files if any write fails."""

    for change in changes:
        if change.new_text is not None and len(change.new_text.encode()) > MAX_WRITE_BYTES:
            raise ToolError(f"{change.path} would exceed the {MAX_WRITE_BYTES}-byte write limit.")
    backups: dict[Path, str | None] = {}
    try:
        for change in changes:
            path = workspace.resolve(change.path)
            if path.is_dir():
                raise ToolError(f"{change.path} is a directory, not a file.")
            backups[path] = path.read_text(encoding="utf-8") if path.is_file() else None
            if change.new_text is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_text(change.new_text, encoding="utf-8")
            os.replace(temporary, path)
    except BaseException:
        for path, original in backups.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(original, encoding="utf-8")
        raise


def summarize(changes: list[Change]) -> str:
    rows = []
    for change in changes:
        if change.new_text is None:
            rows.append(f"deleted {change.path} (-{change.removed})")
            continue
        ranges = ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in change.ranges)
        verb = "modified" if change.existed else "created"
        where = f", new lines {ranges}" if ranges else ""
        rows.append(f"{verb} {change.path} (+{change.added} -{change.removed}{where})")
    return f"Applied changes to {len(changes)} file(s):\n" + "\n".join(rows)
