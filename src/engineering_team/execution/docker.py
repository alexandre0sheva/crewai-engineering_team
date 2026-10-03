"""The Docker execution backend: every command runs in its own hardened, throwaway container.

``docker run`` is spawned like any local process (its output streams to the same capped log with
the same head-and-tail window), so timeouts, cancellation, and bounded output behave exactly as
they do locally; what differs is the boundary around the workload. See ``docs/SAFETY.md`` for
what that boundary does and does not protect, and :mod:`engineering_team.execution.docker_args`
for the flags.

Docker is never a silent fallback: when it is unavailable the run refuses to start
(:class:`~engineering_team.execution.backend.ExecutionUnavailable`).
"""

from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import subprocess
import threading
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from engineering_team.execution.backend import (
    CommandRecord,
    CommandSpec,
    ExecutionError,
    ExecutionUnavailable,
)
from engineering_team.execution.docker_args import (
    LABEL_RUN,
    VISIBLE_STATE,
    DockerOptions,
    SandboxError,
    container_env,
    env_file_text,
    run_argv,
)
from engineering_team.execution.images import default_image, family_for
from engineering_team.execution.local import (
    DEFAULT_HEAD_CHARS,
    DEFAULT_MAX_LOG_BYTES,
    DEFAULT_TAIL_CHARS,
    KILL_GRACE_SECONDS,
    NON_INTERACTIVE_ENV,
    LocalBackend,
    LocalProcess,
)
from engineering_team.settings import DockerSettings
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY

DOCKER_PROBE_SECONDS = 15.0
PULL_SECONDS = 900.0
CLEANUP_SECONDS = 20.0
# The only host variables the ``docker`` client itself needs. Nothing else (no API key, no
# token) is ever in its environment: the workload gets what the command spec lists, via a file.
CLIENT_ENV_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "XDG_CONFIG_HOME",
        "XDG_RUNTIME_DIR",
        "SYSTEMROOT",
    }
)
CLIENT_ENV_PREFIXES = ("DOCKER_",)
# Git refuses a repository owned by another uid (Docker Desktop maps ownership); the sandbox
# is the only thing that touches it, so trusting the mount is right.
GIT_SAFE_DIRECTORY = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "safe.directory",
    "GIT_CONFIG_VALUE_0": "*",
}

_backends: weakref.WeakSet[DockerBackend] = weakref.WeakSet()


@atexit.register
def _close_backends() -> None:
    for backend in list(_backends):
        backend.close("the program is exiting")


@dataclass(frozen=True)
class DockerStatus:
    """Whether Docker can run the sandbox, and if not, why and what to do."""

    ok: bool
    message: str
    version: str | None = None
    hint: str | None = None


def client_environment() -> dict[str, str]:
    """The environment for the ``docker`` client process (see ``CLIENT_ENV_NAMES``)."""

    return {
        name: value
        for name, value in os.environ.items()
        if name in CLIENT_ENV_NAMES or name.startswith(CLIENT_ENV_PREFIXES)
    }


