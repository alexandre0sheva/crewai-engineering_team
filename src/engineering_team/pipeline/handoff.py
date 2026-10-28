"""Hand-off packs: what a session is told up front so it does not rediscover it.

Every agent session used to start cold: list the tree, read the same files, ask what the checks
said. The controller already knows those facts, so each session's prompt carries a compact pack
built from the workspace and the run's state: the files (most recently changed first, with a
content hash so an agent can tell whether a file changed since it last saw it), the latest
check results, and one line per earlier stage. Everything in it is the controller's record;
the agents' own summaries are quoted as theirs.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from engineering_team.tools.ignore import iter_files

if TYPE_CHECKING:
    from engineering_team.pipeline.recipes import StageSpec
    from engineering_team.pipeline.state import PipelineState
    from engineering_team.runtime.context import RunContext

MAX_FILES = 60
SCAN_LIMIT = 2000
MAX_HASH_BYTES = 262_144  # larger files are listed without a hash
MAX_SUMMARY_LINE = 160
MAX_CHECKS = 12


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


def file_lines(ctx: RunContext) -> list[str]:
    """``path  bytes  hash`` for the most recently changed files, listed by path."""

    found = []
    for count, path in enumerate(iter_files(ctx.workspace)):
        if count >= SCAN_LIMIT:
            break
        try:
            info = path.stat()
        except OSError:
            continue
        found.append((info.st_mtime, info.st_size, path))
    recent = sorted(found, key=lambda item: item[0], reverse=True)[:MAX_FILES]
    lines = []
    for _, size, path in sorted(recent, key=lambda item: item[2]):
        name = ctx.workspace.relative_name(path)
        try:
            digest = _digest(path.read_bytes()) if size <= MAX_HASH_BYTES else "-"
        except OSError:
            continue
        lines.append(f"- {name}  {size} B  {digest}")
    if len(found) > MAX_FILES:
        more = f"{len(found) - MAX_FILES}{'+' if len(found) >= SCAN_LIMIT else ''}"
        lines.append(f"- ... and {more} more (Find Files or Project Tree list them)")
    return lines


def handoff_pack(ctx: RunContext, state: PipelineState, stage: StageSpec) -> str:
    """The pack for a session of ``stage`` (empty when there is nothing to hand over)."""

    files = file_lines(ctx)
    parts = []
    if files:
        parts += [
            "Files in the workspace now (path, size, sha256 prefix; the most recently changed, "
            "listed by path). Read a file before you change it:",
            *files,
        ]
    if state.checks:
        parts += ["", "The controller's latest check results:"]
        parts += [
            f"- {check.id}: {check.status}" + (f" ({check.summary})" if check.summary else "")
            for check in list(state.checks)[:MAX_CHECKS]
        ]
    earlier = [  # a copy: packages call this from their lanes while the state moves on
        (name, text) for name, text in list(state.summaries.items()) if name != stage.name and text
    ]
    if earlier:
        parts += ["", "Earlier stages, in their agents' own words (not evidence):"]
        parts += [
            f"- {name}: {' '.join(text.split())[:MAX_SUMMARY_LINE]}" for name, text in earlier
        ]
    if not parts:
        return ""
    return "Hand-off from the controller:\n" + "\n".join(parts)
