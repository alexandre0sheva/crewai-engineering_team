"""``doctor``: is this machine ready to run the team, and what to do if not."""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from rich import box
from rich.table import Table
from rich.text import Text

from engineering_team.cli.context import get as get_globals
from engineering_team.cli.context import print_json
from engineering_team.execution.backend import CommandSpec
from engineering_team.execution.docker import DockerStatus, docker_status
from engineering_team.execution.local import LocalBackend
from engineering_team.model_routing import ResolvedModel
from engineering_team.settings import Settings
from engineering_team.tools.commands import DEFAULT_COMMAND_ALLOWLIST
from engineering_team.workspaces import resolve_workspace_root

VERSION = re.compile(r"\d+(?:\.\d+)+\S*")
PROBE_SECONDS = 8.0
SUPPORTED_PYTHON = ((3, 11), (3, 14))  # >=3.11, <3.14
# Language runtimes: (name, the executables the allowlist must contain for it to matter, probe)
RUNTIMES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("node", ("node", "npm"), ("node", "--version")),
    ("go", ("go",), ("go", "version")),
    ("java", ("java",), ("java", "-version")),
    ("rust", ("cargo",), ("cargo", "--version")),
    ("dotnet", ("dotnet",), ("dotnet", "--version")),
)
Which = Callable[[str], str | None]
Probe = Callable[[tuple[str, ...]], str]
Ping = Callable[[ResolvedModel, Settings], str]


@dataclass(frozen=True)
class Check:
    """One line of the report. ``status``: ok, warn (works, but worse), fail, or info."""

    name: str
    status: str
    detail: str
    hint: str = ""


def probe_version(argv: tuple[str, ...]) -> str:
    """The first version number a program prints for ``argv`` ("" when it cannot be run)."""

    with tempfile.TemporaryDirectory(prefix="engineering-team-doctor-") as scratch:
        record = LocalBackend(Path(scratch)).run(
            CommandSpec(
                argv=argv,
                cwd=Path(scratch),
                env={"PATH": "/usr/bin:/bin"},
                timeout=PROBE_SECONDS,
                label="doctor",
            )
        )
    match = VERSION.search(record.output_tail)
    return match.group(0).strip("\"'),;") if record.exit_code == 0 and match else ""


def _probe_with(which: Which, probe: Probe, argv: tuple[str, ...]) -> tuple[str | None, str, str]:
    """``(path, version, problem)`` for a program: ``path`` is ``None`` when it is not installed,
    ``problem`` says why a program that is installed cannot run (a binary for another CPU)."""

    path = which(argv[0])
    if path is None:
        return None, "", ""
    try:
        # Run the program found, not whatever a minimal PATH would find.
        return path, probe((path, *argv[1:])), ""
    except OSError as exc:
        return path, "", f"found at {path} but cannot run ({exc.strerror or exc})"


def _found(name: str, path: str | None, version: str, problem: str, missing: Check) -> Check:
    if path is None:
        return missing
    if problem:
        return Check(name, "warn", problem)
    return Check(name, "ok", version or f"found at {path}")


def ping_model(resolved: ResolvedModel, settings: Settings) -> str:
    """A tiny prompt to one model; returns its (clipped) answer or raises."""

    from engineering_team.crew import build_llm

    answer = build_llm(resolved, settings).call("Reply with the single word OK.")
    return " ".join(str(answer).split())[:40]


def run_checks(
    settings: Settings,
    *,
    which: Which = shutil.which,
    probe: Probe = probe_version,
    docker: Callable[[], DockerStatus] = docker_status,
    online: bool = False,
    ping: Ping = ping_model,
    python: tuple[int, int, int] | None = None,
) -> list[Check]:
    """Every check, in the order they are shown. Never raises and never prints a secret."""

    checks: list[Check] = []
    major, minor, micro = python or sys.version_info[:3]
    supported = SUPPORTED_PYTHON[0] <= (major, minor) < SUPPORTED_PYTHON[1]
    checks.append(
        Check(
            "Python",
            "ok" if supported else "fail",
            f"{major}.{minor}.{micro}",
            "" if supported else "Use Python 3.11, 3.12, or 3.13.",
        )
    )
    for name, argv, why, status in (
        ("uv", ("uv", "--version"), "installs and runs Python projects", "warn"),
        ("git", ("git", "--version"), "history and checkpoints", "warn"),
    ):
        path, version, problem = _probe_with(which, probe, argv)
        hint = "https://docs.astral.sh/uv/" if name == "uv" else "Install Git, or pass --no-git."
        missing = Check(name, status, f"not found ({why} need it)", hint)
        checks.append(_found(name, path, version, problem, missing))
    checks.append(_docker_check(settings, docker))
    checks.extend(_runtime_checks(settings, which, probe))
    checks.extend(_provider_checks(settings))
    checks.append(_browser_check(settings))
    checks.append(_ui_check())
    checks.append(_team_check(settings))
    checks.append(_workspace_check(settings))
    if online:
        checks.extend(_online_checks(settings, ping))
    return checks


def _docker_check(settings: Settings, docker: Callable[[], DockerStatus]) -> Check:
    status = docker()
    wanted = settings.execution.backend == "docker"
    if status.ok:
        hint = "" if wanted else "Recommended: run with --sandbox docker for untrusted requests."
        return Check("Docker", "ok", status.message, hint)
    return Check("Docker", "fail" if wanted else "info", status.message, status.hint or "")


