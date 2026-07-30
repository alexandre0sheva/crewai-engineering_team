from __future__ import annotations

from pathlib import Path

from crewai import Process

from engineering_team.crew import EngineeringTeam
from engineering_team.tools import configure_workspace


def test_hierarchical_crew_constructs_without_calling_an_llm(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENGINEERING_VERBOSE", "false")
    monkeypatch.setenv("ENGINEERING_TRACING", "false")
    configure_workspace(tmp_path / "generated-project")

    built_crew = EngineeringTeam().crew()

    assert built_crew.process is Process.hierarchical
    assert len(built_crew.agents) == 4
    assert len(built_crew.tasks) == 6
    assert built_crew.manager_agent is not None
    assert "Principal Engineering Lead" in built_crew.manager_agent.role
    assert built_crew.manager_agent.tools == []
    assert all(agent.tools for agent in built_crew.agents)

    # Exercise the same initialization path used by kickoff. CrewAI rejects a
    # custom hierarchical manager here if it was constructed with any tools.
    built_crew._create_manager_agent()
    assert built_crew.manager_agent.allow_delegation is True


def test_smoke_profile_uses_lower_cost_model_tiers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENGINEERING_RUN_PROFILE", "smoke")
    monkeypatch.setenv("ENGINEERING_LEAD_MODEL", "openai/gpt-5.6-sol")
    monkeypatch.setenv("ENGINEERING_WORKER_MODEL", "openai/gpt-5.6-terra")
    monkeypatch.setenv("ENGINEERING_LEAD_REASONING_EFFORT", "high")
    monkeypatch.setenv("ENGINEERING_WORKER_REASONING_EFFORT", "low")
    for name in (
        "ENGINEERING_SMOKE_LEAD_MODEL",
        "ENGINEERING_SMOKE_WORKER_MODEL",
        "ENGINEERING_SMOKE_LEAD_REASONING_EFFORT",
        "ENGINEERING_SMOKE_WORKER_REASONING_EFFORT",
        "ENGINEERING_SMOKE_LEAD_MAX_ITER",
        "ENGINEERING_SMOKE_WORKER_MAX_ITER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENGINEERING_VERBOSE", "false")
    configure_workspace(tmp_path / "smoke-project")

    built_crew = EngineeringTeam().crew()

    assert built_crew.manager_agent is not None
    assert built_crew.manager_agent.llm.model == "gpt-5.6-terra"
    assert built_crew.manager_agent.llm.reasoning_effort == "low"
    assert built_crew.manager_agent.max_iter == 18
    assert {agent.llm.model for agent in built_crew.agents} == {"gpt-5.6-luna"}
    assert {agent.llm.reasoning_effort for agent in built_crew.agents} == {"none"}
    assert {agent.max_iter for agent in built_crew.agents} == {14}