def docker_status(binary: str | None = None) -> DockerStatus:
    """Probe the Docker CLI and daemon (used by ``doctor`` and before a sandboxed run)."""

    docker = binary or shutil.which("docker")
    if not docker:
        return DockerStatus(
            False,
            "Docker is not installed (no `docker` on PATH).",
            hint="Install Docker (https://docs.docker.com/get-docker/), or run with "
            "--sandbox local.",
        )
    host = os.environ.get("DOCKER_HOST", "")
    if host.startswith(("tcp://", "ssh://")):
        return DockerStatus(
            False,
            f"DOCKER_HOST points at a remote daemon ({host.split('://')[0]}://...).",
            hint="The sandbox bind-mounts the project, so the daemon must run on this machine. "
            "Unset DOCKER_HOST or use --sandbox local.",
        )
    try:
        result = subprocess.run(
            [docker, "version", "--format", "{{.Server.Version}} {{.Server.Os}}"],
            capture_output=True,
            text=True,
            timeout=DOCKER_PROBE_SECONDS,
            env=client_environment(),
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DockerStatus(False, f"Docker did not answer ({exc}).", hint=_START_HINT)
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        detail = lines[-1] if lines else "no output"
        message = f"The Docker daemon is not reachable: {detail}"
        return DockerStatus(False, message, hint=_START_HINT)
    version, _, system = result.stdout.strip().partition(" ")
    if system and system != "linux":
        return DockerStatus(
            False,
            f"The Docker daemon runs {system} containers.",
            version=version,
            hint="Switch Docker to Linux containers, or use --sandbox local.",
        )
    return DockerStatus(True, f"Docker {version} is running.", version=version)


_START_HINT = "Start Docker (Docker Desktop or the docker service), or use --sandbox local."


class DockerProcess(LocalProcess):
    """One ``docker run`` client; stopping it removes the container, not just the client."""

    def __init__(
        self,
        spec: CommandSpec,
        command_id: str,
        log_path: Path,
        *,
        docker: str,
        argv: list[str],
        container: str,
        env_file: Path,
        **kwargs: object,
    ) -> None:
        self._docker = docker
        self._docker_argv = argv
        self.container = container
        self._env_file = env_file
        super().__init__(spec, command_id, log_path, **kwargs)  # type: ignore[arg-type]

    def _launch(self, spec: CommandSpec) -> tuple[list[str], Mapping[str, str], Path]:
        return self._docker_argv, {**client_environment(), **NON_INTERACTIVE_ENV}, spec.cwd

    def stop(self, grace: float = KILL_GRACE_SECONDS) -> CommandRecord:
        self._remove_container(grace)
        return super().stop(grace)

    def _finish(self) -> CommandRecord:
        record = super()._finish()
        with contextlib.suppress(OSError):
            self._env_file.unlink()
        if record.exit_code < 0:  # the client was killed: the container may have outlived it
            self._remove_container(0)
        return record

    def _remove_container(self, grace: float) -> None:
        """``docker stop`` (SIGTERM, then SIGKILL after ``grace``) and ``docker rm -f``."""

        if self._process.poll() is not None and not self._reader.is_alive():
            return
        for command in (
            ["stop", "--time", str(max(0, int(grace))), self.container],
            ["rm", "--force", "--volumes", self.container],
        ):
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    [self._docker, *command],
                    capture_output=True,
                    timeout=CLEANUP_SECONDS + grace,
                    env=client_environment(),
                    stdin=subprocess.DEVNULL,
                )


