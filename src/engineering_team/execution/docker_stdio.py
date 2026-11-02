"""An interactive process (a language server) in the Docker sandbox: ``docker run -i``."""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_team.execution.backend import CommandSpec, ExecutionError
from engineering_team.execution.docker_args import (
    SandboxError,
    container_env,
    env_file_text,
    run_argv,
)
from engineering_team.execution.stdio import NON_INTERACTIVE_ENV, LocalStdioProcess

if TYPE_CHECKING:
    from engineering_team.execution.docker import DockerBackend

CLEANUP_SECONDS = 20.0


class DockerStdioProcess(LocalStdioProcess):
    """One ``docker run -i`` client; stopping it removes the container, not just the client."""

    def __init__(
        self,
        backend: DockerBackend,
        spec: CommandSpec,
        log_path: Path,
        *,
        argv: list[str],
        container: str,
        env_file: Path,
        cancel_event: threading.Event | None,
    ) -> None:
        self._backend = backend
        self._argv = argv
        self.container = container
        self._env_file = env_file
        super().__init__(spec, log_path, cancel_event=cancel_event)

    def _launch(self, spec: CommandSpec) -> tuple[list[str], Mapping[str, str], Path]:
        from engineering_team.execution.docker import client_environment

        return self._argv, {**client_environment(), **NON_INTERACTIVE_ENV}, spec.cwd

    def stop(self, grace: float = 3.0) -> None:
        docker = self._backend._docker
        from engineering_team.execution.docker import client_environment

        for command in (
            ["stop", "--time", str(max(0, int(grace))), self.container],
            ["rm", "--force", "--volumes", self.container],
        ):
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    [docker, *command],
                    capture_output=True,
                    timeout=CLEANUP_SECONDS + grace,
                    env=client_environment(),
                    stdin=subprocess.DEVNULL,
                )
        super().stop(grace)
        with contextlib.suppress(OSError):
            self._env_file.unlink()


def open_docker_stdio(
    backend: DockerBackend, spec: CommandSpec, number: int, log_path: Path
) -> DockerStdioProcess:
    """Start ``spec`` in a container of the backend's sandbox with stdin and stdout attached."""

    image = backend.image_for(spec)
    backend._ensure_image(image)
    env = container_env(spec)
    env.update(NON_INTERACTIVE_ENV)
    name = f"et-{backend.run_id}-stdio-{number}"
    env_file = log_path.with_suffix(".env")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(env_file_text(env))
    try:
        argv = run_argv(
            spec,
            backend.options,
            docker=backend._docker,
            image=image,
            name=name,
            command_id=f"stdio-{number}",
            env_file=env_file,
            interactive=True,
        )
        return DockerStdioProcess(
            backend,
            spec,
            log_path,
            argv=argv,
            container=name,
            env_file=env_file,
            cancel_event=backend._cancel,
        )
    except (SandboxError, OSError) as exc:
        env_file.unlink(missing_ok=True)
        if isinstance(exc, SandboxError):
            raise ExecutionError(str(exc)) from exc
        raise
