"""External agent adapters: each runs a (fake) binary through ``ExecutionBackend`` and reads its
report. Never uses a real agent or a network."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from agent_fakes import install_fake_agents

from engineering_team.bench.agents import (
    AGENT_NAMES,
    Aider,
    ClaudeCode,
    Codex,
    ExternalAgent,
    OpenCode,
    build_agent,
    probe_agent,
)
from engineering_team.execution.local import LocalBackend
from engineering_team.pricing import Price, PriceTable

NAMES = {"claude-code": "claude", "codex": "codex", "opencode": "opencode", "aider": "aider"}


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    return install_fake_agents(tmp_path / "bin", tmp_path / "no-suite")


def environment(bin_dir: Path, tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "HOME": str(tmp_path)}
    env["FAKE_AGENT_LOG"] = str(tmp_path / "calls.jsonl")
    return {**env, **extra}


def make(name: str, bin_dir: Path, tmp_path: Path, **kwargs: object):  # noqa: ANN201
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    env = environment(bin_dir, tmp_path, **kwargs.pop("extra_env", {}))  # type: ignore[arg-type]
    agent = build_agent(name, LocalBackend(tmp_path / "logs"), env=env, **kwargs)  # type: ignore[arg-type]
    return agent, workspace


def calls(tmp_path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]


def test_every_adapter_satisfies_the_protocol(bin_dir: Path, tmp_path: Path) -> None:
    for name in AGENT_NAMES:
        agent, _ = make(name, bin_dir, tmp_path)
        assert isinstance(agent, ExternalAgent)
        assert agent.name == name and agent.available()


@pytest.mark.parametrize("name", AGENT_NAMES)
def test_an_agent_runs_in_the_workspace_and_reports_its_cost(
    name: str, bin_dir: Path, tmp_path: Path
) -> None:
    agent, workspace = make(name, bin_dir, tmp_path, model="m")

    run = agent.run(workspace, "Write a thing.", timeout=30)

    assert run.exit_code == 0 and not run.timed_out and not run.error
    assert run.wall_s >= 0
    assert run.transcript_path is not None and run.transcript_path.is_file()
    assert run.cost_usd is None or run.cost_usd > 0 or name == "codex"
    assert Path(str(calls(tmp_path)[0]["cwd"])).resolve() == workspace.resolve()
    assert agent.version() == f"{NAMES[name]} 9.9.9 (fake)"


def test_claude_code_reports_its_own_cost_tokens_and_model(bin_dir: Path, tmp_path: Path) -> None:
    agent, workspace = make("claude-code", bin_dir, tmp_path, model="sonnet", max_cost_usd=1.5)

    run = agent.run(workspace, "Do it.", timeout=30)

    assert run.cost_usd == pytest.approx(0.42)
    assert (run.report.prompt_tokens, run.report.completion_tokens) == (1000, 50)
    assert run.report.cached_prompt_tokens == 900 and run.report.turns == 3
    assert run.report.models == ("claude-sonnet-5-5",)
    argv = calls(tmp_path)[0]["argv"]
    assert argv[:2] == ["-p", "Do it."]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--max-budget-usd") + 1] == "1.5000"
    assert "--output-format" in argv and "json" in argv


def test_claude_code_without_a_cost_cap_or_model_passes_neither(
    bin_dir: Path, tmp_path: Path
) -> None:
    agent, workspace = make("claude-code", bin_dir, tmp_path)

    agent.run(workspace, "Do it.", timeout=30)

    argv = calls(tmp_path)[0]["argv"]
    assert "--model" not in argv and "--max-budget-usd" not in argv


def test_codex_cost_is_its_tokens_times_our_price_table(bin_dir: Path, tmp_path: Path) -> None:
    table = PriceTable([Price(model="openai/test-model", input=2.0, output=10.0, cached_input=0.5)])
    workspace = tmp_path / "ws"
    workspace.mkdir()
    agent = Codex(
        LocalBackend(tmp_path / "logs"), model="test-model", prices=table,
        env=environment(bin_dir, tmp_path),
    )  # fmt: skip

    run = agent.run(workspace, "Do it.", timeout=30)

    # 600 fresh + 400 cached prompt tokens, 200 completion tokens
    assert run.cost_usd == pytest.approx((600 * 2.0 + 400 * 0.5 + 200 * 10.0) / 1_000_000)
    argv = calls(tmp_path)[0]["argv"]
    assert argv[argv.index("-m") + 1] == "test-model"
    assert argv[-2:] == ["--", "Do it."]
    assert "danger-full-access" in argv


def test_codex_without_a_known_model_has_an_unknown_cost(bin_dir: Path, tmp_path: Path) -> None:
    agent, workspace = make("codex", bin_dir, tmp_path)

    run = agent.run(workspace, "Do it.", timeout=30)

    assert run.cost_usd is None
    assert run.report.prompt_tokens == 1000 and run.report.completion_tokens == 200


def test_opencode_sums_the_cost_of_its_steps(bin_dir: Path, tmp_path: Path) -> None:
    agent, workspace = make("opencode", bin_dir, tmp_path, model="anthropic/claude-sonnet-5-5")

    run = agent.run(workspace, "Do it.", timeout=30)

    assert run.cost_usd == pytest.approx(0.06) and run.report.turns == 2
    assert run.report.prompt_tokens == 220 and run.report.completion_tokens == 40
    argv = calls(tmp_path)[0]["argv"]
    assert argv[:2] == ["run", "--format"] and argv[-1] == "Do it."
    assert argv[argv.index("-m") + 1] == "anthropic/claude-sonnet-5-5"


def test_aider_cost_is_the_session_figure_it_prints(bin_dir: Path, tmp_path: Path) -> None:
    agent, workspace = make("aider", bin_dir, tmp_path, model="sonnet")

    run = agent.run(workspace, "Do it.", timeout=30)

    assert run.cost_usd == pytest.approx(0.02)
    assert (run.report.prompt_tokens, run.report.completion_tokens) == (1200, 300)
    argv = calls(tmp_path)[0]["argv"]
    assert argv[argv.index("--message") + 1] == "Do it." and "--yes-always" in argv


@pytest.mark.parametrize("name", AGENT_NAMES)
def test_a_failing_agent_keeps_its_exit_code(name: str, bin_dir: Path, tmp_path: Path) -> None:
    agent, workspace = make(name, bin_dir, tmp_path, extra_env={"FAKE_AGENT_MODE": "crash"})

    run = agent.run(workspace, "Do it.", timeout=30)

    assert run.exit_code == 3 and not run.timed_out and not run.error
    assert "the fake agent crashed" in (run.transcript_path or Path()).read_text()
    assert run.cost_usd is None  # it reported nothing: unknown, never zero


@pytest.mark.parametrize("name", AGENT_NAMES)
def test_an_agent_that_runs_too_long_is_stopped(name: str, bin_dir: Path, tmp_path: Path) -> None:
    agent, workspace = make(name, bin_dir, tmp_path, extra_env={"FAKE_AGENT_MODE": "hang"})

    run = agent.run(workspace, "Do it.", timeout=1)

    assert run.timed_out and run.wall_s < 30
    assert run.exit_code is not None and run.exit_code != 0


@pytest.mark.parametrize("name", AGENT_NAMES)
def test_a_missing_binary_is_reported_not_raised(name: str, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    agent = build_agent(name, LocalBackend(tmp_path / "logs"), env={"PATH": str(empty)})

    run = agent.run(tmp_path, "Do it.", timeout=5)

    assert not agent.available() and agent.version() is None
    assert run.exit_code is None and f"`{NAMES[name]}` is not on PATH" in run.error
    assert run.transcript_path is None


def test_an_executable_that_cannot_start_is_reported(tmp_path: Path) -> None:
    bad = tmp_path / "bin"
    bad.mkdir()
    (bad / "claude").write_text("#!/nonexistent/interpreter\n")
    (bad / "claude").chmod(0o755)
    agent = ClaudeCode(LocalBackend(tmp_path / "logs"), env={"PATH": str(bad)})

    run = agent.run(tmp_path, "Do it.", timeout=5)

    assert run.exit_code is None and "could not start" in run.error


def test_claude_code_with_an_error_result_reports_the_reason(tmp_path: Path) -> None:
    text = json.dumps(
        {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "ran out",
         "total_cost_usd": 0.1}
    )  # fmt: skip

    report = ClaudeCode(LocalBackend(tmp_path)).parse("noise\n" + text)

    assert report.error.startswith("error_max_turns") and report.cost_usd == 0.1


def test_output_with_no_report_gives_an_empty_one(tmp_path: Path) -> None:
    for cls in (ClaudeCode, Codex, OpenCode, Aider):
        report = cls(LocalBackend(tmp_path)).parse("just words\n{not json")
        assert report.cost_usd is None and report.prompt_tokens == 0 and not report.error


@pytest.mark.parametrize(
    ("name", "env", "expected"),
    [
        ("claude-code", {}, "subscription"),
        ("claude-code", {"ANTHROPIC_API_KEY": "k"}, "api"),
        ("claude-code", {"CLAUDE_CODE_USE_BEDROCK": "1"}, "api"),
        ("codex", {}, "subscription"),
        ("codex", {"OPENAI_API_KEY": "k"}, "api"),
        ("opencode", {}, "unknown"),
        ("aider", {}, "api"),
    ],
)
def test_billing_follows_the_credentials_in_the_environment(
    name: str, env: dict[str, str], expected: str, tmp_path: Path
) -> None:
    agent = build_agent(name, LocalBackend(tmp_path), env=env)

    assert agent.billing() == expected


def test_the_billing_can_be_overridden(tmp_path: Path) -> None:
    agent = build_agent("claude-code", LocalBackend(tmp_path), env={}, billing="api")

    assert agent.billing() == "api"


def test_probe_reports_version_billing_and_availability(bin_dir: Path, tmp_path: Path) -> None:
    env = environment(bin_dir, tmp_path)

    info = probe_agent("claude-code", env=env)

    assert (info.available, info.version, info.billing) == (
        True,
        "claude 9.9.9 (fake)",
        "subscription",
    )
    assert info.caps_cost
    assert not probe_agent("aider", env={"PATH": str(tmp_path)}).available


def test_an_unknown_agent_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown agent 'gemini'"):
        build_agent("gemini", LocalBackend(tmp_path))


def test_the_agent_names_are_the_documented_ones() -> None:
    assert AGENT_NAMES == ("claude-code", "codex", "opencode", "aider")
