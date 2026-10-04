"""Hooks: commands and webhooks at stage and run boundaries, bounded and unable to fail a run."""

from __future__ import annotations

import http.server
import json
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pipeline_fakes import FakeRunner
from test_pipeline_flow import only_run, run_dir, start, use_runner

from engineering_team.contracts import RunSummary
from engineering_team.extensions.hooks import HookRunner, post_json
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.settings import Settings, SettingsError, load_settings

MakeContext = Callable[..., RunContext]
WEBHOOK = "https://hooks.example.com/services/T000/B000/SECRETPART"


def settings_with(**hooks: list[dict[str, Any]]) -> Settings:
    return load_settings(overrides={f"hooks.{event}": items for event, items in hooks.items()})


class Recorder:
    """A webhook poster that records what it was sent and answers a scripted status."""

    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self.sent: list[tuple[str, dict[str, Any], float]] = []
        self.status, self.error = status, error

    def __call__(self, url: str, payload: Any, timeout: float) -> int:
        self.sent.append((url, dict(payload), timeout))
        if self.error:
            raise self.error
        return self.status


def hook_events(ctx: RunContext) -> list[dict[str, Any]]:
    return [e.data for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "hook.ran"]


def runner_for(make_context: MakeContext, poster: Recorder, **hooks: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
    ctx = make_context(settings=settings_with(**hooks))
    return ctx, HookRunner(ctx, poster=poster)


def summary() -> RunSummary:
    return RunSummary(status="succeeded", duration_seconds=12.5, estimated_cost_usd=0.42)  # type: ignore[call-arg]


# -- the schema -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event", "hook", "message"),
    [
        ("after_stage", {}, "exactly one of 'command'"),
        ("after_stage", {"command": ["x"], "url": WEBHOOK}, "exactly one of 'command'"),
        ("after_stage", {"command": []}, "must start with the program"),
        ("after_stage", {"url": "ftp://x"}, "http\\(s\\) address"),
        ("after_stage", {"url": WEBHOOK, "timeout_seconds": 0}, "greater than or equal to 1"),
        ("after_stage", {"url": WEBHOOK, "timeout_seconds": 500}, "less than or equal to 120"),
        ("after_stage", {"url": WEBHOOK, "statuses": ["exploded"]}, "statuses"),
        ("after_stage", {"url": WEBHOOK, "stages": ["Not A Stage"]}, "stage name"),
        ("before_stage", {"url": WEBHOOK, "statuses": ["failed"]}, "drop 'statuses'"),
        ("on_finish", {"url": WEBHOOK, "stages": ["verify"]}, "drop 'stages'"),
        ("after_stage", {"url": WEBHOOK, "retries": 3}, "retries"),
    ],
)
def test_a_hook_must_be_one_clear_thing(event: str, hook: dict[str, Any], message: str) -> None:
    with pytest.raises(SettingsError, match=message):
        settings_with(**{event: [hook]})


def test_hooks_come_from_toml_array_tables(tmp_path: Path) -> None:
    config = tmp_path / "cfg.toml"
    config.write_text(
        f'[[hooks.on_finish]]\nurl = "{WEBHOOK}"\nstatuses = ["failed"]\n\n'
        '[[hooks.after_stage]]\ncommand = ["./notify.sh", "--quiet"]\ntimeout_seconds = 30\n',
        encoding="utf-8",
    )

    settings = load_settings(config_file=config)

    assert settings.hooks.any
    assert settings.hooks.on_finish[0].statuses == ["failed"]
    assert settings.hooks.after_stage[0].command == ["./notify.sh", "--quiet"]
    assert not load_settings().hooks.any


# -- webhooks -------------------------------------------------------------------------------------


def test_a_webhook_gets_a_slack_style_json_payload(make_context: MakeContext) -> None:
    post = Recorder()
    ctx, hooks = runner_for(make_context, post, after_stage=[{"url": WEBHOOK}])

    hooks.after_stage("verify", "failed", "2 checks failed")

    ((url, payload, timeout),) = post.sent
    assert url == WEBHOOK and timeout == 10.0
    assert payload["text"] == "[engineering-team] mvp-app: stage 'verify' failed: 2 checks failed"
    assert payload | {"text": ""} == {
        "text": "",
        "event": "after_stage",
        "run_id": ctx.run_id,
        "project": "mvp-app",
        "stage": "verify",
        "status": "failed",
        "error": "2 checks failed",
    }


