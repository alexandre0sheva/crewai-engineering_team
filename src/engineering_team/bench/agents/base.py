"""What an external coding agent is to the benchmark, and the one way to run its CLI.

An :class:`ExternalAgent` is another tool (Claude Code, Codex, ...) asked to do a task in a
workspace. Every adapter starts the tool's own command line, non-interactively, through an
:class:`~engineering_team.execution.backend.ExecutionBackend`, then reads what the tool reports
about itself (cost, tokens, models). Only the *hidden checks* decide whether it succeeded; what the
tool says about its own work is never evidence.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal, Protocol, runtime_checkable

from engineering_team.execution.backend import CommandSpec, ExecutionBackend
from engineering_team.pricing import PriceTable, build_table

# api: billed per token. subscription: covered by a plan (Claude Code on a Pro/Max login, Codex on a
# ChatGPT login), so money spent is not known. unknown: the agent can be either.
Billing = Literal["api", "subscription", "unknown"]
BILLINGS: tuple[str, ...] = ("api", "subscription", "unknown")
VERSION_TIMEOUT = 20.0


@dataclass(frozen=True)
class Report:
    """What an agent's own output says about a run. Every field may be missing; none is evidence
    of success."""

    cost_usd: float | None = None  # as reported (or priced from reported tokens); None: unknown
    prompt_tokens: int = 0  # the full billed prompt, cache reads included
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    models: tuple[str, ...] = ()
    turns: int = 0
    error: str = ""  # the agent said it failed, and why


@dataclass(frozen=True)
class AgentRun:
    """One agent run: how it ended, how long it took, and what it says it cost."""

    exit_code: int | None  # None: the process never started
    wall_s: float
    cost_usd: float | None  # None: unknown; for a subscription this is only an equivalent
    transcript_path: Path | None  # the agent's combined output, as a log file
    billing: Billing = "unknown"
    timed_out: bool = False
    cancelled: bool = False
    report: Report = field(default_factory=Report)
    error: str = ""  # why it could not run at all (not found, not startable)


@runtime_checkable
class ExternalAgent(Protocol):
    """A coding agent the benchmark can run on a task, in a workspace of its own."""

    name: str

    def available(self) -> bool:
        """Whether its command is on ``PATH``."""

    def version(self) -> str | None:
        """What its ``--version`` prints (``None``: not installed, or no answer)."""

    def billing(self) -> Billing:
        """How this environment pays for it."""

    def run(self, workspace: Path, request: str, timeout: float) -> AgentRun:
        """Ask it to do ``request`` in ``workspace``; stop it after ``timeout`` seconds."""


def json_objects(text: str) -> list[dict[str, object]]:
    """The JSON objects in an agent's output: the whole text if it is one document (an object, or
    a list of them), otherwise every line that is an object. Text around them is ignored."""

    try:
        whole = json.loads(text)
    except ValueError:
        whole = None
    if isinstance(whole, dict):
        return [whole]
    if isinstance(whole, list):
        return [item for item in whole if isinstance(item, dict)]
    found: list[dict[str, object]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            found.append(item)
    return found


def count(value: object) -> int:
    """A token count from a JSON value (anything that is not a number is zero)."""

    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


class CliAgent:
    """Shared behaviour of the adapters: find the command, run it, read its report.

    A subclass names the command (:attr:`binary`), builds its arguments (:meth:`arguments`),
    parses its output (:meth:`parse`) and says how it is paid for (:meth:`detect_billing`).
    """

    name: ClassVar[str]
    binary: ClassVar[str]
    version_args: ClassVar[tuple[str, ...]] = ("--version",)
    caps_cost: ClassVar[bool] = False  # whether its command line can stop a run at a spend cap

    def __init__(
        self,
        backend: ExecutionBackend,
        *,
        model: str | None = None,
        max_cost_usd: float | None = None,
        env: Mapping[str, str] | None = None,
        billing: Billing | None = None,
        prices: PriceTable | None = None,
    ) -> None:
        self.backend = backend
        self.model = model
        self.max_cost_usd = max_cost_usd
        self.env = dict(os.environ if env is None else env)
        self._billing = billing
        self._prices = prices
        self._version: str | None = None
        self._version_known = False

    @property
    def prices(self) -> PriceTable:
        if self._prices is None:
            self._prices = build_table()
        return self._prices

    def executable(self) -> str | None:
        return shutil.which(self.binary, path=self.env.get("PATH"))

    def available(self) -> bool:
        return self.executable() is not None

    def version(self) -> str | None:
        if not self._version_known:
            self._version, self._version_known = self._ask_version(), True
        return self._version

    def _ask_version(self) -> str | None:
        exe = self.executable()
        if exe is None:
            return None
        try:
            record = self.backend.run(
                CommandSpec(
                    argv=(exe, *self.version_args),
                    cwd=Path.cwd(),
                    env=self.env,
                    timeout=VERSION_TIMEOUT,
                    label=f"{self.name} --version",
                )  # fmt: skip
            )
        except OSError:
            return None
        if record.exit_code != 0:
            return None
        lines = [line.strip() for line in record.output_tail.splitlines() if line.strip()]
        return lines[0][:80] if lines else None

    def billing(self) -> Billing:
        return self._billing or self.detect_billing(self.env)

    def detect_billing(self, env: Mapping[str, str]) -> Billing:
        return "unknown"

    def arguments(self, request: str, workspace: Path) -> list[str]:
        raise NotImplementedError

    def parse(self, output: str) -> Report:
        raise NotImplementedError

    def run(self, workspace: Path, request: str, timeout: float) -> AgentRun:
        billing = self.billing()
        exe = self.executable()
        if exe is None:
            return AgentRun(
                None, 0.0, None, None, billing,
                error=f"`{self.binary}` is not on PATH: install {self.name} or fix PATH.",
            )  # fmt: skip
        spec = CommandSpec(
            argv=(exe, *self.arguments(request, workspace)), cwd=workspace, env=self.env,
            timeout=timeout, label=self.name, network=True,
        )  # fmt: skip
        try:
            record = self.backend.run(spec)
        except OSError as exc:
            return AgentRun(None, 0.0, None, None, billing, error=f"could not start {exe}: {exc}")
        try:
            output = record.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            output = record.output_tail
        report = self.parse(output)
        return AgentRun(
            exit_code=record.exit_code, wall_s=round(record.duration, 2), cost_usd=report.cost_usd,
            transcript_path=record.log_path, billing=billing, timed_out=record.timed_out,
            cancelled=record.cancelled, report=report,
        )  # fmt: skip
