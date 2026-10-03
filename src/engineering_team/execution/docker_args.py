"""The ``docker run`` command line for one sandboxed command (pure: nothing here runs Docker).

Everything the sandbox promises is visible in :func:`run_argv`, so a golden test can pin it. The
workspace is mounted at its own host path, so every path in an argument, an environment
variable, or a command's output means the same inside and outside the container.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from engineering_team.execution.backend import CommandSpec
from engineering_team.tools.workspace import AGENT_SCRATCH_PARTS, CONTROLLER_DIRECTORY

LABEL_RUN = "engineering-team.run"
LABEL_COMMAND = "engineering-team.command"
LABEL_MARK = "engineering-team"

# Controller directories that stay visible (read-write) inside the container; everything else
# under ``.engineering-team/`` (run records, pinned checks, the board) is hidden by a tmpfs.
VISIBLE_STATE = ("tmp", "cache", "tool-home")

# Environment names that describe the host, not the sandbox: the image supplies its own.
HOST_ONLY_ENV = frozenset(
    {
        "PATH",
        "VIRTUAL_ENV",
        "PYTHONHOME",
        "PYTHONPATH",
        "SHELL",
        "USER",
        "LOGNAME",
        "PWD",
        "OLDPWD",
        "SYSTEMROOT",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
)
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class SandboxError(ValueError):
    """A command the sandbox cannot run as asked (the message says how to fix the call)."""


@dataclass(frozen=True)
class DockerOptions:
    """The hardening knobs of one run's sandbox."""

    root: Path
    uid: int
    gid: int
    memory: str = "2g"
    cpus: float = 2.0
    pids_limit: int = 512
    tmpfs_size: str = "512m"
    network_for_setup: bool = True
    run_id: str = ""
    extra_labels: Mapping[str, str] = field(default_factory=dict)


def network_mode(spec: CommandSpec, options: DockerOptions) -> str:
    """``none`` unless the command is a setup command that may use the network, or it publishes
    a port (publishing needs the bridge network, so such a command also has outbound access)."""

    if spec.ports or (spec.network and options.network_for_setup):
        return "bridge"
    return "none"


def container_env(spec: CommandSpec) -> dict[str, str]:
    """The environment the workload gets: the spec's variables minus host-only ones."""

    env: dict[str, str] = {}
    for name, value in spec.env.items():
        if name in HOST_ONLY_ENV:
            continue
        if not NAME.fullmatch(name):
            raise SandboxError(f"Environment variable name {name!r} is not valid.")
        if "\n" in value or "\r" in value or "\0" in value:
            raise SandboxError(f"Environment variable {name} has a line break in its value.")
        env[name] = value
    return env


def env_file_text(env: Mapping[str, str]) -> str:
    """``NAME=value`` lines for ``--env-file`` (values stay off the process list)."""

    return "".join(f"{name}={value}\n" for name, value in sorted(env.items()))


def check_cwd(spec: CommandSpec, root: Path) -> Path:
    """``spec.cwd`` if it lies inside the project and not in hidden controller state."""

    cwd = spec.cwd.resolve()
    try:
        relative = cwd.relative_to(root)
    except ValueError:
        raise SandboxError(
            f"The Docker sandbox only runs commands inside the project; {spec.cwd} is outside "
            f"{root}."
        ) from None
    parts = relative.parts
    if parts[:1] == (CONTROLLER_DIRECTORY,) and parts[:2] != AGENT_SCRATCH_PARTS:
        raise SandboxError(f"{CONTROLLER_DIRECTORY}/ is controller state; run in the project.")
    return cwd


def mounts(options: DockerOptions) -> list[str]:
    """The ``--mount``/``--tmpfs`` arguments: the project, with controller state hidden."""

    root = options.root
    state = root / CONTROLLER_DIRECTORY
    args = ["--mount", f"type=bind,src={root},dst={root}"]
    # Hide the controller directory, then show back only the scratch, cache, and home that
    # project commands use. Docker mounts parents before children, so the order is safe.
    args += ["--tmpfs", f"{state}:rw,nosuid,nodev,noexec,size=1m,mode=0755"]
    for name in VISIBLE_STATE:
        args += ["--mount", f"type=bind,src={state / name},dst={state / name}"]
    return args


def run_argv(
    spec: CommandSpec,
    options: DockerOptions,
    *,
    docker: str,
    image: str,
    name: str,
    command_id: str,
    env_file: Path,
) -> list[str]:
    """The full ``docker run`` command for ``spec`` (``spec.argv`` follows the image name)."""

    cwd = check_cwd(spec, options.root)
    argv = [
        docker, "run", "--rm", "--init", "--pull", "never", "--name", name,
        "--label", f"{LABEL_MARK}=1",
        "--label", f"{LABEL_RUN}={options.run_id}",
        "--label", f"{LABEL_COMMAND}={command_id}",
        # Output reaches us through the attached stream; the daemon need not keep a second copy.
        "--log-driver", "none",
        "--user", f"{options.uid}:{options.gid}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", f"/tmp:rw,nosuid,nodev,exec,size={options.tmpfs_size}",
        "--pids-limit", str(options.pids_limit),
        "--memory", options.memory,
        "--memory-swap", options.memory,
        "--cpus", f"{options.cpus:g}",
        "--ulimit", "core=0",
        "--network", network_mode(spec, options),
        *mounts(options),
        "--workdir", str(cwd),
        "--env-file", str(env_file),
    ]  # fmt: skip
    for key, value in sorted(options.extra_labels.items()):
        argv += ["--label", f"{key}={value}"]
    for port in sorted(set(spec.ports)):
        if not 1 <= port <= 65535:
            raise SandboxError(f"{port} is not a valid TCP port.")
        argv += ["--publish", f"127.0.0.1:{port}:{port}/tcp"]
    # No entrypoint: the command is run as given, whatever the image's default does.
    argv += ["--entrypoint", "", image, *_command(spec.argv)]
    return argv


def _command(argv: Sequence[str]) -> list[str]:
    if not argv:
        raise SandboxError("There is no command to run.")
    return list(argv)