def test_on_finish_carries_the_outcome_cost_and_duration(make_context: MakeContext) -> None:
    post = Recorder()
    ctx, hooks = runner_for(make_context, post, on_finish=[{"url": WEBHOOK}])

    hooks.on_finish("succeeded", None, summary())

    (_, payload, _) = post.sent[0]
    assert payload["event"] == "on_finish" and payload["status"] == "succeeded"
    assert payload["duration_seconds"] == 12.5 and payload["estimated_cost_usd"] == 0.42
    assert "stage" not in payload and "error" not in payload
    assert payload["text"].endswith(f"run {ctx.run_id} succeeded")


def test_stage_and_status_filters_pick_the_hooks_that_run(make_context: MakeContext) -> None:
    post = Recorder()
    _, hooks = runner_for(
        make_context,
        post,
        before_stage=[{"url": WEBHOOK, "stages": ["verify"]}],
        after_stage=[{"url": WEBHOOK, "stages": ["verify"], "statuses": ["failed"]}],
        on_finish=[{"url": WEBHOOK, "statuses": ["failed", "cancelled"]}],
    )

    hooks.before_stage("plan")
    hooks.after_stage("verify", "succeeded")
    hooks.after_stage("plan", "failed")
    hooks.on_finish("succeeded", None, None)
    assert post.sent == []

    hooks.before_stage("verify")
    hooks.after_stage("verify", "failed", "x")
    hooks.on_finish("cancelled", None, None)
    assert [p["event"] for _, p, _ in post.sent] == ["before_stage", "after_stage", "on_finish"]


def test_the_webhook_address_is_never_written_to_the_event_log(make_context: MakeContext) -> None:
    post = Recorder(error=OSError(f"connection to {WEBHOOK} refused"))
    ctx, hooks = runner_for(make_context, post, on_finish=[{"url": WEBHOOK}])

    hooks.on_finish("failed", "boom", None)

    log = (ctx.run_dir / "events.jsonl").read_text(encoding="utf-8")
    (event,) = hook_events(ctx)
    assert event["target"] == "hooks.example.com" and event["ok"] is False
    assert "SECRETPART" not in log and "<url>" in event["detail"]


