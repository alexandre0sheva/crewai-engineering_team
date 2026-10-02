"""Discovering a project's own scripts so agents run them instead of guessing commands."""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path

from engineering_team.tools.support import ToolError

MAKE_TARGET = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?![=:])")
JUST_RECIPE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)(?:\s+[^:]*)?:(?!=)")
JUST_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:?=")
JS_RUNNERS = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
)
MAX_BODY_CHARS = 100


@dataclass(frozen=True)
class Script:
    source: str  # npm, pnpm, yarn, bun, make, just, uv
    name: str
    body: str  # what it does, for the listing
    argv: tuple[str, ...]  # the command that runs it (without extra arguments)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.name}"

    def command(self, extra: list[str]) -> str:
        argv = list(self.argv)
        if extra:
            if self.source == "npm":
                argv.append("--")  # npm needs it to forward flags to the script
            argv.extend(extra)
        return shlex.join(argv)


def _package_json(directory: Path) -> list[Script]:
    file = directory / "package.json"
    if not file.is_file():
        return []
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"package.json is not valid JSON ({exc}); fix it first.") from exc
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return []
    runner = next((name for lock, name in JS_RUNNERS if (directory / lock).exists()), "npm")
    return [
        Script(runner, str(name), str(body)[:MAX_BODY_CHARS], (runner, "run", str(name)))
        for name, body in scripts.items()
    ]


def _makefile(directory: Path) -> list[Script]:
    for filename in ("Makefile", "makefile", "GNUmakefile"):
        file = directory / filename
        if file.is_file():
            names: dict[str, None] = {}
            for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
                if (match := MAKE_TARGET.match(line)) and "%" not in line.split(":")[0]:
                    names.setdefault(match.group(1))
            return [Script("make", name, "make target", ("make", name)) for name in names]
    return []


def _justfile(directory: Path) -> list[Script]:
    for filename in ("justfile", "Justfile", ".justfile"):
        file = directory / filename
        if file.is_file():
            names: dict[str, None] = {}
            for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line[:1].isspace() or line.startswith(("#", "set ", "alias ")):
                    continue
                if JUST_ASSIGNMENT.match(line):
                    continue
                if match := JUST_RECIPE.match(line):
                    names.setdefault(match.group(1))
            return [Script("just", name, "just recipe", ("just", name)) for name in names]
    return []


def _pyproject(directory: Path) -> list[Script]:
    file = directory / "pyproject.toml"
    if not file.is_file():
        return []
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ToolError(f"pyproject.toml is not valid TOML ({exc}); fix it first.") from exc
    scripts = data.get("project", {}).get("scripts", {})
    return [
        Script(
            "uv",
            str(name),
            f"console script -> {target}"[:MAX_BODY_CHARS],
            ("uv", "run", str(name)),
        )
        for name, target in scripts.items()
    ]


def discover_scripts(directory: Path) -> list[Script]:
    """Scripts defined directly in ``directory`` (no recursion), in a stable order."""

    return [
        *_package_json(directory),
        *_makefile(directory),
        *_justfile(directory),
        *_pyproject(directory),
    ]


def list_scripts(directory: Path, display: str) -> str:
    scripts = discover_scripts(directory)
    if not scripts:
        return (
            f"No scripts found in {display} (looked for package.json scripts, Makefile targets, "
            "justfile recipes, and pyproject [project.scripts]). Use Project Tree to find the "
            "project's own tooling, or run a command with Run Project Command."
        )
    rows = [f"{script.key}  -  {script.body}" for script in scripts]
    return f"{len(scripts)} script(s) in {display}; run one with Run Script\n" + "\n".join(rows)


def resolve_script(directory: Path, name: str, display: str) -> Script:
    scripts = discover_scripts(directory)
    exact = [script for script in scripts if script.key == name]
    matches = exact or [script for script in scripts if script.name == name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        options = ", ".join(script.key for script in matches)
        raise ToolError(f"'{name}' is ambiguous in {display}; use one of: {options}.")
    available = ", ".join(script.key for script in scripts) or "none"
    raise ToolError(
        f"No script named '{name}' in {display}. Available: {available}. "
        "Call List Scripts to see them."
    )
