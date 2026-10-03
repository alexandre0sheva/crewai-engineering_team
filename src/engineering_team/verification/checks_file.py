"""User-supplied checks (``--checks FILE``): parsed, validated, and pinned for the run.

The file belongs to the person running the team, so it must live **outside** the project the
agents write to. At the start of a run it is copied into the controller-owned run directory
and its hash is recorded in the pipeline state; every verification re-reads the copy and
refuses to run if its hash no longer matches (an altered check file proves nothing).
"""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

import yaml

from engineering_team.atomic_io import atomic_write_text
from engineering_team.contracts import CheckKind, CheckSpec
from engineering_team.runtime.context import RunContext
from engineering_team.tools.commands import SHELL_CONTROL_TOKENS

PINNED_FILENAME = "checks.yaml"
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
FIELDS = frozenset(
    {"id", "name", "command", "type", "script", "kind", "required", "timeout", "criteria", "cwd"}
)
KINDS = get_args(CheckKind)
MAX_TIMEOUT = 3600
DEFAULT_TIMEOUT = 300.0


class ChecksFileError(ValueError):
    """A checks file that cannot be used; the message says what to fix."""


@dataclass(frozen=True)
class ChecksPin:
    """What was recorded when the run pinned its checks."""

    digest: str = ""
    script_digests: dict[str, str] = field(default_factory=dict)


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _argv(raw: Any, where: str) -> list[str]:
    if isinstance(raw, str):
        try:
            argv = shlex.split(raw)
        except ValueError as exc:
            raise ChecksFileError(f"{where}: the command is not valid ({exc}).") from exc
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        argv = list(raw)
    else:
        raise ChecksFileError(f"{where}: command must be a string or a list of strings.")
    if not argv:
        raise ChecksFileError(f"{where}: needs a command.")
    if any(token in SHELL_CONTROL_TOKENS for token in argv):
        raise ChecksFileError(
            f"{where}: a shell operator (&&, |, >, ...) is not allowed; checks run without a "
            "shell. Put the steps in a script and run that."
        )
    return argv


