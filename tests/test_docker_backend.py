"""The Docker backend's lifecycle, against a fake ``docker`` (no daemon needed)."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from docker_fakes import calls, install_fake_docker, verbs

from engineering_team.execution import (
    ExecutionBackend,
    ExecutionError,
    ExecutionUnavailable,
    LocalBackend,
    create_backend,
    has_executable,
)
from engineering_team.execution.backend import CommandSpec, ProcessHandle
from engineering_team.execution.docker import DockerBackend, docker_status
from engineering_team.settings import DockerSettings, ExecutionSettings, load_settings

RUN_ID = "20261003-120000-abc123"


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake ``docker`` first on PATH; returns the file its invocations are logged to."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    install_fake_docker(bin_dir)
    log = tmp_path / "docker-calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("DOCKER_FAKE_LOG", str(log))
    return log


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = (tmp_path / "project").resolve()
    root.mkdir()
    return root


@pytest.fixture
def make_backend(project: Path, tmp_path: Path, fake: Path) -> Callable[..., DockerBackend]:
    def build(
        settings: DockerSettings | None = None,
        cancel: threading.Event | None = None,
        **options: object,
    ) -> DockerBackend:
        return DockerBackend(
            project,
            tmp_path / "logs",
            RUN_ID,
            settings or DockerSettings(),
            cancel,
            uid=501,
            gid=20,
            **options,  # type: ignore[arg-type]
        )

    return build


def command(project: Path, *argv: str, **changes: object) -> CommandSpec:
    values: dict[str, object] = {
        "argv": argv,
        "cwd": project,
        "env": {"PATH": "/host/bin", "HOME": str(project / ".engineering-team" / "tool-home")},
        "timeout": 20.0,
        "label": "test",
    }
    return CommandSpec(**{**values, **changes})  # type: ignore[arg-type]


def hide_from_host(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Make the host look like it lacks these programs (everything else is found as usual)."""

    real = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name, *a, **k: None if name in names else real(name, *a, **k)
    )


def run_calls(log: Path) -> list[list[str]]:
    return [call["args"] for call in calls(log) if call["args"][0] == "run"]  # type: ignore[index, misc]


# -- is Docker usable? -----------------------------------------------------------------------


def test_status_reports_a_missing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")

    status = docker_status()

    assert not status.ok
    assert "not installed" in status.message and "--sandbox local" in (status.hint or "")