def _runtime_checks(settings: Settings, which: Which, probe: Probe) -> list[Check]:
    allowed = DEFAULT_COMMAND_ALLOWLIST | set(settings.command_allowlist)
    out: list[Check] = []
    for name, needs, argv in RUNTIMES:
        if not all(item in allowed for item in needs):
            continue
        path, version, problem = _probe_with(which, probe, argv)
        missing = Check(name, "info", "not installed", f"Only needed for {name} projects.")
        out.append(_found(name, path, version, problem, missing))
    return out


def _provider_checks(settings: Settings) -> list[Check]:
    problems = [
        (Check("Credentials", "fail", item, "Put the key in .env (see .env.example)."))
        for item in settings.missing_credentials()
    ]
    problems += [Check("Provider SDK", "fail", item) for item in settings.sdk_problems()]
    if problems:
        return problems
    return [
        Check(
            "Credentials",
            "ok",
            f"present for {', '.join(sorted(settings.used_providers())) or settings.provider}",
        )
    ]


def _browser_check(settings: Settings) -> Check:
    if importlib.util.find_spec("playwright") is None:
        return Check(
            "Browser tools",
            "info",
            "Playwright not installed",
            "Optional: uv sync --extra browser, then playwright install chromium.",
        )
    return Check(
        "Browser tools",
        "ok",
        "Playwright installed",
        "If a browser tool fails: playwright install chromium.",
    )


def _ui_check() -> Check:
    from engineering_team.ui import INSTALL_HINT, missing_dependencies

    if missing := missing_dependencies():
        return Check(
            "Web UI",
            "info",
            f"not installed ({', '.join(missing)} missing)",
            f"Optional: {INSTALL_HINT}, then `engineering-team ui`.",
        )
    return Check(
        "Web UI", "ok", "FastAPI and uvicorn installed", "Start it with: engineering-team ui"
    )


def _team_check(settings: Settings) -> Check:
    """The team definition is valid, and what a teammate asks for but cannot have is said."""

    from engineering_team.team import TeamError, build_roster, group_notes

    try:
        roster = build_roster(settings)
    except TeamError as exc:
        return Check("Team", "fail", str(exc), "See docs/TEAM.md, or run `engineering-team team`.")
    notes = [note for member in roster.enabled() for note in group_notes(member)]
    if settings.team and settings.web.roles and not settings.web.enabled:
        notes.append("web.roles names teammates but web.enabled is false: they get no web tools.")
    detail = f"{len(roster.enabled())} of {len(roster.all())} teammates enabled"
    if notes:
        return Check("Team", "warn", detail, " ".join(notes))
    return Check("Team", "ok", detail)


def _workspace_check(settings: Settings) -> Check:
    try:
        root = resolve_workspace_root(settings.workspace_root)
    except ValueError as exc:
        return Check("Workspace root", "fail", str(exc))
    target = root if root.exists() else root.parent
    ok = target.is_dir() and _writable(target)
    return Check(
        "Workspace root",
        "ok" if ok else "fail",
        str(root),
        "" if ok else "Choose a writable --workspace-root.",
    )


def _writable(directory: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=directory):
            return True
    except OSError:
        return False


def _online_checks(settings: Settings, ping: Ping) -> list[Check]:
    out = []
    seen: set[str] = set()
    for resolved in settings.resolved_models():
        if resolved.model in seen:
            continue
        seen.add(resolved.model)
        try:
            answer = ping(resolved, settings)
        except Exception as exc:  # any provider failure is a finding, not a crash
            out.append(
                Check(
                    f"Model {resolved.model}",
                    "fail",
                    f"{type(exc).__name__}: {str(exc)[:120]}",
                    f"tier {resolved.tier}",
                )
            )
        else:
            out.append(
                Check(f"Model {resolved.model}", "ok", f"answered ({resolved.tier}): {answer}")
            )
    return out


STYLE = {"ok": ("✓", "green"), "warn": ("!", "yellow"), "fail": ("✗", "red"), "info": ("-", "dim")}


def doctor(
    ctx: typer.Context,
    online: Annotated[
        bool, typer.Option(help="Also send one tiny prompt to each model tier.")
    ] = False,
    provider: Annotated[str | None, typer.Option(help="Check this provider's preset.")] = None,
    config: Annotated[str | None, typer.Option(help="Path to a config file.")] = None,
) -> None:
    """Check Python, uv, Git, Docker, language runtimes, and provider credentials."""

    from engineering_team.cli.info_commands import load

    g = get_globals(ctx)
    overrides: dict[str, Any] = {"provider": provider} if provider else {}
    settings = load(g, config, **overrides)
    checks = run_checks(settings, online=online)
    failed = any(check.status == "fail" for check in checks)
    if g.json:
        print_json({"ok": not failed, "checks": [asdict(check) for check in checks]})
    else:
        table = Table(box=box.SIMPLE, pad_edge=False, show_header=False)
        for check in checks:
            mark, style = STYLE[check.status]
            table.add_row(
                Text(mark, style=style),
                Text(check.name, style="bold"),
                check.detail,
                Text(check.hint, style="dim"),
            )
        g.console().print(table)
        g.console().print(
            "Ready." if not failed else "Fix the items marked ✗ before starting a run.",
            style="green" if not failed else "red",
        )
    raise typer.Exit(1 if failed else 0)
