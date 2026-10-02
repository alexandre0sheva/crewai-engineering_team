"""What this machine can run: OS, resources, which language tools exist, which are missing.

Version probes run through the execution backend (so a container backend reports its own
tools). The probes are fixed commands, not agent input, so they do not go through the allowlist.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from engineering_team.execution.backend import CommandSpec
from engineering_team.tools.commands import DEFAULT_COMMAND_ALLOWLIST, command_environment

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext

PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("python", ("python", "--version")),
    ("python3", ("python3", "--version")),
    ("uv", ("uv", "--version")),
    ("node", ("node", "--version")),
    ("npm", ("npm", "--version")),
    ("pnpm", ("pnpm", "--version")),
    ("go", ("go", "version")),
    ("java", ("java", "-version")),
    ("rustc", ("rustc", "--version")),
    ("cargo", ("cargo", "--version")),
    ("dotnet", ("dotnet", "--version")),
    ("docker", ("docker", "--version")),
    ("git", ("git", "--version")),
)
VERSION = re.compile(r"\d+(?:\.\d+)+\S*")
PROBE_SECONDS = 5.0


def _memory_gb() -> str:
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return "unknown RAM"
    return f"{total / 1024**3:.1f} GB RAM"


def _probe(ctx: RunContext, name: str, argv: tuple[str, ...]) -> str:
    """One line: ``name version``, ``name: found but cannot run (...)``, or ``None`` if absent."""

    if shutil.which(argv[0]) is None:
        return ""
    spec = CommandSpec(
        argv=argv,
        cwd=ctx.workspace.root,
        env=command_environment(ctx.workspace),
        timeout=PROBE_SECONDS,
        label=f"probe-{name}",
    )
    try:
        record = ctx.backend.run(spec)
    except OSError as exc:  # e.g. a binary built for another CPU
        return f"{name}: found at {shutil.which(argv[0])} but cannot run ({exc.strerror or exc})"
    if record.timed_out or record.exit_code != 0:
        return f"{name}: found but `{' '.join(argv)}` failed (exit {record.exit_code})"
    match = VERSION.search(record.output_tail)
    return f"{name} {match.group(0) if match else '(version unknown)'}"


def environment_info(ctx: RunContext) -> str:
    """OS, CPUs, memory, tool versions on PATH, and allowlisted executables that are missing."""

    with ThreadPoolExecutor(max_workers=8) as pool:
        probed = list(pool.map(lambda item: (item[0], _probe(ctx, *item)), PROBES))
    found = [line for _, line in probed if line]
    absent = [name for name, line in probed if not line]
    allowed = DEFAULT_COMMAND_ALLOWLIST | ctx.workspace.extra_commands
    missing = sorted(name for name in allowed if shutil.which(name) is None)
    lines = [
        f"Environment: {platform.system()} {platform.release()} {platform.machine()}, "
        f"{os.cpu_count() or '?'} CPUs, {_memory_gb()}",
        "Tools on PATH:",
        *(f"- {line}" for line in found),
        "Not installed: " + (", ".join(absent) or "none of the probed tools"),
        "Allowlisted executables that are missing (do not use them): "
        + (", ".join(missing) or "none"),
        "Use these versions and omissions when choosing commands; Install Dependencies fetches "
        "a project's packages, not these tools.",
    ]
    return "\n".join(lines)