def test_status_reports_a_daemon_that_is_down(fake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_FAKE_DAEMON", "down")

    status = docker_status()

    assert not status.ok and "daemon is not reachable" in status.message
    assert "Is the docker daemon running" in status.message


def test_status_reports_a_remote_daemon(fake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "tcp://build-box:2375")

    status = docker_status()

    assert not status.ok and "remote daemon" in status.message
    assert "bind-mounts" in (status.hint or "")
    assert calls(fake) == []  # it never talked to the remote machine


def test_status_reports_windows_containers_and_a_working_docker(
    fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKER_FAKE_VERSION", "27.3.1 windows")
    assert not docker_status().ok

    monkeypatch.setenv("DOCKER_FAKE_VERSION", "27.3.1 linux")
    status = docker_status()
    assert status.ok and status.version == "27.3.1"


# -- choosing the backend ---------------------------------------------------------------------


def settings_for(backend: str) -> object:
    return load_settings().model_copy(update={"execution": ExecutionSettings(backend=backend)})


def test_local_is_the_default_backend(tmp_path: Path) -> None:
    backend = create_backend(load_settings(), tmp_path, tmp_path / "logs", RUN_ID)

    assert type(backend) is LocalBackend and isinstance(backend, ExecutionBackend)


def test_docker_without_docker_is_an_error_not_a_fallback(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ExecutionUnavailable, match="Docker is not installed") as caught:
        create_backend(settings_for("docker"), project, tmp_path / "logs", RUN_ID)  # type: ignore[arg-type]

    assert isinstance(caught.value, ValueError)  # the CLI reports it as a usage error


def test_docker_with_a_dead_daemon_is_an_error(
    project: Path, tmp_path: Path, fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKER_FAKE_DAEMON", "down")

    with pytest.raises(ExecutionUnavailable, match="daemon is not reachable"):
        create_backend(settings_for("docker"), project, tmp_path / "logs", RUN_ID)  # type: ignore[arg-type]


def test_a_working_docker_gives_the_docker_backend(
    project: Path, tmp_path: Path, fake: Path
) -> None:
    backend = create_backend(settings_for("docker"), project, tmp_path / "logs", RUN_ID)  # type: ignore[arg-type]

    assert isinstance(backend, DockerBackend) and isinstance(backend, ExecutionBackend)
    assert has_executable(backend, "definitely-not-installed-on-the-host")  # the image decides


def test_the_run_context_uses_the_configured_backend(
    make_context: Callable[..., object], fake: Path
) -> None:
    ctx = make_context("sandboxed", settings=settings_for("docker"))

    assert isinstance(ctx.backend, DockerBackend)  # type: ignore[attr-defined]


def test_a_run_context_without_docker_does_not_start_locally(
    make_context: Callable[..., object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")

    with pytest.raises(ExecutionUnavailable):
        make_context("unsandboxed", settings=settings_for("docker"))


def test_root_is_refused(project: Path, tmp_path: Path, fake: Path) -> None:
    with pytest.raises(ExecutionUnavailable, match="not root"):
        DockerBackend(project, tmp_path / "logs", RUN_ID, DockerSettings(), uid=0, gid=0)


def test_a_project_path_with_a_comma_is_refused(tmp_path: Path, fake: Path) -> None:
    odd = tmp_path / "a,b"
    odd.mkdir()

    with pytest.raises(ExecutionUnavailable, match="comma"):
        DockerBackend(odd, tmp_path / "logs", RUN_ID, DockerSettings(), uid=501, gid=20)


# -- running commands -------------------------------------------------------------------------


def test_a_command_runs_in_a_container_and_reports_like_a_local_one(
    make_backend: Callable[..., DockerBackend], project: Path, fake: Path
) -> None:
    backend = make_backend()

    record = backend.run(command(project, "pytest", "-q"))

    assert record.exit_code == 0 and not record.timed_out and not record.truncated
    assert "hello from the container" in record.output_tail
    assert record.argv == ("pytest", "-q") and record.cwd == project  # the command, not docker's
    assert record.id == "cmd-1" and record.log_path.name == "1.log"
    assert verbs(fake) == ["image", "run"]
    [argv] = run_calls(fake)
    assert argv[argv.index("--name") + 1] == f"et-{RUN_ID}-cmd-1"
    assert argv[-2:] == ["pytest", "-q"]
    assert "astral/uv:python3.12-bookworm-slim" in argv  # pytest -> the Python image


def test_the_exit_code_is_the_commands(
    make_backend: Callable[..., DockerBackend], project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKER_FAKE_MODE", "exit3")

    record = make_backend().run(command(project, "pytest"))

    assert record.exit_code == 3 and "failing" in record.output_tail


def test_the_environment_travels_in_a_private_file_not_on_the_command_line(
    make_backend: Callable[..., DockerBackend], project: Path, fake: Path
) -> None:
    secret = "sk-test-not-a-real-key"
    spec = command(
        project, "pytest", env={"PATH": "/host/bin", "HOME": "/p/home", "API_TOKEN": secret}
    )

    make_backend().run(spec)

    [call] = [c for c in calls(fake) if c["args"][0] == "run"]  # type: ignore[index]
    assert secret not in " ".join(call["args"])  # type: ignore[arg-type]
    assert call["env_mode"] == "0o600"
    env_file = str(call["env_file"])
    assert f"API_TOKEN={secret}" in env_file and "HOME=/p/home" in env_file
    assert "PATH=/host/bin" not in env_file and "CI=1" in env_file
    assert not list((project.parent / "logs").glob("*.env"))  # removed once the command ended


def test_the_host_environment_never_reaches_the_workload(
    make_backend: Callable[..., DockerBackend],
    project: Path,
    fake: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host-canary")

    make_backend().run(command(project, "pytest"))

    [call] = [c for c in calls(fake) if c["args"][0] == "run"]  # type: ignore[index]
    assert "sk-host-canary" not in str(call)


def test_each_image_is_checked_once_and_pulled_when_missing(
    make_backend: Callable[..., DockerBackend],
    project: Path,
    fake: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_FAKE_IMAGES", "absent")
    backend = make_backend()

    backend.run(command(project, "pytest"))
    backend.run(command(project, "pytest"))

    assert verbs(fake) == ["image", "pull", "run", "run"]


def test_a_failed_pull_says_what_to_do(
    make_backend: Callable[..., DockerBackend],
    project: Path,
    fake: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_FAKE_IMAGES", "absent")
    monkeypatch.setenv("DOCKER_FAKE_PULL", "fail")

    with pytest.raises(ExecutionError, match="could not pull image .*pull access denied") as caught:
        make_backend().run(command(project, "pytest"))

    assert isinstance(caught.value, OSError)  # tools report it as ERROR:, git as a GitError
    assert "execution.docker.image" in str(caught.value)
    assert "run" not in verbs(fake)


def test_images_follow_the_command_and_the_settings(
    make_backend: Callable[..., DockerBackend], project: Path
) -> None:
    default = make_backend()
    assert default.image_for(command(project, "npm", "test")) == "node:22-slim"
    assert default.image_for(command(project, "git", "status", label="git")) == "alpine/git:2.54.0"

    custom = make_backend(
        DockerSettings(image="my/image:1", setup_image="my/setup:1", git_image="my/git:1")
    )
    assert custom.image_for(command(project, "npm", "test")) == "my/image:1"
    assert custom.image_for(command(project, "npm", "ci", network=True)) == "my/setup:1"
    assert custom.image_for(command(project, "git", "log", label="git")) == "my/git:1"


def test_the_workspace_state_directories_exist_for_the_mounts(
    make_backend: Callable[..., DockerBackend], project: Path
) -> None:
    make_backend()

    for name in ("tmp", "cache", "tool-home"):
        assert (project / ".engineering-team" / name).is_dir()


def test_an_output_flood_is_bounded(
    make_backend: Callable[..., DockerBackend], project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKER_FAKE_MODE", "flood")

    record = make_backend(max_log_bytes=50_000).run(command(project, "pytest"))

    assert record.truncated and len(record.output_tail) < 20_000
    assert record.log_path.stat().st_size < 51_000
    assert "log capped" in record.log_path.read_text(encoding="utf-8")


# -- timeouts, cancellation, cleanup ----------------------------------------------------------


def test_a_timeout_stops_and_removes_the_container(
    make_backend: Callable[..., DockerBackend],
    project: Path,
    fake: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_FAKE_MODE", "hang")

    started = time.monotonic()
    record = make_backend().run(command(project, "pytest", timeout=1.0))

    assert record.timed_out and time.monotonic() - started < 15
    name = f"et-{RUN_ID}-cmd-1"
    removals = [c["args"] for c in calls(fake) if c["args"][0] in ("stop", "rm")]  # type: ignore[index]
    assert [r[0] for r in removals] == ["stop", "rm"]
    assert all(name in r for r in removals)
    assert "--force" in removals[1]


def test_cancelling_the_run_removes_the_container(
    make_backend: Callable[..., DockerBackend],
    project: Path,
    fake: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_FAKE_MODE", "hang")
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()

    record = make_backend(cancel=cancel).run(command(project, "pytest", timeout=30.0))

    assert record.cancelled and not record.timed_out
    assert {"stop", "rm"} <= set(verbs(fake))


def test_a_background_process_is_a_handle_whose_stop_removes_the_container(
    make_backend: Callable[..., DockerBackend],
    project: Path,
    fake: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_FAKE_MODE", "hang")
    handle = make_backend().start(command(project, "node", "server.js", ports=(3000,)))
    assert isinstance(handle, ProcessHandle)
    deadline = time.monotonic() + 5
    while "started" not in handle.read_output() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert handle.poll() is None

    record = handle.stop(grace=1.0)

    assert handle.poll() is not None and record.exit_code != 0
    assert {"stop", "rm"} <= set(verbs(fake))
    [argv] = run_calls(fake)
    assert "127.0.0.1:3000:3000/tcp" in argv  # published on loopback only


def test_close_removes_every_container_the_run_left_behind(
    make_backend: Callable[..., DockerBackend], fake: Path
) -> None:
    make_backend().close("the run ended")

    ps, rm = [c["args"] for c in calls(fake)]  # type: ignore[misc]
    assert f"label=engineering-team.run={RUN_ID}" in ps
    assert rm[:3] == ["rm", "--force", "--volumes"] and rm[3:] == ["c0ffee", "beef01"]


# -- the rest of the program honours the backend ----------------------------------------------


def test_a_sandbox_resolves_programs_itself(
    make_backend: Callable[..., DockerBackend], monkeypatch: pytest.MonkeyPatch
) -> None:
    hide_from_host(monkeypatch, "node")

    assert not has_executable(None, "node")  # the host has no node...
    assert has_executable(make_backend(), "node")  # ...but the node image does


def test_prepare_command_does_not_look_for_the_program_on_the_host(
    make_backend: Callable[..., DockerBackend], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engineering_team.tools.commands import prepare_command
    from engineering_team.tools.workspace import ProjectWorkspace, WorkspaceError

    hide_from_host(monkeypatch, "node")
    workspace = ProjectWorkspace.create(tmp_path / "ws")

    with pytest.raises(WorkspaceError, match="Executable not found: node"):
        prepare_command(workspace, "node app.js")
    assert prepare_command(workspace, "node app.js", backend=make_backend()).argv[0] == "node"


def test_environment_info_describes_the_sandbox_instead_of_probing_it(
    make_context: Callable[..., object], fake: Path
) -> None:
    from engineering_team.tools.envinfo import environment_info

    text = environment_info(make_context("info", settings=settings_for("docker")))  # type: ignore[arg-type]

    assert text.startswith("Docker sandbox: commands run in a fresh container")
    assert "no network, except install commands" in text and "listen on 0.0.0.0" in text
    assert verbs(fake) == ["version"]  # nothing was started (or pulled) to answer


def test_git_is_available_when_the_sandbox_image_has_it(
    make_context: Callable[..., object], fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hide_from_host(monkeypatch, "git")
    ctx = make_context("gitbox", settings=settings_for("docker"))

    assert ctx.git.available() is True  # type: ignore[attr-defined]


def test_git_runs_in_the_git_image_with_a_trusted_mount(
    make_context: Callable[..., object], fake: Path
) -> None:
    ctx = make_context("gitbox", settings=settings_for("docker"))

    ctx.git.head()  # type: ignore[attr-defined]

    [argv] = run_calls(fake)
    assert "alpine/git:2.54.0" in argv
    env_file = str(next(c for c in calls(fake) if c["args"][0] == "run")["env_file"])  # type: ignore[index]
    assert "GIT_CONFIG_VALUE_0=*" in env_file and "GIT_CONFIG_NOSYSTEM=1" in env_file


def test_the_sandbox_flag_overrides_the_backend_setting() -> None:
    from engineering_team import main

    args = main._run_parser().parse_args(["--sandbox", "docker", "--request", "x"])

    assert main._cli_overrides(args)["execution.backend"] == "docker"
    assert "execution.backend" not in main._cli_overrides(main._run_parser().parse_args([]))
    with pytest.raises(SystemExit):
        main._run_parser().parse_args(["--sandbox", "vm"])


def test_a_run_that_cannot_get_its_backend_leaves_no_run_directory(
    make_context: Callable[..., object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")

    with pytest.raises(ExecutionUnavailable):
        make_context("empty", settings=settings_for("docker"))

    assert not list((tmp_path / "empty" / ".engineering-team" / "runs").glob("*"))