def _check(entry: Any, index: int) -> CheckSpec:
    if not isinstance(entry, dict):
        raise ChecksFileError(f"Check {index + 1} must be a mapping with an id and a command.")
    check_id = entry.get("id")
    if not isinstance(check_id, str) or not check_id.strip():
        raise ChecksFileError(f"Check {index + 1} needs an id.")
    where = f"Check '{check_id}'"
    if not ID_PATTERN.fullmatch(check_id):
        raise ChecksFileError(
            f"{where}: the id must be letters, digits, and . _ : - (at most 64 characters)."
        )
    unknown = sorted(set(entry) - FIELDS)
    if unknown:
        raise ChecksFileError(
            f"{where}: unknown field(s) {', '.join(unknown)}; allowed: {', '.join(sorted(FIELDS))}."
        )
    kind = entry.get("kind", "custom")
    if kind not in KINDS:
        raise ChecksFileError(f"{where}: kind must be one of {', '.join(KINDS)}.")
    check_type = entry.get("type", "command")
    if check_type not in ("command", "browser_script"):
        raise ChecksFileError(f"{where}: type must be 'command' or 'browser_script'.")
    timeout = entry.get("timeout", DEFAULT_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        raise ChecksFileError(f"{where}: timeout must be a number of seconds.")
    if not 0 < timeout <= MAX_TIMEOUT:
        raise ChecksFileError(f"{where}: timeout must be between 1 and {MAX_TIMEOUT} seconds.")
    criteria = entry.get("criteria", [])
    if not isinstance(criteria, list) or not all(isinstance(c, str) for c in criteria):
        raise ChecksFileError(f"{where}: criteria must be a list of criterion ids, e.g. [AC-1].")
    required = entry.get("required", True)
    if not isinstance(required, bool):
        raise ChecksFileError(f"{where}: required must be true or false.")
    cwd = entry.get("cwd", ".")
    if not isinstance(cwd, str) or cwd.startswith("/") or ".." in Path(cwd).parts:
        raise ChecksFileError(f"{where}: cwd must be a directory inside the project.")
    script: str | None = None
    argv: list[str] = []
    if check_type == "browser_script":
        script = entry.get("script")
        if not isinstance(script, str) or not script.strip():
            raise ChecksFileError(
                f"{where}: a browser_script check needs a script (a project path)."
            )
        if script.startswith("/") or ".." in Path(script).parts or "command" in entry:
            raise ChecksFileError(f"{where}: give a script path inside the project, not a command.")
    elif "command" not in entry:
        raise ChecksFileError(f"{where}: needs a command.")
    else:
        argv = _argv(entry["command"], where)
    return CheckSpec(
        id=check_id,
        name=str(entry.get("name") or check_id),
        argv=argv,
        required=required,
        timeout=float(timeout),
        criteria_ids=list(criteria),
        kind=kind,
        type=check_type,
        source="user",
        cwd=cwd,
        script=script,
    )


def parse_checks(text: str, *, source: str) -> list[CheckSpec]:
    """The checks in a file's text; raises :class:`ChecksFileError` naming the first problem."""

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ChecksFileError(f"{source} is not valid YAML: {exc}") from exc
    entries = data.get("checks") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ChecksFileError(
            f"{source} must be a list of checks (or a mapping with a 'checks:' list); each "
            "check has an id and a command."
        )
    checks = [_check(entry, index) for index, entry in enumerate(entries)]
    seen: set[str] = set()
    for check in checks:
        if check.id in seen:
            raise ChecksFileError(f"{source}: the check id '{check.id}' is used twice.")
        seen.add(check.id)
    return checks


def load_checks_file(path: Path, workspace_root: Path) -> tuple[list[CheckSpec], str]:
    """Read and validate ``path``; returns the checks and the file's text.

    A file inside the project (where an agent could edit it) is refused.
    """

    path = path.expanduser()
    if not path.is_file():
        raise ChecksFileError(f"Checks file not found: {path}. Pass --checks FILE.")
    resolved, root = path.resolve(), workspace_root.resolve()
    if resolved == root or root in resolved.parents:
        raise ChecksFileError(
            f"The checks file {path} is inside the project. Keep it outside the project "
            "(agents can edit files in it), e.g. next to your request file."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ChecksFileError(f"Cannot read checks file {path}: {exc}") from exc
    return parse_checks(text, source=str(path)), text


def _script_digests(ctx: RunContext, checks: list[CheckSpec]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for check in checks:
        if check.type != "browser_script" or check.script is None:
            continue
        target = ctx.workspace.root / check.script
        try:
            digests[check.script] = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            raise ChecksFileError(
                f"Check '{check.id}': the script {check.script} does not exist in the project. "
                "Put the test file there before the run (its content is pinned)."
            ) from None
    return digests


def pin_checks(ctx: RunContext, source: Path | None) -> ChecksPin:
    """Copy the user's checks file into the run directory and fingerprint it (and the scripts
    its browser_script checks name). With no file there is nothing to pin."""

    if source is None:
        return ChecksPin()
    checks, text = load_checks_file(source, ctx.workspace.root)
    digests = _script_digests(ctx, checks)
    atomic_write_text(ctx.run_dir / PINNED_FILENAME, text)
    return ChecksPin(digest_of(text), digests)


def pinned_checks(ctx: RunContext, digest: str) -> list[CheckSpec]:
    """The run's pinned user checks, after proving the pinned copy is the one recorded."""

    path = ctx.run_dir / PINNED_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError) as exc:
        raise ChecksFileError(f"Cannot read the pinned checks {path}: {exc}") from exc
    if (digest_of(text) if text else "") != digest:
        raise ChecksFileError(
            "The check definitions changed since the run started (the pinned copy no longer "
            "matches its recorded hash), so nothing they show can be trusted. Start a new run."
        )
    return parse_checks(text, source=str(path)) if text else []
