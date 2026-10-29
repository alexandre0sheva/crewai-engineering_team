"""External coding agents the benchmark runs beside the team (``bench run --agent``)."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from engineering_team.bench.agents.aider import Aider
from engineering_team.bench.agents.base import (
    BILLINGS,
    AgentRun,
    Billing,
    CliAgent,
    ExternalAgent,
    Report,
)
from engineering_team.bench.agents.claude_code import ClaudeCode
from engineering_team.bench.agents.codex import Codex
from engineering_team.bench.agents.opencode import OpenCode
from engineering_team.execution.backend import ExecutionBackend
from engineering_team.execution.local import LocalBackend

AGENTS: dict[str, type[CliAgent]] = {cls.name: cls for cls in (ClaudeCode, Codex, OpenCode, Aider)}
AGENT_NAMES: tuple[str, ...] = tuple(AGENTS)

__all__ = [
    "AGENTS", "AGENT_NAMES", "BILLINGS", "AgentInfo", "AgentRun", "Aider", "Billing", "CliAgent",
    "ClaudeCode", "Codex", "ExternalAgent", "OpenCode", "Report", "build_agent", "probe_agent",
]  # fmt: skip


def build_agent(
    name: str,
    backend: ExecutionBackend,
    *,
    model: str | None = None,
    max_cost_usd: float | None = None,
    env: Mapping[str, str] | None = None,
    billing: Billing | None = None,
) -> CliAgent:
    if name not in AGENTS:
        raise ValueError(f"Unknown agent {name!r}. Choose from {', '.join(AGENT_NAMES)}.")
    return AGENTS[name](backend, model=model, max_cost_usd=max_cost_usd, env=env, billing=billing)


@dataclass(frozen=True)
class AgentInfo:
    """What a batch needs to know about an agent before it starts."""

    name: str
    available: bool
    version: str | None
    billing: Billing
    caps_cost: bool


def probe_agent(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
    billing: Billing | None = None,
) -> AgentInfo:
    """Whether the agent's command exists, its version, and how this environment pays for it."""

    with tempfile.TemporaryDirectory(prefix="bench-agent-") as scratch:
        agent = build_agent(name, LocalBackend(Path(scratch)), env=env, billing=billing)
        return AgentInfo(
            name=name, available=agent.available(), version=agent.version(),
            billing=agent.billing(), caps_cost=agent.caps_cost,
        )  # fmt: skip
