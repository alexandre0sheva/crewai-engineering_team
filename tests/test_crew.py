from __future__ import annotations

from collections.abc import Callable

from crewai import Process

from engineering_team.crew import EngineeringTeam
from engineering_team.runtime.context import RunContext
from engineering_team.settings import load_settings

MakeContext = Callable[..., RunContext]


def test_hierarchical_crew_constructs_without_calling_an_llm(
    make_context: MakeContext,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENGINEERING_VERBOSE", "false")
    monkeypatch.setenv("ENGINEERING_TRACING", "false")
    ctx = make_context("generated-project", settings=load_settings())

    built_crew = EngineeringTeam(ctx).crew()

    assert built_crew.process is Process.hierarchical
    assert len(built_crew.agents) == 4
    assert len(built_crew.tasks) == 6
    assert built_crew.manager_agent is not None
    assert "Principal Engineering Lead" in built_crew.manager_agent.role
    assert built_crew.manager_agent.tools == []
    assert all(agent.tools for agent in built_crew.agents)
    assert built_crew.output_log_file == str(ctx.run_dir / "crew-log.json")

    # Exercise the same initialization path used by kickoff. CrewAI rejects a
    # custom hierarchical manager here if it was constructed with any tools.
    built_crew._create_manager_agent()
    assert built_crew.manager_agent.allow_delegation is True


def test_smoke_profile_uses_lower_cost_model_tiers(make_context: MakeContext) -> None:
    settings = load_settings(
        env={
            "ENGINEERING_RUN_PROFILE": "smoke",
            # Standard-profile variables must never leak into smoke mode.
            "ENGINEERING_LEAD_MODEL": "openai/gpt-4.1",
            "ENGINEERING_WORKER_MODEL": "openai/gpt-6.1-sol",
            "ENGINEERING_LEAD_REASONING_EFFORT": "high",
            "ENGINEERING_WORKER_REASONING_EFFORT": "high",
            "ENGINEERING_VERBOSE": "false",
        }
    )

    built_crew = EngineeringTeam(make_context("smoke-project", settings=settings)).crew()

    assert built_crew.manager_agent is not None
    assert built_crew.manager_agent.llm.model == "gpt-6-luna"
    assert built_crew.manager_agent.llm.reasoning_effort == "low"
    assert built_crew.manager_agent.max_iter == 18
    assert {agent.llm.model for agent in built_crew.agents} == {"gpt-6-luna"}
    assert {agent.llm.reasoning_effort for agent in built_crew.agents} == {"none"}
    assert {agent.max_iter for agent in built_crew.agents} == {14}


def test_settings_drive_models_iteration_caps_and_per_role_overrides(
    make_context: MakeContext,
) -> None:
    settings = load_settings(
        env={
            "ENGINEERING_LEAD_MODEL": "openai/gpt-4.1",
            "ENGINEERING_WORKER_MAX_ITER": "7",
            "ENGINEERING_VERBOSE": "false",
        },
        overrides={"models.roles.quality_engineer.model": "openai/gpt-6.1-sol"},
    )

    built_crew = EngineeringTeam(make_context(settings=settings)).crew()

    assert built_crew.manager_agent.llm.model == "gpt-4.1"
    by_role = {agent.role.split(" for ")[0]: agent for agent in built_crew.agents}
    quality = next(a for r, a in by_role.items() if "Quality" in r)
    backend = next(a for r, a in by_role.items() if "Backend" in r)
    assert quality.llm.model == "gpt-6.1-sol"
    assert backend.llm.model == "gpt-6-luna"
    assert {agent.max_iter for agent in built_crew.agents} == {7}


def test_verbose_and_tracing_come_from_settings(make_context: MakeContext) -> None:
    settings = load_settings(env={"ENGINEERING_VERBOSE": "false", "ENGINEERING_TRACING": "true"})

    built_crew = EngineeringTeam(make_context(settings=settings)).crew()

    assert built_crew.verbose is False
    assert built_crew.tracing is True


def test_docs_mcp_urls_reach_specialists_only_outside_smoke_mode(
    make_context: MakeContext,
) -> None:
    base = {"ENGINEERING_DOCS_MCP_URLS": "https://docs.example/mcp", "ENGINEERING_VERBOSE": "false"}

    standard = EngineeringTeam(make_context("standard", settings=load_settings(env=base))).crew()
    smoke_settings = load_settings(env={**base, "ENGINEERING_RUN_PROFILE": "smoke"})
    smoke = EngineeringTeam(make_context("smoke", settings=smoke_settings)).crew()

    assert all(agent.mcps == ["https://docs.example/mcp"] for agent in standard.agents)
    assert all(not agent.mcps for agent in smoke.agents)


class _LLMRecorder:
    calls: list[dict]

    def __init__(self) -> None:
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


def test_build_llm_sends_only_parameters_the_provider_accepts(monkeypatch) -> None:
    from engineering_team import crew as crew_module
    from engineering_team.crew import build_llm

    recorder = _LLMRecorder()
    monkeypatch.setattr(crew_module, "LLM", recorder)

    openai_settings = load_settings()
    build_llm(openai_settings.resolve_model("engineering_lead"), openai_settings)
    anthropic_settings = load_settings(overrides={"provider": "anthropic"})
    build_llm(anthropic_settings.resolve_model("engineering_lead"), anthropic_settings)
    ollama_settings = load_settings(overrides={"provider": "ollama"})
    build_llm(ollama_settings.resolve_model("backend_engineer"), ollama_settings)

    openai_call, anthropic_call, ollama_call = recorder.calls
    # GPT-6 needs the Responses API for tools and an explicit context window.
    assert openai_call == {
        "model": "openai/gpt-6.1-sol",
        "reasoning_effort": "high",
        "api": "responses",
        "context_window_size": int(1_050_000 * 0.75),
    }
    # Anthropic ignores reasoning effort, so it must not be sent.
    assert anthropic_call == {"model": "anthropic/claude-sonnet-5-5"}
    assert ollama_call["base_url"] == "http://localhost:11434"
    assert ollama_call["context_window_size"] == int(32_768 * 0.75)


def test_artifact_guardrail_rejects_missing_and_nearly_empty_files(
    make_context: MakeContext,
) -> None:
    from types import SimpleNamespace

    from engineering_team.crew import _require_workspace_files

    workspace = make_context().workspace
    workspace.write_file("docs/empty.md", "  \n")
    workspace.write_file("docs/stub.md", "# Title\n")
    workspace.write_file("docs/real.md", "# Architecture\n" + "A concrete design sentence. " * 5)
    output = SimpleNamespace(raw="done")

    ok, message = _require_workspace_files(workspace, "docs/real.md")(output)
    assert ok is True and message == "done"

    ok, message = _require_workspace_files(
        workspace, "docs/missing.md", "docs/empty.md", "docs/stub.md"
    )(output)
    assert ok is False
    assert "docs/missing.md (missing)" in message
    assert "docs/empty.md (nearly empty)" in message
    assert "docs/stub.md (nearly empty)" in message


def test_each_specialist_gets_its_own_tool_instances_bound_to_the_run(
    make_context: MakeContext,
) -> None:
    first_ctx, second_ctx = make_context("first"), make_context("second")

    first = EngineeringTeam(first_ctx).crew()
    second = EngineeringTeam(second_ctx).crew()

    tools = [tool for agent in (*first.agents, *second.agents) for tool in agent.tools]
    assert len({id(tool) for tool in tools}) == len(tools)
    writer = next(tool for tool in first.agents[0].tools if tool.name == "Write Project File")
    writer.run(path="only-first.txt", content="x")
    assert (first_ctx.workspace.root / "only-first.txt").is_file()
    assert not (second_ctx.workspace.root / "only-first.txt").exists()
