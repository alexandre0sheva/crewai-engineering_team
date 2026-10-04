"""The report written at the end of real (scripted) runs, and the ``report`` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cli_helpers import latest_run, project, run_new, workspace_root
from pipeline_fakes import FakeRunner, write_checks
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.report import write as write_module
from engineering_team.runtime.run_store import RunStore

runner = CliRunner()
FAILING = "- {id: tests, name: Project tests, kind: test, command: 'false'}\n"


def invoke(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["--workspace-root", str(workspace_root()), *args])


def run_dir_of(name: str = "demo") -> Path:
    return RunStore(project(name)).run_dir(latest_run(name))


def test_every_run_ends_with_a_report_in_its_run_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, _ = run_new(monkeypatch)

    html = (run_dir_of() / "report.html").read_text(encoding="utf-8")

    assert code == 0
    assert "Verified" in html and 'id="timeline"' in html and 'id="checks"' in html
    for stage in ("spec", "plan", "implement", "verify", "release"):
        assert stage in html
    assert "Project tests" in html  # the controller's check
    assert "K-001" in html or "WP-1" in html  # the board has cards


def test_a_failed_run_gets_a_readable_report_saying_why(monkeypatch: pytest.MonkeyPatch) -> None:
    code, _ = run_new(monkeypatch, runner=FakeRunner(fail={"plan": 99}))

    html = (run_dir_of() / "report.html").read_text(encoding="utf-8")

    assert code == 1
    assert "<h2>Failed</h2>" in html and 'class="banner bad"' in html
    assert "Stage plan failed" in html  # the reason is on the page, in the banner
    assert html.index("Stage plan failed") < html.index('id="summary"')


def test_a_run_that_fails_verification_shows_the_failing_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = write_checks(FAILING, "failing.yaml")

    code, _ = run_new(monkeypatch, "--checks", str(bad))

    html = (run_dir_of() / "report.html").read_text(encoding="utf-8")
    assert code == 3
    assert "Not verified" in html and "Required check tests failed" in html
    assert "Project tests" in html


def test_a_cancelled_run_gets_a_report(monkeypatch: pytest.MonkeyPatch) -> None:
    code, _ = run_new(monkeypatch, runner=FakeRunner(cancel_at="foundation"))

    html = (run_dir_of() / "report.html").read_text(encoding="utf-8")

    assert code == 130
    assert "<h2>Cancelled</h2>" in html and "foundation" in html


def test_a_report_that_cannot_be_written_does_not_fail_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(write_module, "build_report", lambda _: 1 / 0)

    code, _ = run_new(monkeypatch)

    assert code == 0
    assert not (run_dir_of() / "report.html").exists()


def test_the_end_of_run_summary_names_the_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = run_new(monkeypatch, "--json")

    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert data["run_report"] == str(run_dir_of() / "report.html")


# -- the command ---------------------------------------------------------------------------------


@pytest.fixture
def finished(monkeypatch: pytest.MonkeyPatch) -> str:
    code, _ = run_new(monkeypatch)
    assert code == 0
    return latest_run()


def test_report_writes_html_by_default_and_names_the_file(finished: str) -> None:
    (run_dir_of() / "report.html").unlink()

    result = invoke("report")

    assert result.exit_code == 0
    path = run_dir_of() / "report.html"
    assert path.is_file() and str(path) in result.stdout


def test_report_can_write_markdown(finished: str) -> None:
    result = invoke("report", finished[:12], "--format", "md")

    text = (run_dir_of() / "report.md").read_text(encoding="utf-8")
    assert result.exit_code == 0 and "report.md" in result.stdout
    assert text.startswith(f"# Run {finished}: Verified")
    for heading in ("## Summary", "## Stage timeline", "## Independent checks", "## Environment"):
        assert heading in text


def test_report_json_output(finished: str) -> None:
    result = invoke("--json", "report", "--format", "md")

    data = json.loads(result.stdout)
    assert data["run_id"] == finished and data["format"] == "md" and data["opened"] is False
    assert data["path"].endswith("report.md")


def test_report_open_hands_the_file_to_the_browser(
    monkeypatch: pytest.MonkeyPatch, finished: str
) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)

    result = invoke("report", "--open")

    assert result.exit_code == 0
    assert opened == [(run_dir_of() / "report.html").as_uri()]


def test_report_open_says_so_when_nothing_could_open_it(
    monkeypatch: pytest.MonkeyPatch, finished: str
) -> None:
    monkeypatch.setattr("webbrowser.open", lambda url: False)

    result = invoke("report", "--open")

    assert result.exit_code == 0 and "Could not open it automatically" in result.output


def test_report_rejects_an_unknown_format_and_an_unknown_run(finished: str) -> None:
    assert invoke("report", "--format", "pdf").exit_code == 2
    assert invoke("report", "no-such-run").exit_code == 2
