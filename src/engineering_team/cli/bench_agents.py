"""``bench run --agent``: which external agents a batch compares the team with, and how."""

from __future__ import annotations

from collections.abc import Sequence

from engineering_team.bench.agents import AGENT_NAMES, BILLINGS, AgentInfo, Billing, probe_agent
from engineering_team.cli.context import fail


def agent_names(values: Sequence[str] | None) -> list[str]:
    chosen = [a.strip() for raw in values or [] for a in raw.split(",") if a.strip()]
    unknown = [a for a in chosen if a not in AGENT_NAMES]
    if unknown:
        fail(f"Unknown agent: {', '.join(unknown)}. Choose from {', '.join(AGENT_NAMES)}.")
    return list(dict.fromkeys(chosen))


def agent_pairs(
    values: Sequence[str] | None, option: str, agents: Sequence[str], allowed: Sequence[str] = ()
) -> dict[str, str]:
    """``AGENT=VALUE`` pairs of an option (``--agent-model``, ``--agent-billing``)."""

    pairs: dict[str, str] = {}
    for raw in values or []:
        name, _, value = raw.partition("=")
        name, value = name.strip(), value.strip()
        if not value:
            fail(f"{option} takes AGENT=VALUE, not {raw!r}.")
        if name not in agents:
            fail(f"{option} names {name!r}, which is not one of the selected --agent values.")
        if allowed and value not in allowed:
            fail(f"{option} for {name} must be one of {', '.join(allowed)}, not {value!r}.")
        pairs[name] = value
    return pairs


def probe_agents(names: Sequence[str], billing: dict[str, str]) -> dict[str, AgentInfo]:
    """Each selected agent's version and billing; stops (exit 2) when a command is missing."""

    found = {
        name: probe_agent(name, billing=_billing(billing.get(name))) for name in names
    }  # fmt: skip
    missing = [name for name, info in found.items() if not info.available]
    if missing:
        fail(
            f"Not on PATH: {', '.join(missing)}. Install the agent, or leave it out of --agent. "
            "A batch never silently drops a contender."
        )
    return found


def _billing(value: str | None) -> Billing | None:
    return value if value in BILLINGS else None  # type: ignore[return-value]
