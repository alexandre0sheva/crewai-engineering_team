"""``doctor`` with a mocked environment: what it reports and what it never prints."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.cli.doctor import Check, probe_version, run_checks
from engineering_team.execution.docker import DockerStatus
from engineering_team.settings import Settings, load_settings

PRESENT = {"uv", "git", "node", "npm", "go", "java", "cargo"}
VERSIONS = {
    "uv": "0.9.1",
    "git": "2.50.0",
    "node": "22.1.0",
    "go": "1.25.0",
    "java": "21.0.2",
    "cargo": "1.90.0",
}


def fake_which(names: set[str]) -> Callable[[str], str | None]:
    return lambda name: f"/usr/bin/{name}" if name in names else None


def fake_probe(argv: tuple[str, ...]) -> str:
    return VERSIONS.get(Path(argv[0]).name, "1.0")


def docker_ok() -> DockerStatus:
    return DockerStatus(True, "Docker 27.3.1 is running.", version="27.3.1")


def docker_down() -> DockerStatus:
    return DockerStatus(False, "The Docker daemon is not reachable.", hint="Start Docker.")


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {check.name: check for check in checks}


def run(settings: Settings | None = None, **kwargs: object) -> dict[str, Check]:
    options: dict[str, object] = {
        "which": fake_which(PRESENT),
        "probe": fake_probe,
        "docker": docker_ok,
    }
    options.update(kwargs)
    return by_name(run_checks(settings or load_settings(), **options))  # type: ignore[arg-type]


def test_a_complete_machine_passes_every_check() -> None:
    checks = run()

    assert checks["uv"].detail == "0.9.1" and checks["git"].detail == "2.50.0"
    assert checks["node"].status == "ok" and checks["go"].detail == "1.25.0"
    assert checks["Credentials"].status == "ok" and "openai" in checks["Credentials"].detail
    assert "Docker 27.3.1" in checks["Docker"].detail
    assert "--sandbox docker" in checks["Docker"].hint  # recommended when it is there
    assert not [c for c in checks.values() if c.status == "fail"]


def test_missing_tools_are_named_with_what_to_do_about_them() -> None:
    checks = run(which=fake_which({"git"}))

    assert checks["uv"].status == "warn" and "docs.astral.sh/uv" in checks["uv"].hint
    assert (
        checks["node"].status == "info" and "Only needed for node projects" in checks["node"].hint
    )
    assert checks["git"].status == "ok"


def test_a_binary_for_another_cpu_is_reported_not_crashed_on() -> None:
    def broken(argv: tuple[str, ...]) -> str:
        raise OSError(86, "Bad CPU type in executable")

    checks = run(probe=broken)

    assert (
        checks["go"].status == "warn"
        and "cannot run (Bad CPU type in executable)" in checks["go"].detail
    )


def test_a_missing_api_key_is_a_failure_that_never_shows_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")

    checks = run()

    assert (
        checks["Credentials"].status == "fail" and "OPENAI_API_KEY" in checks["Credentials"].detail
    )


def test_the_key_value_is_never_in_the_output(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-test-do-not-print-me-0123456789"
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    result = CliRunner().invoke(app, ["--json", "doctor"])

    assert secret not in result.stdout and secret not in result.stderr


def test_docker_down_is_informational_unless_the_sandbox_is_chosen() -> None:
    assert run(docker=docker_down)["Docker"].status == "info"

    wanted = load_settings(overrides={"execution.backend": "docker"})
    checks = run(wanted, docker=docker_down)

    assert checks["Docker"].status == "fail" and checks["Docker"].hint == "Start Docker."


def test_an_unsupported_python_is_a_failure() -> None:
    assert run(python=(3, 10, 4))["Python"].status == "fail"
    assert run(python=(3, 14, 0))["Python"].status == "fail"
    assert run(python=(3, 12, 1))["Python"].status == "ok"


def test_online_sends_one_tiny_prompt_per_distinct_model() -> None:
    asked: list[str] = []

    def ping(resolved, settings) -> str:  # noqa: ANN001
        asked.append(resolved.model)
        if "luna" in resolved.model:
            raise RuntimeError("401 invalid key")
        return "OK"

    checks = run(online=True, ping=ping)

    models = {name: check for name, check in checks.items() if name.startswith("Model ")}
    assert len(asked) == len(set(asked)) == len(models) >= 2
    assert any(c.status == "fail" and "401 invalid key" in c.detail for c in models.values())
    assert any(c.status == "ok" for c in models.values())


def test_without_online_no_model_is_called() -> None:
    def ping(resolved, settings) -> str:  # noqa: ANN001
        raise AssertionError("must not be called")

    assert not [name for name in run(ping=ping) if name.startswith("Model ")]


def test_a_workspace_root_that_cannot_be_written_is_a_failure(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        checks = run(load_settings(overrides={"workspace_root": str(locked / "ws")}))
    finally:
        locked.chmod(0o700)

    assert checks["Workspace root"].status == "fail"


def test_doctor_exit_code_and_json_follow_the_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    ok = runner.invoke(app, ["--json", "doctor"])
    monkeypatch.delenv("OPENAI_API_KEY")
    broken = runner.invoke(app, ["--json", "doctor"])

    assert json.loads(ok.stdout)["ok"] is True and ok.exit_code == 0
    report = json.loads(broken.stdout)
    assert report["ok"] is False and broken.exit_code == 1
    assert {"name", "status", "detail", "hint"} == set(report["checks"][0])


def test_the_real_version_probe_reads_a_version_from_a_real_program() -> None:
    import sys

    assert probe_version((sys.executable, "--version")).startswith("3.")
    assert probe_version((sys.executable, "-c", "import sys; sys.exit(3)")) == ""
