"""Prompt caching: CrewAI marks the stable prefix, and the cache fields reach the usage report."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from engineering_team.contracts import Event
from engineering_team.pricing import build_table
from engineering_team.report.models_view import cache_rows
from engineering_team.runtime.bridge import _usage
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.usage import UsageTracker
from engineering_team.testing import ScriptedLLM, build_agent, build_task

MakeContext = Callable[..., RunContext]


def spy_on_messages(llm: ScriptedLLM) -> list[list[dict[str, Any]]]:
    """Every message list the model is sent, as the executor hands it over (flags included)."""

    seen: list[list[dict[str, Any]]] = []
    original = llm.call

    def call(messages: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append([dict(m) for m in messages])
        return original(messages, *args, **kwargs)

    object.__setattr__(llm, "call", call)
    return seen


def test_the_system_prompt_and_task_prompt_are_marked_as_cache_breakpoints(
    make_context: MakeContext,
) -> None:
    llm = ScriptedLLM(["done"])
    seen = spy_on_messages(llm)
    agent = build_agent(make_context(), llm)

    agent.execute_task(build_task(agent, "Do the work."))

    marked = [m["role"] for m in seen[0] if m.get("cache_breakpoint")]
    # The system prompt (role, tools) and the task prompt: Anthropic caches up to the last of them,
    # OpenAI and Gemini cache any identical prefix by themselves.
    assert marked == ["system", "user"]


def test_what_comes_before_the_task_is_the_same_for_every_task_of_a_teammate(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    first, second = ScriptedLLM(["done"]), ScriptedLLM(["done"])

    for llm, request in ((first, "Build the habit tracker."), (second, "Fix the login bug.")):
        agent = build_agent(ctx, llm, role="Backend Engineer")
        agent.execute_task(build_task(agent, request))

    def system(llm: ScriptedLLM) -> str:
        return str(llm.calls[0].messages[0]["content"])

    assert llm.calls[0].messages[0]["role"] == "system"
    assert system(first) == system(second)  # a stable, cacheable prefix
    assert "Build the habit tracker." not in system(first)  # the request comes after it


def test_cache_reads_and_writes_reach_the_usage_counts() -> None:
    # Anthropic reports both, folded into the prompt by CrewAI; OpenAI and OpenRouter report reads.
    anthropic = _usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "total_tokens": 1050,
            "cached_prompt_tokens": 600,
            "cache_creation_tokens": 300,
        }
    )
    openrouter = _usage(
        {
            "prompt_tokens": 800,
            "completion_tokens": 20,
            "total_tokens": 820,
            "cached_prompt_tokens": 512,
            "cost": 0.0004,
        }
    )

    assert anthropic["cached_prompt_tokens"] == 600 and anthropic["cache_creation_tokens"] == 300
    assert openrouter["cached_prompt_tokens"] == 512 and openrouter["cost_usd"] == 0.0004


def usage_events() -> list[tuple[str, dict[str, Any]]]:
    model = "anthropic/claude-haiku-5-5"
    cold = {"prompt_tokens": 1000, "completion_tokens": 100, "cache_creation_tokens": 800}
    warm = {"prompt_tokens": 1000, "completion_tokens": 100, "cached_prompt_tokens": 800}
    return [
        ("llm.call", {"agent": "a", "model": model, "usage": cold}),
        ("llm.call", {"agent": "a", "model": model, "usage": warm}),
    ]


def test_the_report_counts_cache_hits_and_writes_and_prices_them_apart() -> None:
    tracker = UsageTracker()
    for kind, data in usage_events():
        tracker.emit(kind, **data)

    table = build_table()
    report = tracker.report(table)
    price = table.lookup("anthropic/claude-haiku-5-5")
    assert price is not None and price.cached_input is not None

    assert report.totals.cached_prompt_tokens == 800
    assert report.totals.cache_creation_tokens == 800
    assert cache_rows(report) == [("Cache hits", "40% of prompt tokens"),
                                  ("Cache writes", "800 tokens")]  # fmt: skip
    plain = 2 * (1000 * price.input + 100 * price.output) / 1_000_000
    cached = report.estimated_cost_usd
    assert cached is not None
    assert cached != plain  # cache reads and writes are not billed as plain input


def test_a_report_rebuilt_from_events_keeps_the_cache_fields() -> None:
    events = [
        Event.model_validate(
            {
                "seq": i,
                "ts": "2026-11-02T10:00:00Z",
                "run_id": "r",
                "type": kind,
                "agent": data["agent"],
                "data": {key: value for key, value in data.items() if key != "agent"},
            }
        )
        for i, (kind, data) in enumerate(usage_events(), start=1)
    ]

    report = UsageTracker.from_events(events).report(build_table())

    assert report.totals.cached_prompt_tokens == 800


def test_no_prompt_tokens_no_cache_rows() -> None:
    assert cache_rows(UsageTracker().report(build_table())) == []