def test_secret_values_are_scrubbed_from_payloads_and_details(
    make_context: MakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEPLOY_TOKEN", "tok-1234567890-abcdef")
    post = Recorder()
    ctx, hooks = runner_for(make_context, post, after_stage=[{"url": WEBHOOK}])

    hooks.after_stage("verify", "failed", "curl failed with tok-1234567890-abcdef")

    (_, payload, _) = post.sent[0]
    assert "tok-1234567890" not in json.dumps(payload) and "[REDACTED]" in payload["text"]


def test_a_failing_hook_is_an_event_and_nothing_else(make_context: MakeContext) -> None:
    post = Recorder(status=500)
    ctx, hooks = runner_for(
        make_context, post, before_stage=[{"url": WEBHOOK}, {"url": WEBHOOK + "2"}]
    )

    hooks.before_stage("plan")  # does not raise; the second hook still runs

    events = hook_events(ctx)
    assert [e["ok"] for e in events] == [False, False]
    assert "HTTP 500" in events[0]["detail"] and events[0]["hook"] == "before_stage"
    assert len(post.sent) == 2


def test_no_hooks_configured_costs_nothing(make_context: MakeContext) -> None:
    post = Recorder()
    ctx = make_context()
    hooks = HookRunner(ctx, poster=post)

    hooks.before_stage("plan")
    hooks.on_finish("succeeded", None, None)

    assert not hooks.active and post.sent == [] and hook_events(ctx) == []


# -- commands -------------------------------------------------------------------------------------


def python_hook(code: str, **extra: Any) -> dict[str, Any]:
    return {"command": [sys.executable, "-c", code], **extra}


def test_a_command_gets_the_facts_in_a_minimal_environment(make_context: MakeContext) -> None:
    code = (
        "import json, os, pathlib;"
        "pathlib.Path('hook-env.json').write_text(json.dumps(dict(os.environ)))"
    )
    ctx, hooks = runner_for(make_context, Recorder(), after_stage=[python_hook(code)])

    hooks.after_stage("verify", "failed", "x")

    env = json.loads((Path.cwd() / "hook-env.json").read_text(encoding="utf-8"))
    assert env["ENGINEERING_HOOK_EVENT"] == "after_stage"
    assert env["ENGINEERING_HOOK_STAGE"] == "verify" and env["ENGINEERING_HOOK_STATUS"] == "failed"
    assert env["ENGINEERING_HOOK_RUN_ID"] == ctx.run_id and "PATH" in env
    assert json.loads(env["ENGINEERING_HOOK_PAYLOAD"])["stage"] == "verify"
    assert "OPENAI_API_KEY" not in env  # provider keys are not handed to hooks
    assert [e["ok"] for e in hook_events(ctx)] == [True]


@pytest.mark.parametrize(
    ("hook", "detail"),
    [
        (python_hook("import sys; print('nope'); sys.exit(3)"), "exit code 3. nope"),
        ({"command": ["/nonexistent/program"]}, "FileNotFoundError"),
        (python_hook("import time; time.sleep(30)", timeout_seconds=1), "timed out after 1s"),
    ],
)
def test_a_command_that_fails_times_out_or_cannot_start_is_isolated(
    make_context: MakeContext, hook: dict[str, Any], detail: str
) -> None:
    ctx, hooks = runner_for(make_context, Recorder(), on_finish=[hook])

    hooks.on_finish("succeeded", None, None)

    (event,) = hook_events(ctx)
    assert event["ok"] is False and detail in event["detail"] and event["kind"] == "command"


def test_hooks_still_run_after_the_run_was_cancelled(make_context: MakeContext) -> None:
    ctx, hooks = runner_for(
        make_context, Recorder(), on_finish=[python_hook("print('still here')")]
    )
    ctx.cancel_event.set()

    hooks.on_finish("cancelled", None, None)

    (event,) = hook_events(ctx)
    assert event["ok"] is True and "still here" in event["detail"]


# -- the real webhook client ----------------------------------------------------------------------


@pytest.fixture
def server() -> Iterator[Callable[[int], tuple[str, list[dict[str, Any]]]]]:
    started: list[http.server.HTTPServer] = []

    def start_server(status: int) -> tuple[str, list[dict[str, Any]]]:
        received: list[dict[str, Any]] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append({"type": self.headers["Content-Type"], "json": json.loads(body)})
                self.send_response(status)
                if status == 302:
                    self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                return None

        httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        started.append(httpd)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{httpd.server_port}/hook", received

    yield start_server
    for httpd in started:
        httpd.shutdown()


def test_post_json_sends_json_and_returns_the_status(server: Any) -> None:
    url, received = server(200)

    assert post_json(url, {"text": "hello"}, 5) == 200
    assert received == [{"type": "application/json", "json": {"text": "hello"}}]


@pytest.mark.parametrize("status", [302, 404, 500])
def test_post_json_raises_for_redirects_and_errors(server: Any, status: int) -> None:
    url, _ = server(status)

    with pytest.raises(OSError, match=str(status)):
        post_json(url, {"text": "hello"}, 5)


def test_a_slack_style_webhook_works_with_configuration_alone(
    make_context: MakeContext, server: Any
) -> None:
    url, received = server(200)
    ctx = make_context(settings=settings_with(on_finish=[{"url": url}]))

    HookRunner(ctx).on_finish("succeeded", None, summary())

    assert received[0]["json"]["text"].endswith("succeeded")
    assert [e["ok"] for e in hook_events(ctx)] == [True]


# -- in a run -------------------------------------------------------------------------------------


def log_hook(name: str, **extra: Any) -> dict[str, Any]:
    code = (
        "import os;"
        "open('hooks.log', 'a').write("
        "os.environ['ENGINEERING_HOOK_EVENT'] + ' ' + "
        "os.environ.get('ENGINEERING_HOOK_STAGE', '-') + ' ' + "
        "os.environ.get('ENGINEERING_HOOK_STATUS', '-') + '\\n')"
    )
    return python_hook(code, **extra)


def configure(**hooks: list[dict[str, Any]]) -> None:
    toml = []
    for event, items in hooks.items():
        for item in items:
            toml.append(f"[[hooks.{event}]]")
            toml.extend(f"{key} = {json.dumps(value)}" for key, value in item.items())
    Path("engineering-team.toml").write_text("\n".join(toml) + "\n", encoding="utf-8")


def test_a_pipeline_run_fires_the_hooks_around_every_stage_and_at_the_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(
        before_stage=[log_hook("b", stages=["plan", "verify"])],
        after_stage=[log_hook("a", stages=["plan", "verify"])],
        on_finish=[log_hook("f")],
    )
    use_runner(monkeypatch, FakeRunner())

    assert start() == 0

    lines = (Path.cwd() / "hooks.log").read_text(encoding="utf-8").splitlines()
    assert lines == [
        "before_stage plan -",
        "after_stage plan succeeded",
        "before_stage verify -",
        "after_stage verify succeeded",
        "on_finish - succeeded",
    ]
    events = [
        e.data for e in read_events(run_dir(only_run()) / "events.jsonl") if e.type == "hook.ran"
    ]
    assert len(events) == 5 and all(e["ok"] for e in events)


def test_a_failing_hook_does_not_fail_the_run_and_shows_in_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(
        before_stage=[python_hook("import sys; sys.exit(1)")],
        on_finish=[{"command": ["/nonexistent/program"]}],
    )
    use_runner(monkeypatch, FakeRunner())

    assert start() == 0

    manifest = only_run()
    assert manifest.status == "succeeded" and manifest.verdict == "verified"
    events = [
        e.data for e in read_events(run_dir(manifest) / "events.jsonl") if e.type == "hook.ran"
    ]
    assert events and not any(e["ok"] for e in events)
    report = (run_dir(manifest) / "report.html").read_text(encoding="utf-8")
    assert "hook failed" in report


def test_a_cancelled_run_still_reports_through_on_finish_and_a_failed_stage_through_after_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(
        after_stage=[log_hook("a", statuses=["cancelled", "failed"])],
        on_finish=[log_hook("f", statuses=["cancelled"])],
    )
    use_runner(monkeypatch, FakeRunner(cancel_at="foundation"))

    assert start() == 130

    lines = (Path.cwd() / "hooks.log").read_text(encoding="utf-8").splitlines()
    assert lines == ["after_stage foundation cancelled", "on_finish - cancelled"]


# -- secrets in the settings ----------------------------------------------------------------------


def test_a_webhook_address_and_a_servers_environment_are_hidden_everywhere_settings_are_shown(
    make_context: MakeContext,
) -> None:
    from engineering_team.runtime.session import RunRecorder

    settings = load_settings(
        overrides={
            "hooks.on_finish": [{"url": WEBHOOK}, {"command": ["notify.sh", "--token", "abc"]}],
            "mcp.local": {"command": "srv", "env": {"INDEX_DIR": "/private/dir"}},
            "mcp.docs": {"url": "https://user:pw@docs.example.com/mcp?k=1"},
        }
    )

    shown = {row.key: row.value for row in settings.describe()}
    ctx = make_context(settings=settings)
    RunRecorder.begin(ctx, request="x")
    record = (ctx.run_dir / "settings.json").read_text(encoding="utf-8")

    assert shown["hooks.on_finish.0.url"] == "https://hooks.example.com/***"
    assert shown["hooks.on_finish.1.command"] == "notify.sh, --token, abc"
    assert shown["mcp.local.env.INDEX_DIR"] == "****"
    assert shown["mcp.docs.url"] == "https://***@docs.example.com/mcp?***"
    for text in (json.dumps(shown), record):
        assert "SECRETPART" not in text and "/private/dir" not in text and "pw@" not in text
    assert "hooks.example.com" in record
