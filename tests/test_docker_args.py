"""The ``docker run`` argv and the image choice, tested without Docker."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team.execution.backend import CommandSpec
from engineering_team.execution.docker_args import (
    DockerOptions,
    SandboxError,
    container_env,
    env_file_text,
    network_mode,
    run_argv,
)
from engineering_team.execution.images import DEFAULT_IMAGES, default_image, family_for


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project = (tmp_path / "project").resolve()
    project.mkdir()
    return project


def options(root: Path, **changes: object) -> DockerOptions:
    values: dict[str, object] = {
        "root": root,
        "uid": 501,
        "gid": 20,
        "run_id": "20261003-120000-abc123",
    }
    return DockerOptions(**{**values, **changes})  # type: ignore[arg-type]


def spec(root: Path, *argv: str, **changes: object) -> CommandSpec:
    values: dict[str, object] = {"argv": argv, "cwd": root, "env": {}, "label": "test"}
    return CommandSpec(**{**values, **changes})  # type: ignore[arg-type]


def argv_for(root: Path, command: CommandSpec, **changes: object) -> list[str]:
    return run_argv(
        command,
        options(root, **changes),
        docker="docker",
        image="python:3.12-slim",
        name="et-run-cmd-1",
        command_id="cmd-1",
        env_file=root / "logs" / "1.env",
    )


def test_the_generated_docker_run_command_is_pinned(root: Path) -> None:
    argv = argv_for(root, spec(root, "pytest", "-q"))
    state = root / ".engineering-team"

    assert argv == [
        "docker", "run", "--rm", "--init", "--pull", "never", "--name", "et-run-cmd-1",
        "--label", "engineering-team=1",
        "--label", "engineering-team.run=20261003-120000-abc123",
        "--label", "engineering-team.command=cmd-1",
        "--log-driver", "none",
        "--user", "501:20",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,exec,size=512m",
        "--pids-limit", "512",
        "--memory", "2g",
        "--memory-swap", "2g",
        "--cpus", "2",
        "--ulimit", "core=0",
        "--network", "none",
        "--mount", f"type=bind,src={root},dst={root}",
        "--tmpfs", f"{state}:rw,nosuid,nodev,noexec,size=1m,mode=0755",
        "--mount", f"type=bind,src={state / 'tmp'},dst={state / 'tmp'}",
        "--mount", f"type=bind,src={state / 'cache'},dst={state / 'cache'}",
        "--mount", f"type=bind,src={state / 'tool-home'},dst={state / 'tool-home'}",
        "--workdir", str(root),
        "--env-file", str(root / "logs" / "1.env"),
        "--entrypoint", "", "python:3.12-slim", "pytest", "-q",
    ]  # fmt: skip


def test_the_sandbox_never_asks_for_more_than_it_needs(root: Path) -> None:
    argv = argv_for(root, spec(root, "pytest"), memory="1g", cpus=0.5, pids_limit=64)
    for flag in ("--privileged", "--pid", "--cap-add", "--device", "--ipc", "--userns", "--uts"):
        assert flag not in argv
    assert not any("docker.sock" in item for item in argv)
    assert argv[argv.index("--network") + 1] != "host"
    assert argv[argv.index("--user") + 1] != "0:0"
    assert argv[argv.index("--memory") + 1] == "1g"
    assert argv[argv.index("--cpus") + 1] == "0.5"
    assert argv[argv.index("--pids-limit") + 1] == "64"
    assert "--env" not in argv and "-e" not in argv  # values go through the 0600 env file


def test_the_controller_directory_is_hidden_except_scratch_cache_and_home(root: Path) -> None:
    argv = argv_for(root, spec(root, "ls"))
    state = str(root / ".engineering-team")
    destinations = [a for a in argv if a.startswith(("type=bind", state))]

    assert destinations[0] == f"type=bind,src={root},dst={root}"
    assert destinations[1].startswith(state + ":")  # the tmpfs that hides it, before its children
    assert not any("runs" in item or "checks" in item for item in destinations)
    assert sorted(item.split("dst=")[1].rsplit("/", 1)[1] for item in destinations[2:]) == [
        "cache",
        "tmp",
        "tool-home",
    ]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, "none"),
        ({"network": True}, "bridge"),
        ({"ports": (3000,)}, "bridge"),
    ],
)
def test_network_is_off_except_for_setup_commands_and_published_ports(
    root: Path, changes: dict[str, object], expected: str
) -> None:
    assert network_mode(spec(root, "npm", "install", **changes), options(root)) == expected


def test_network_none_setting_refuses_even_setup_commands(root: Path) -> None:
    offline = options(root, network_for_setup=False)

    assert network_mode(spec(root, "npm", "install", network=True), offline) == "none"
    assert network_mode(spec(root, "node", "server.js", ports=(3000,)), offline) == "bridge"


def test_ports_are_published_on_loopback_only(root: Path) -> None:
    argv = argv_for(root, spec(root, "node", "server.js", ports=(8000, 3000, 3000)))

    published = [argv[i + 1] for i, item in enumerate(argv) if item == "--publish"]
    assert published == ["127.0.0.1:3000:3000/tcp", "127.0.0.1:8000:8000/tcp"]
    with pytest.raises(SandboxError, match="valid TCP port"):
        argv_for(root, spec(root, "node", ports=(70000,)))


def test_the_environment_is_the_specs_without_host_only_names() -> None:
    env = container_env(
        CommandSpec(
            argv=("pytest",),
            cwd=Path("/p"),
            env={"PATH": "/Users/me/bin", "HOME": "/p/.engineering-team/tool-home", "LANG": "C"},
        )
    )

    assert env == {"HOME": "/p/.engineering-team/tool-home", "LANG": "C"}
    assert env_file_text(env) == "HOME=/p/.engineering-team/tool-home\nLANG=C\n"


def test_an_environment_value_with_a_line_break_is_refused(root: Path) -> None:
    with pytest.raises(SandboxError, match="line break"):
        container_env(spec(root, "x", env={"A": "one\ntwo"}))
    with pytest.raises(SandboxError, match="not valid"):
        container_env(spec(root, "x", env={"A B": "1"}))


def test_commands_outside_the_project_or_in_controller_state_are_refused(
    root: Path, tmp_path: Path
) -> None:
    (root / ".engineering-team" / "runs").mkdir(parents=True)
    (root / ".engineering-team" / "tmp").mkdir()

    with pytest.raises(SandboxError, match="outside"):
        argv_for(root, spec(root, "ls", cwd=tmp_path))
    with pytest.raises(SandboxError, match="controller state"):
        argv_for(root, spec(root, "ls", cwd=root / ".engineering-team" / "runs"))
    assert argv_for(root, spec(root, "ls", cwd=root / ".engineering-team" / "tmp"))


# -- image choice ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("program", "family"),
    [
        ("pytest", "python"),
        ("uv", "python"),
        ("npm", "node"),
        ("npx", "node"),
        ("go", "go"),
        ("cargo", "rust"),
        ("mvn", "java"),
        ("dotnet", "dotnet"),
        ("bundle", "ruby"),
        ("composer", "php"),
    ],
)
def test_the_program_chooses_the_image_family(root: Path, program: str, family: str) -> None:
    assert family_for(program, root, root) == family
    assert default_image(family) == DEFAULT_IMAGES[family]


def test_other_programs_follow_the_stack_of_their_directory(root: Path) -> None:
    (root / "web").mkdir()
    (root / "web" / "package.json").write_text('{"name": "web"}', encoding="utf-8")
    (root / "api").mkdir()
    (root / "api" / "pyproject.toml").write_text('[project]\nname = "api"\n', encoding="utf-8")
    (root / "svc").mkdir()
    (root / "svc" / "go.mod").write_text("module svc\n", encoding="utf-8")

    assert family_for("make", root, root / "web") == "node"
    assert family_for("make", root, root / "api") == "python"
    assert family_for("make", root, root / "svc") == "go"
    assert family_for("make", root, root / "empty") == "python"  # nothing detected: the default


def test_every_default_image_is_a_pinned_tag() -> None:
    for image in DEFAULT_IMAGES.values():
        assert ":" in image and not image.endswith(":latest"), image