class DockerBackend(LocalBackend):
    """Run each command in a container; logs go to ``log_dir/<n>.log`` like the local backend.

    ``resolves_on_host`` is false: whether a program exists is decided by the image, so the
    host's ``PATH`` says nothing about it.
    """

    resolves_on_host = False

    def __init__(
        self,
        root: Path,
        log_dir: Path,
        run_id: str,
        settings: DockerSettings,
        cancel_event: threading.Event | None = None,
        *,
        docker: str | None = None,
        uid: int | None = None,
        gid: int | None = None,
        max_log_bytes: int = DEFAULT_MAX_LOG_BYTES,
        head_chars: int = DEFAULT_HEAD_CHARS,
        tail_chars: int = DEFAULT_TAIL_CHARS,
    ) -> None:
        super().__init__(
            log_dir,
            cancel_event,
            max_log_bytes=max_log_bytes,
            head_chars=head_chars,
            tail_chars=tail_chars,
        )
        self.root = root.resolve()
        self.run_id = run_id
        self.settings = settings
        self._docker = docker or shutil.which("docker") or "docker"
        host_uid = os.getuid() if uid is None else uid
        host_gid = os.getgid() if gid is None else gid
        if host_uid == 0:
            raise ExecutionUnavailable(
                "The Docker sandbox runs commands as your user, not root, and you are root. "
                "Run as a regular user, or use --sandbox local."
            )
        if "," in str(self.root):
            raise ExecutionUnavailable(
                f"The project path {self.root} contains a comma, which Docker mounts cannot "
                "express. Move the project or use --sandbox local."
            )
        self.options = DockerOptions(
            root=self.root,
            uid=host_uid,
            gid=host_gid,
            memory=settings.memory,
            cpus=settings.cpus,
            pids_limit=settings.pids_limit,
            tmpfs_size=settings.tmpfs_size,
            network_for_setup=settings.network == "setup",
            run_id=run_id,
        )
        state = self.root / CONTROLLER_DIRECTORY
        for name in VISIBLE_STATE:  # bind sources must exist (and belong to us, not to Docker)
            (state / name).mkdir(parents=True, exist_ok=True)
        self._images: set[str] = set()
        self._image_lock = threading.Lock()
        _backends.add(self)

    # -- choosing and fetching the image ----------------------------------------------

    def image_for(self, spec: CommandSpec) -> str:
        """The image for ``spec``: git's own, the setup image for network commands, the
        configured one, or the stack's default."""

        cfg = self.settings
        if spec.label == "git" or spec.argv[0] == "git":
            return cfg.git_image
        if spec.network and cfg.setup_image:
            return cfg.setup_image
        if cfg.image:
            return cfg.image
        return default_image(family_for(spec.argv[0], self.root, spec.cwd))

    def describe(self) -> str:
        """What the sandbox is, in a few lines an agent can plan with."""

        cfg = self.settings
        images = (
            f"the configured image ({cfg.image})"
            if cfg.image
            else f"an image chosen per language (python: {default_image('python')}, "
            f"node: {default_image('node')}, ...)"
        )
        network = (
            "no network, except install commands (Install Dependencies, audits)"
            if cfg.network == "setup"
            else "no network at all"
        )
        return (
            f"Docker sandbox: commands run in a fresh container from {images}; "
            f"{network}; {cfg.memory} RAM, {cfg.cpus:g} CPUs, {cfg.pids_limit} processes; the "
            "project is read-write and everything else is read-only or absent. A server that "
            "publishes a port must listen on 0.0.0.0 inside the container."
        )

    def _ensure_image(self, image: str) -> None:
        with self._image_lock:
            if image in self._images:
                return
            if self._docker_call("image", "inspect", "--format", "{{.Id}}", image).returncode == 0:
                self._images.add(image)
                return
            pulled = self._docker_call("pull", "--quiet", image, timeout=PULL_SECONDS)
            if pulled.returncode != 0:
                lines = (pulled.stderr or pulled.stdout).strip().splitlines()
                raise ExecutionError(
                    f"Docker could not pull image {image}: {lines[-1] if lines else 'no output'}. "
                    "Check the name (execution.docker.image) and your connection, or run "
                    f"`docker pull {image}` yourself."
                )
            self._images.add(image)

    def _docker_call(
        self, *args: str, timeout: float = DOCKER_PROBE_SECONDS
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self._docker, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=client_environment(),
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExecutionError(f"`docker {args[0]}` took longer than {timeout:.0f}s.") from exc
        except OSError as exc:
            raise ExecutionError(f"Docker cannot run ({exc.strerror or exc}).") from exc

    # -- running -----------------------------------------------------------------------

    def _spawn(self, spec: CommandSpec, command_id: str, log_path: Path) -> LocalProcess:
        image = self.image_for(spec)
        self._ensure_image(image)
        env = container_env(spec)
        env.update(NON_INTERACTIVE_ENV)
        if image == self.settings.git_image or spec.argv[0] == "git":
            env.update(GIT_SAFE_DIRECTORY)
        container = f"et-{self.run_id}-{command_id}"
        env_file = log_path.with_suffix(".env")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(env_file_text(env))
        try:
            argv = run_argv(
                spec,
                self.options,
                docker=self._docker,
                image=image,
                name=container,
                command_id=command_id,
                env_file=env_file,
            )
            return DockerProcess(
                spec,
                command_id,
                log_path,
                docker=self._docker,
                argv=argv,
                container=container,
                env_file=env_file,
                cancel_event=self._cancel,
                **self._limits,
            )
        except (SandboxError, OSError) as exc:
            env_file.unlink(missing_ok=True)
            if isinstance(exc, SandboxError):
                raise ExecutionError(str(exc)) from exc
            raise

    def close(self, reason: str = "the run ended") -> None:
        """Remove every container this run still has (stopped, stuck, or orphaned)."""

        del reason
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            listed = self._docker_call(
                "ps", "--all", "--quiet", "--filter", f"label={LABEL_RUN}={self.run_id}"
            )
            ids = listed.stdout.split()
            if ids:
                self._docker_call("rm", "--force", "--volumes", *ids, timeout=CLEANUP_SECONDS)
