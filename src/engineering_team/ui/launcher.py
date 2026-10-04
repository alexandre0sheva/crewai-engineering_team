"""Starting runs as CLI subprocesses, and remembering them.

A run is ``python -m engineering_team <globals> <mode> <options>`` in a process of its own
(its own session, so stopping the server does not stop it). The server names the run first
(``--run-id``) so it can find the run again once the process has created it. Per start it keeps a
small record (pid, log file) in ``<workspace root>/.engineering-team-ui/``; that is how a run
that died before it could write a manifest is still explained, and how the server counts the runs
it has going after a restart.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from engineering_team.atomic_io import atomic_write_json
from engineering_team.runtime.context import new_run_id
from engineering_team.settings import (
    OVERRIDES_ENV,
    SettingsError,
    child_environment,
    load_settings,
)
from engineering_team.workspaces import resolve_workspace_root

STATE_DIRECTORY = ".engineering-team-ui"
MAX_LOG_TAIL = 4000
Mode = Literal["new", "feature", "fix", "maintain", "review"]
REPO_MODES = ("feature", "fix", "maintain", "review")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
REQUEST_SUFFIXES = (".md", ".markdown", ".txt", ".rst")
CONTEXT_SUFFIXES = (".md", ".markdown", ".txt", ".rst", ".json", ".yaml", ".yml", ".csv", ".toml")


class StartError(ValueError):
    """A start that cannot happen; ``status`` is the HTTP status that describes why."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_cost_usd: float | None = Field(default=None, gt=0)
    max_tokens: int | None = Field(default=None, ge=1)
    max_wall_seconds: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=1)


class RunOptions(BaseModel):
    """The options every start (and a resume) may carry; each is what the CLI option of the same
    name does, or the setting of the same name."""

    model_config = ConfigDict(extra="forbid")

    strategy: Literal["hierarchical", "pipeline", "single"] | None = None
    profile: str | None = None
    provider: str | None = None
    sandbox: Literal["local", "docker"] | None = None
    allow_web: bool = False
    budget: Budget | None = None
    max_parallel_agents: int | None = Field(default=None, ge=1, le=16)
    disabled_teammates: list[str] = Field(default_factory=list)  # roster toggles


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = "new"
    request: str | None = Field(default=None, max_length=200_000)  # text; or upload files
    example: str | None = None
    repo: str | None = None  # the project directory (feature, fix, maintain, review)
    project_name: str | None = None
    # Mode-specific.
    task: str | None = None  # maintain
    goal: str | None = None  # maintain
    fix_findings: bool = False  # maintain security-audit: also fix what it finds
    base: str | None = None  # review
    focus: str | None = None  # review
    repro: str | None = None  # fix
    trace: str | None = Field(default=None, max_length=200_000)  # fix: a stack trace or log
    allow_unreproduced: bool = False  # fix
    worktree: bool = False  # isolation (feature, fix, maintain)
    allow_dirty: bool = False
    squash: bool = False
    no_git: bool = False  # new
    interactive: bool = True  # the team may ask questions, answered through the API
    options: RunOptions = Field(default_factory=RunOptions)


@dataclass(frozen=True)
class Upload:
    name: str
    data: bytes


class StartRecord(BaseModel):
    run_id: str
    pid: int
    mode: str
    resume: bool = False
    log: str
    started: float
    argv: list[str]


def overrides_for(options: RunOptions) -> dict[str, Any]:
    """The dotted settings the options set (the value of ``ENGINEERING_OVERRIDES``)."""

    found: dict[str, Any] = {}
    if options.budget is not None:
        for key, value in options.budget.model_dump().items():
            if value is not None:
                found[f"budget.{key}"] = value
    if options.max_parallel_agents is not None:
        found["parallel.max_parallel_agents"] = options.max_parallel_agents
    for key in options.disabled_teammates:
        found[f"team.{key}.enabled"] = False
    return found


def safe_name(name: str, suffixes: Sequence[str]) -> str:
    """A file name that is only itself: no directories, no odd characters, a known suffix."""

    base = SAFE_NAME.sub("-", Path(name.replace("\\", "/")).name).strip(".-")
    if not base or not base.lower().endswith(tuple(suffixes)):
        raise StartError(f"{name!r} is not an accepted file; use one of: {', '.join(suffixes)}.")
    return base[:100]


