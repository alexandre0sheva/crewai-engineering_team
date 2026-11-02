"""Which language server serves which files, and the run's one server per language.

Servers start lazily, on the first question about a file of their language, through the run's
execution backend, and are stopped when the run ends or is cancelled (the backend's process
watcher). A language whose server is missing or failed to start is remembered, so the question is
answered once ("no server") and the tool falls back to what it did before.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_team.codeintel.lsp import LspClient, LspError, LspUnavailable
from engineering_team.execution.backend import (
    CommandSpec,
    ExecutionError,
    has_executable,
    open_stdio,
)
from engineering_team.runtime.events import EventSink

if TYPE_CHECKING:
    from engineering_team.settings.lsp import LspSettings
    from engineering_team.tools.workspace import ProjectWorkspace


@dataclass(frozen=True)
class ServerSpec:
    language: str
    candidates: tuple[tuple[str, ...], ...]  # commands to try, in order; the first installed wins
    extensions: dict[str, str]  # file extension -> LSP language id


SERVERS: dict[str, ServerSpec] = {
    "python": ServerSpec(
        "python",
        (("pyright-langserver", "--stdio"), ("basedpyright-langserver", "--stdio")),
        {".py": "python", ".pyi": "python"},
    ),
    "typescript": ServerSpec(
        "typescript",
        (("typescript-language-server", "--stdio"),),
        {
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".js": "javascript",
            ".jsx": "javascriptreact",
            ".mjs": "javascript",
            ".cjs": "javascript",
        },
    ),
    "go": ServerSpec("go", (("gopls",),), {".go": "go"}),
    "rust": ServerSpec("rust", (("rust-analyzer",),), {".rs": "rust"}),
}


def language_of(path: str | Path) -> tuple[str, str] | None:
    """``(language, LSP language id)`` for a source file, or ``None`` for one no server serves."""

    suffix = Path(path).suffix.lower()
    for spec in SERVERS.values():
        if suffix in spec.extensions:
            return spec.language, spec.extensions[suffix]
    return None


class LspManager:
    """One server per language for a run."""

    def __init__(
        self,
        backend: object,
        settings: LspSettings,
        workspace: ProjectWorkspace,
        log_dir: Path,
        events: EventSink,
        cancel_event: threading.Event,
    ) -> None:
        self._backend = backend
        self._settings = settings
        self._workspace = workspace
        self._root = workspace.root
        self._log_dir = log_dir
        self._events = events
        self._cancel = cancel_event
        self._lock = threading.Lock()
        self._clients: dict[str, LspClient] = {}
        self._unavailable: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    def client_for(self, path: str | Path) -> tuple[LspClient, str]:
        """The server for ``path`` and the file's LSP language id. Raises
        :class:`LspUnavailable` with the reason when there is none."""

        found = language_of(path)
        if found is None:
            raise LspUnavailable(
                f"No language server is configured for {Path(path).suffix or path}."
            )
        language, language_id = found
        with self._lock:
            if language in self._unavailable:
                raise LspUnavailable(self._unavailable[language])
            client = self._clients.get(language)
            if client is not None and (gone := client.closed) is None:
                return client, language_id
            if client is not None:  # it died mid-run: not restarted, the run goes on without it
                del self._clients[language]
                message = (
                    f"{client.name} stopped earlier in this run ({gone}); it is not restarted."
                )
                self._unavailable[language] = message
                self._events.emit("lsp.unavailable", language=language, reason=message)
                raise LspUnavailable(message)
            try:
                client = self._start(language)
            except LspUnavailable as exc:
                self._unavailable[language] = str(exc)
                self._events.emit("lsp.unavailable", language=language, reason=str(exc))
                raise
            self._clients[language] = client
        return client, language_id

    def _command(self, language: str) -> tuple[str, ...]:
        configured = self._settings.servers.get(language)
        candidates = (tuple(configured),) if configured else SERVERS[language].candidates
        for argv in candidates:
            if has_executable(self._backend, argv[0]):
                return argv
        names = " or ".join(argv[0] for argv in candidates)
        raise LspUnavailable(
            f"No {language} language server is installed ({names}); install one or set "
            f"lsp.servers.{language}."
        )

    def _start(self, language: str) -> LspClient:
        argv = self._command(language)
        from engineering_team.tools.commands import command_environment  # tools import codeintel

        spec = CommandSpec(
            argv=argv,
            cwd=self._root,
            env=command_environment(self._workspace),  # as every project command: a safe subset
            label="lsp",
            timeout=0.0,
        )
        try:
            process = open_stdio(self._backend, spec)
        except (ExecutionError, OSError) as exc:
            raise LspUnavailable(f"Could not start {argv[0]}: {exc}") from exc
        client = LspClient(
            process,
            self._root,
            name=argv[0],
            request_timeout=self._settings.request_timeout_seconds,
            diagnostics_wait=self._settings.diagnostics_wait_seconds,
            cancel_event=self._cancel,
        )
        try:
            client.start(self._settings.startup_timeout_seconds)
        except LspError as exc:
            client.close()
            raise LspUnavailable(f"{argv[0]} did not start: {exc}") from exc
        self._events.emit("lsp.started", language=language, server=argv[0])
        return client

    def close_all(self, reason: str) -> None:
        with self._lock:
            clients, self._clients = list(self._clients.items()), {}
        for language, client in clients:
            client.close()
            self._events.emit("lsp.stopped", language=language, server=client.name, reason=reason)
