"""``doctor --probe-tools``: reading a model's reply and reporting per model (offline: the live
request is injected or scripted)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from engineering_team import llm as llm_module
from engineering_team.cli import doctor_probe
from engineering_team.cli.app import app
from engineering_team.cli.doctor import run_checks
from engineering_team.cli.doctor_probe import (
    PROBE_TOOL,
    ToolProbe,
    classify,
    ping_tools,
    probe_checks,
)
from engineering_team.settings import load_settings
from engineering_team.testing import ScriptedLLM, ToolCall

OPENAI_CALL = {"id": "c1", "type": "function", "function": {"name": PROBE_TOOL, "arguments": "{}"}}


@pytest.mark.parametrize(
    "response",
    [
        [OPENAI_CALL],  # OpenAI, Ollama, vLLM: a list of function calls
        [SimpleNamespace(id="c1", function=SimpleNamespace(name=PROBE_TOOL, arguments="{}"))],
        [SimpleNamespace(type="tool_use", id="t1", name=PROBE_TOOL, input={"word": "ping"})],
        [{"name": PROBE_TOOL, "input": {"word": "ping"}, "toolUseId": "t1"}],  # Bedrock
    ],
)
def test_a_tool_call_in_any_providers_format_counts_as_calling_tools(response: object) -> None:
    probe = classify(response)

    assert probe.called and probe.detail == f"called {PROBE_TOOL}"


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (
            "Sure! I will call the tool probe_echo now.",
            "Sure! I will call the tool probe_echo now.",
        ),
        ("  Hello\n\nthere  ", "Hello there"),
        ("", "(empty answer)"),
        (None, "(empty answer)"),
        ([], "(empty answer)"),
        ("x" * 200, "x" * 60),
    ],
)
def test_text_is_an_answer_in_text_only(response: object, detail: str) -> None:
    probe = classify(response)

    assert not probe.called and probe.detail == detail


def test_the_probe_reads_what_a_scripted_model_does_with_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = {
        "capable": ScriptedLLM([ToolCall(PROBE_TOOL, {"word": "ping"})]),
        "texty": ScriptedLLM(["I would use the echo tool."]),
    }
    settings = load_settings()
    resolved = settings.resolved_models()[0]
    chosen = iter(models.values())
    monkeypatch.setattr(llm_module, "build_llm", lambda *_: next(chosen))

    called = ping_tools(resolved, settings)
    text_only = ping_tools(resolved, settings)

    assert called == ToolProbe(True, f"called {PROBE_TOOL}")
    assert text_only == ToolProbe(False, "I would use the echo tool.")
    request = models["capable"].calls[0]
    assert request.tools is not None and request.tools[0]["function"]["name"] == PROBE_TOOL


def test_one_check_per_distinct_model_says_can_call_tools_or_answers_in_text_only() -> None:
    settings = load_settings()
    asked: list[str] = []

    def ping(resolved, _settings) -> ToolProbe:  # noqa: ANN001
        asked.append(resolved.model)
        if "luna" in resolved.model:
            return ToolProbe(False, "OK")
        if "sol" in resolved.model:
            raise RuntimeError("401 invalid key")
        return ToolProbe(True, f"called {PROBE_TOOL}")

    checks = probe_checks(settings, ping)

    assert len(asked) == len(set(asked)) == len(checks) >= 2
    by_status = {c.status for c in checks}
    assert by_status <= {"ok", "fail"}
    text_only = next(c for c in checks if "luna" in c.name)
    assert text_only.status == "fail" and "answers in text only" in text_only.detail
    assert "tool calling" in text_only.hint
    broken = next(c for c in checks if "sol" in c.name)
    assert broken.status == "fail" and "401 invalid key" in broken.detail


def test_doctor_only_probes_when_asked() -> None:
    def ping(resolved, _settings) -> ToolProbe:  # noqa: ANN001
        raise AssertionError("must not be called")

    kwargs = {"which": lambda _: None, "probe": lambda _: "1.0", "tool_ping": ping}
    checks = run_checks(load_settings(), **kwargs)  # type: ignore[arg-type]
    assert not [c for c in checks if c.name.startswith("Tools ")]


def test_run_checks_adds_the_tool_checks_after_the_others() -> None:
    checks = run_checks(
        load_settings(),
        which=lambda _: None,
        probe=lambda _: "1.0",
        probe_tools=True,
        tool_ping=lambda *_: ToolProbe(True, "called probe_echo"),
    )

    names = [c.name for c in checks]
    assert names[-1].startswith("Tools ") and all(c.status == "ok" for c in checks[-2:])


def test_the_command_has_the_flag_and_reports_in_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor_probe, "ping_tools", lambda *_: ToolProbe(False, "I cannot call tools.")
    )

    result = CliRunner().invoke(app, ["--json", "doctor", "--probe-tools"])

    data = json.loads(result.stdout)
    probes = [c for c in data["checks"] if c["name"].startswith("Tools ")]
    assert probes and all("answers in text only" in c["detail"] for c in probes)
    assert result.exit_code == 1 and data["ok"] is False
