"""Fallback models: which errors move a call on, the cooldown, the event, and the report."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from crewai import Crew, Process
from report_helpers import write_manifest

from engineering_team.contracts import Event
from engineering_team.llm import build_llm, cap_output
from engineering_team.llm_fallback import attach_fallbacks, failure_kind
from engineering_team.report import write_run_report
from engineering_team.report.collect import build_report
from engineering_team.report.render_md import render_markdown
from engineering_team.runtime.bridge import bind_run, flush_bridge
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.settings import load_settings
from engineering_team.testing import ScriptedLLM, Turn, build_agent, build_task

MakeContext = Callable[..., RunContext]


class RateLimitError(Exception):
    status_code = 429


class ApiError(Exception):
    def __init__(self, status_code: int, message: str = "boom") -> None:
        super().__init__(message)
        self.status_code = status_code


def raises(error: Exception) -> Callable[[Any, Any], Any]:
    def script(messages: Any, tools: Any) -> Any:
        raise error

    return script


def throttled() -> list[Any]:
    """What a rate-limited model does: CrewAI retries a throttled call twice before giving up."""

    return [raises(RateLimitError("429 Too Many Requests"))] * 3


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("crewai.llms.retry.get_retry_delay_seconds", lambda *a, **k: 0.0)


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, type: str, **data: Any) -> None:
        self.events.append((type, data))


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (RateLimitError("slow down"), "rate_limit"),
        (ApiError(429), "rate_limit"),
        (RuntimeError("Rate limit reached for gpt"), "rate_limit"),
        (ApiError(503), "provider_error"),
        (ApiError(529, "overloaded"), "provider_error"),
        (ApiError(500), "provider_error"),
        (ApiError(504), "timeout"),
        (TimeoutError("read timed out"), "timeout"),
        (ConnectionError("connection reset"), "provider_error"),
        (RuntimeError("The service is temporarily unavailable"), "provider_error"),
    ],
)
def test_errors_a_second_model_may_cure_move_the_call_on(error: Exception, kind: str) -> None:
    assert failure_kind(error) == kind


@pytest.mark.parametrize(
    "error",
    [
        ApiError(400, "context length exceeded, rate limit mentioned"),
        ApiError(401, "invalid api key"),
        ApiError(403),
        ApiError(404, "model not found"),
        ValueError("bad messages"),
        RuntimeError("The run was stopped: max_cost_usd 1.0 exceeded."),
    ],
)
def test_errors_of_the_request_or_the_run_are_raised_as_they_are(error: Exception) -> None:
    assert failure_kind(error) is None


def test_the_cause_chain_is_read() -> None:
    try:
        try:
            raise RateLimitError("429")
        except RateLimitError as inner:
            raise RuntimeError("LLM call failed") from inner
    except RuntimeError as outer:
        assert failure_kind(outer) == "rate_limit"


def pair(primary: list[Any], fallback: list[Any]) -> tuple[ScriptedLLM, ScriptedLLM]:
    return (
        ScriptedLLM(primary, model="scripted/primary"),
        ScriptedLLM(fallback, model="scripted/fallback"),
    )


def test_a_rate_limited_call_is_answered_by_the_fallback_and_recorded() -> None:
    primary, fallback = pair(throttled(), ["from the fallback"])
    notes = Recorder()
    attach_fallbacks(
        primary, [("scripted/fallback", lambda: fallback)], primary="scripted/primary", notify=notes
    )

    assert primary.call([{"role": "user", "content": "hi"}]) == "from the fallback"

    ((kind, data),) = notes.events
    assert kind == "model.fallback"
    assert (data["from_model"], data["to_model"], data["reason"]) == (
        "scripted/primary",
        "scripted/fallback",
        "rate_limit",
    )
    assert "RateLimitError" in data["error"]


def test_a_healthy_primary_never_touches_the_fallback() -> None:
    primary, fallback = pair(["fine"], [])
    built: list[bool] = []

    def build() -> ScriptedLLM:
        built.append(True)
        return fallback

    attach_fallbacks(primary, [("scripted/fallback", build)], primary="p", notify=Recorder())

    assert primary.call("hi") == "fine"
    assert built == []  # a fallback is only built when needed


def test_an_error_a_second_model_cannot_cure_is_raised_and_nothing_falls_back() -> None:
    primary, fallback = pair([raises(ApiError(401, "invalid api key"))], ["unused"])
    notes = Recorder()
    attach_fallbacks(primary, [("scripted/fallback", lambda: fallback)], primary="p", notify=notes)

    with pytest.raises(ApiError):
        primary.call("hi")

    assert notes.events == [] and fallback.remaining == 1


def test_the_chain_is_tried_in_order_and_the_last_error_is_raised_when_all_fail() -> None:
    primary = ScriptedLLM([raises(ApiError(503, "first"))], model="scripted/primary")
    second = ScriptedLLM([raises(ApiError(503, "second"))], model="scripted/second")
    third = ScriptedLLM(["third answers"], model="scripted/third")
    notes = Recorder()
    attach_fallbacks(
        primary,
        [("scripted/second", lambda: second), ("scripted/third", lambda: third)],
        primary="scripted/primary",
        notify=notes,
    )

    assert primary.call("hi") == "third answers"
    assert [data["to_model"] for _, data in notes.events] == ["scripted/third"]

    dead = ScriptedLLM([raises(ApiError(503, "p"))], model="scripted/primary")
    only = ScriptedLLM([raises(ApiError(503, "only fallback"))], model="scripted/only")
    attach_fallbacks(dead, [("scripted/only", lambda: only)], primary="p", notify=Recorder())
    with pytest.raises(ApiError, match="only fallback"):
        dead.call("hi")


def test_a_failed_primary_is_left_alone_for_the_cooldown_then_tried_again() -> None:
    now = [1000.0]
    primary, fallback = pair([*throttled(), "primary is back"], ["one", "two", "three"])
    notes = Recorder()
    attach_fallbacks(
        primary,
        [("scripted/fallback", lambda: fallback)],
        primary="scripted/primary",
        cooldown_seconds=60,
        notify=notes,
        clock=lambda: now[0],
    )

    assert primary.call("a") == "one"
    now[0] += 30
    assert primary.call("b") == "two"  # still cooling: the primary is not asked again
    assert [data["reason"] for _, data in notes.events] == ["rate_limit", "cooldown"]
    assert primary.remaining == 1
    now[0] += 31
    assert primary.call("c") == "primary is back"
    assert fallback.remaining == 1


def test_a_cooling_primary_is_the_last_resort_when_the_fallbacks_fail_too() -> None:
    now = [0.0]
    primary, fallback = pair(
        [*throttled(), "primary again"], [raises(ApiError(503)), raises(ApiError(503))]
    )
    attach_fallbacks(
        primary,
        [("scripted/fallback", lambda: fallback)],
        primary="p",
        notify=Recorder(),
        clock=lambda: now[0],
    )
    with pytest.raises(ApiError):
        primary.call("a")  # primary 429, fallback 503
    assert primary.call("b") == "primary again"  # cooling: fallback is down, primary answers


def test_a_fallback_that_cannot_be_built_is_skipped() -> None:
    primary, fallback = pair(throttled(), ["second fallback"])

    def missing_key() -> ScriptedLLM:
        raise ValueError("OPENAI_API_KEY is required")

    attach_fallbacks(
        primary,
        [("scripted/broken", missing_key), ("scripted/fallback", lambda: fallback)],
        primary="p",
        notify=Recorder(),
    )

    assert primary.call("hi") == "second fallback"


def test_acall_falls_back_too() -> None:
    class Async:
        def __init__(self, reply: str | None) -> None:
            self.reply = reply

        def call(self, *a: Any, **k: Any) -> Any: ...

        async def acall(self, *a: Any, **k: Any) -> Any:
            if self.reply is None:
                raise RateLimitError("429")
            return self.reply

    primary, fallback = Async(None), Async("async fallback")
    notes = Recorder()
    attach_fallbacks(primary, [("f", lambda: fallback)], primary="p", notify=notes)

    assert asyncio.run(primary.acall("hi")) == "async fallback"  # type: ignore[arg-type]
    assert len(notes.events) == 1


def test_the_output_cap_reaches_a_fallback_built_later() -> None:
    primary, fallback = pair(throttled(), ["ok"])
    fallback.max_tokens = None
    primary.max_tokens = None
    attach_fallbacks(primary, [("f", lambda: fallback)], primary="p", notify=Recorder())

    cap_output(primary, 123)
    primary.call("hi")

    assert primary.max_tokens == 123 and fallback.max_tokens == 123


def test_build_llm_attaches_the_tiers_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[str] = []

    def construct(resolved: Any, settings: Any) -> ScriptedLLM:
        built.append(resolved.model)
        return ScriptedLLM(["ok"], model=resolved.model)

    monkeypatch.setattr("engineering_team.llm._construct", construct)
    settings = load_settings(
        overrides={
            "models": {"fallbacks": {"worker": ["anthropic"]}, "fallback_cooldown_seconds": 5}
        }
    )

    llm = build_llm(settings.resolve_model("backend_engineer", tier="worker"), settings)
    plain = build_llm(settings.resolve_model("x", tier="max"), settings)

    assert built == ["openai/gpt-6-luna", "openai/gpt-6.1-sol"]  # fallbacks wait to be needed
    assert llm._fallback_chain.targets[0].model == "anthropic/claude-haiku-5-5"  # type: ignore[attr-defined]
    assert llm._fallback_chain.cooldown_seconds == 5  # type: ignore[attr-defined]
    assert not hasattr(plain, "_fallback_chain")


# -- through a real agent, the run's event log and its report ---------------------------------


def run_crew(ctx: RunContext, llm: ScriptedLLM) -> None:
    agent = build_agent(ctx, llm, role="Backend Engineer")
    Crew(
        agents=[agent],
        tasks=[build_task(agent, "Work as Backend Engineer.")],
        process=Process.sequential,
        verbose=False,
        tracing=False,
        memory=False,
        cache=False,
    ).kickoff()


def events_of(ctx: RunContext) -> list[Event]:
    return list(read_events(ctx.run_dir / "events.jsonl"))


def test_a_scripted_rate_limit_falls_back_and_the_report_shows_it(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    primary, fallback = pair(throttled(), [Turn("done on the fallback", 200, 20)])
    attach_fallbacks(primary, [("scripted/fallback", lambda: fallback)], primary="scripted/primary")

    with bind_run(ctx.run_id, ctx.events):
        run_crew(ctx, primary)
    flush_bridge()

    events = events_of(ctx)
    (moved,) = [e for e in events if e.type == "model.fallback"]
    assert moved.agent == "Backend Engineer"
    assert moved.data["from_model"] == "scripted/primary"
    assert moved.data["to_model"] == "scripted/fallback" and moved.data["reason"] == "rate_limit"
    answered = [e for e in events if e.type == "llm.call"]
    assert [e.data["model"] for e in answered] == ["scripted/fallback"]

    write_manifest(ctx.run_dir)
    report = build_report(ctx.run_dir)
    (use,) = report.models.fallbacks
    assert (use.from_model, use.to_model, use.reason, use.calls) == (
        "scripted/primary",
        "scripted/fallback",
        "rate_limit",
        1,
    )
    text = render_markdown(report)
    assert "Fallback models stood in for a failing model" in text
    assert "scripted/primary" in text and "rate limit" in text
    written = write_run_report(ctx.run_dir)
    assert written is not None
    html = written.read_text("utf-8")
    assert "Fallback models" in html and "scripted/fallback" in html