class RunLauncher:
    """Starts runs and keeps their records. ``command`` is how to invoke the CLI (tests replace
    it)."""

    def __init__(
        self,
        workspace_root: str,
        *,
        max_concurrent: int,
        command: Sequence[str] | None = None,
        config_file: str | None = None,
    ) -> None:
        self.root = resolve_workspace_root(workspace_root)
        self.state_dir = self.root / STATE_DIRECTORY
        self.max_concurrent = max_concurrent
        self.command = list(command or (sys.executable, "-m", "engineering_team"))
        self.config_file = config_file
        self._children: dict[str, subprocess.Popen[bytes]] = {}

    # -- records ------------------------------------------------------------------------

    def record(self, run_id: str) -> StartRecord | None:
        try:
            return StartRecord.model_validate_json(
                (self.state_dir / "starts" / f"{run_id}.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None

    def records(self) -> list[StartRecord]:
        folder = self.state_dir / "starts"
        found = (
            [self.record(path.stem) for path in sorted(folder.glob("*.json"))]
            if folder.is_dir()
            else []
        )
        return [record for record in found if record is not None]

    def alive(self, record: StartRecord) -> bool:
        child = self._children.get(record.run_id)
        if child is not None:
            return child.poll() is None  # also reaps a finished child
        return _pid_alive(record.pid)

    def active(self) -> list[StartRecord]:
        return [record for record in self.records() if self.alive(record)]

    def log_tail(self, record: StartRecord) -> str:
        try:
            text = Path(record.log).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-MAX_LOG_TAIL:].strip()

    def process_info(self, run_id: str) -> dict[str, Any] | None:
        record = self.record(run_id)
        if record is None:
            return None
        alive = self.alive(record)
        child = self._children.get(run_id)
        return {
            "pid": record.pid,
            "alive": alive,
            "exit_code": child.returncode if child is not None and not alive else None,
            "resume": record.resume,
        }

    # -- starting -------------------------------------------------------------------------

    def start(self, spec: StartRun, uploads: dict[str, list[Upload]]) -> StartRecord:
        """Validate, write the uploads, and start the process; returns its record."""

        self._check_capacity()
        overrides = overrides_for(spec.options)
        self._validate_settings(overrides)
        run_id = new_run_id()
        upload_dir = self.state_dir / "uploads" / run_id
        argv = self._arguments(spec, run_id, upload_dir, uploads)
        return self._spawn(run_id, spec.mode, argv, overrides, resume=False)

    def resume(self, run_id: str, options: RunOptions) -> StartRecord:
        """Continue a run that did not finish, in a new process."""

        self._check_capacity()
        overrides = overrides_for(options)
        self._validate_settings(overrides)
        argv = ["--workspace-root", str(self.root), "--answers-via-inbox", "resume", run_id]
        argv += _common_options(options, resume=True)
        if self.config_file:
            argv += ["--config", self.config_file]
        return self._spawn(run_id, "resume", argv, overrides, resume=True)

    def stop_all(self) -> None:
        """Ask every run this server started to stop (used only when the server is told to)."""

        for record in self.active():
            with contextlib.suppress(OSError):
                os.kill(record.pid, signal.SIGINT)

    # -- internals ----------------------------------------------------------------------------

    def _check_capacity(self) -> None:
        busy = len(self.active())
        if busy >= self.max_concurrent:
            raise StartError(
                f"{busy} run(s) are already going (ui.max_concurrent_runs is "
                f"{self.max_concurrent}). Wait for one to finish, or cancel it.",
                429,
            )

    def _validate_settings(self, overrides: dict[str, Any]) -> None:
        """Reject options the settings would reject, before a process is started."""

        env = child_environment({OVERRIDES_ENV: json.dumps(overrides)}) if overrides else None
        try:
            load_settings(env=env, config_file=self.config_file)
        except SettingsError as exc:
            raise StartError(str(exc)) from exc

    def _arguments(
        self, spec: StartRun, run_id: str, upload_dir: Path, uploads: dict[str, list[Upload]]
    ) -> list[str]:
        mode = spec.mode
        argv = ["--workspace-root", str(self.root), "--run-id", run_id]
        if spec.interactive:
            argv.append("--answers-via-inbox")
        argv.append(mode)
        requests = self._save(
            upload_dir, "request", uploads.get("request_files", []), REQUEST_SUFFIXES
        )
        if spec.request is not None and spec.request.strip():
            requests.append(self._write(upload_dir / "request", "request.md", spec.request))
        if mode in ("new", "feature", "fix") and not requests and not spec.example:
            raise StartError("Say what to do: send 'request' text or a request file.")
        context = self._save(
            upload_dir, "context", uploads.get("context_files", []), CONTEXT_SUFFIXES
        )
        if mode in REPO_MODES:
            argv += ["--repo", self._repo(spec.repo)]
        if mode in ("new", "feature", "fix"):
            for path in requests:
                argv += ["--request-file", str(path)]
        if context and mode != "review":
            argv += ["--context-dir", str(upload_dir / "context")]
        if spec.project_name:
            argv += [f"--project-name={spec.project_name}"]
        argv += _common_options(spec.options, mode=mode)
        argv += self._mode_options(spec, mode, upload_dir, requests)
        if self.config_file:
            argv += ["--config", self.config_file]
        return argv

    # Free text goes in as ``--flag=value``: a value that starts with ``-`` is then a value, never
    # an option of its own (``--base=--help``).
    def _mode_options(
        self, spec: StartRun, mode: str, upload_dir: Path, requests: list[Path]
    ) -> list[str]:
        out: list[str] = []
        if mode == "new":
            if spec.example:
                out += [f"--example={spec.example}"]
            if spec.no_git:
                out.append("--no-git")
        if mode in ("feature", "fix", "maintain"):
            out += [flag for flag, on in (
                ("--worktree", spec.worktree), ("--allow-dirty", spec.allow_dirty),
                ("--squash", spec.squash),
            ) if on]  # fmt: skip
        if mode == "fix":
            if spec.repro:
                out += [f"--repro={spec.repro}"]
            if spec.allow_unreproduced:
                out.append("--allow-unreproduced")
            if spec.trace and spec.trace.strip():
                trace = self._write(upload_dir, "trace.txt", spec.trace)
                out += ["--trace-file", str(trace)]
        if mode == "maintain":
            if not spec.task:
                raise StartError(
                    "maintain needs a 'task' (add-tests, refactor, upgrade-deps, ...)."
                )
            out += [f"--task={spec.task}"]
            goal = spec.goal or (requests[0].read_text(encoding="utf-8") if requests else "")
            if goal.strip():
                out += [f"--goal={goal.strip()[:20_000]}"]
            if spec.fix_findings:
                out.append("--fix")
        if mode == "review":
            if spec.base:
                out += [f"--base={spec.base}"]
            if spec.focus:
                out += [f"--focus={spec.focus}"]
        return out

    def _repo(self, repo: str | None) -> str:
        if not repo or "\0" in repo:
            raise StartError("This mode needs 'repo': the project directory.")
        path = Path(repo).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise StartError(f"{repo!r} is not an existing directory (give an absolute path).")
        return str(path.resolve())

    def _write(self, directory: Path, name: str, text: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(text, encoding="utf-8")
        return path

    def _save(
        self, base: Path, kind: str, files: list[Upload], suffixes: Sequence[str]
    ) -> list[Path]:
        saved: list[Path] = []
        directory = base / kind
        for upload in files:
            name = safe_name(upload.name, suffixes)
            try:
                text = upload.data.decode("utf-8")
            except UnicodeDecodeError:
                raise StartError(f"{upload.name!r} is not UTF-8 text.") from None
            if "\0" in text:
                raise StartError(f"{upload.name!r} is not a text file.")
            saved.append(self._write(directory, name, text))
        return saved

    def _spawn(
        self,
        run_id: str,
        mode: str,
        argv: list[str],
        overrides: dict[str, Any],
        *,
        resume: bool,
    ) -> StartRecord:
        logs = self.state_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs / f"{run_id}{'-resume' if resume else ''}.log"
        extra = {"PYTHONUNBUFFERED": "1", "NO_COLOR": "1"}
        if overrides:
            extra[OVERRIDES_ENV] = json.dumps(overrides)
        env = child_environment(extra)
        full = [*self.command, *argv]
        with log_path.open("ab") as log:
            child = subprocess.Popen(  # noqa: S603 - an argument list, never a shell
                full,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,  # the run outlives this server
            )
        self._children[run_id] = child
        record = StartRecord(
            run_id=run_id,
            pid=child.pid,
            mode=mode,
            resume=resume,
            log=str(log_path),
            started=time.time(),
            argv=full,
        )
        atomic_write_json(self.state_dir / "starts" / f"{run_id}.json", record.model_dump())
        return record


def _common_options(options: RunOptions, *, mode: str = "new", resume: bool = False) -> list[str]:
    out: list[str] = []
    if options.provider:
        out += ["--provider", options.provider]
    if options.profile:
        out += ["--profile", options.profile]
    if mode == "review":
        return out
    if options.allow_web:
        out.append("--allow-web")
    if options.sandbox:
        out += ["--sandbox", options.sandbox]
    if options.strategy and mode == "new" and not resume:
        out += ["--strategy", options.strategy]
    return out


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists, but is not ours to signal
    return True
