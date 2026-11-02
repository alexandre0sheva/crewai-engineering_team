"""A minimal Language Server Protocol client: JSON-RPC over the stdio of a process.

The process comes from the run's execution backend (``execution/stdio.py``), so a server runs in
the Docker sandbox when the run does. The client does the handshake, opens files, and asks three
things: diagnostics, definitions and references. Positions are zero-based lines and UTF-16
columns on the wire (:func:`to_utf16`, :func:`from_utf16` convert from and to characters).
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from engineering_team.execution.backend import StdioProcess
from engineering_team.execution.stdio import StdioClosed

READ_SLICE = 0.2
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
SEVERITIES = {1: "error", 2: "warning", 3: "information", 4: "hint"}


class LspError(Exception):
    """A request failed, timed out, or the server answered with an error."""


class LspUnavailable(LspError):
    """There is no usable server (not installed, did not start, ended, or the run was
    cancelled). The message says why, for the agent."""


@dataclass(frozen=True)
class Diagnostic:
    line: int  # zero-based
    character: int
    severity: str
    message: str
    code: str = ""
    source: str = ""


@dataclass(frozen=True)
class Location:
    path: Path | None  # None: a URI that is not a local file
    uri: str
    line: int  # zero-based
    character: int  # UTF-16 offset


def to_utf16(text: str, column: int) -> int:
    """The UTF-16 offset of the zero-based character ``column`` of ``text``."""

    return len(text[:column].encode("utf-16-le")) // 2


def from_utf16(text: str, offset: int) -> int:
    """The zero-based character column of the UTF-16 ``offset`` in ``text``."""

    units = 0
    for index, char in enumerate(text):
        if units >= offset:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def path_of(uri: str) -> Path | None:
    parsed = urlparse(uri)
    return Path(unquote(parsed.path)) if parsed.scheme == "file" else None


def frame(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


class Decoder:
    """Cuts a byte stream into JSON-RPC messages (``Content-Length`` framing)."""

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer += data
        messages: list[dict[str, Any]] = []
        while True:
            head, separator, rest = self._buffer.partition(b"\r\n\r\n")
            if not separator:
                return messages
            length = 0
            for line in head.split(b"\r\n"):
                name, _, value = line.partition(b":")
                if name.strip().lower() == b"content-length":
                    length = int(value.strip() or 0)
            if length <= 0 or length > MAX_MESSAGE_BYTES:
                raise LspError(f"The server sent a message of {length} bytes.")
            if len(rest) < length:
                return messages
            self._buffer = rest[length:]
            decoded = json.loads(rest[:length].decode("utf-8", errors="replace"))
            if isinstance(decoded, dict):
                messages.append(decoded)


class LspClient:
    """One language server: started by :meth:`start`, closed by :meth:`close`."""

    def __init__(
        self,
        process: StdioProcess,
        root: Path,
        *,
        name: str,
        request_timeout: float = 30.0,
        diagnostics_wait: float = 10.0,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.name = name
        self._process = process
        self._root = root
        self._timeout = request_timeout
        self._wait = diagnostics_wait
        self._cancel = cancel_event
        self._pending: dict[int, Future[Any]] = {}
        self._next_id = 0
        self._write_lock = threading.Lock()
        self._state = threading.Condition()
        self._published: dict[str, list[Diagnostic]] = {}
        self._versions: dict[str, tuple[int, str]] = {}  # uri -> (version, text)
        self._capabilities: dict[str, Any] = {}
        self._closed = False
        self._reason = ""
        self._reader = threading.Thread(target=self._read, name=f"lsp-{name}", daemon=True)

    @property
    def closed(self) -> str | None:
        """Why the server is gone (it ended, crashed, or the run was cancelled), if it is."""

        with self._state:
            return (self._reason or "it stopped") if self._closed else None

    # -- the connection -----------------------------------------------------------------

    def start(self, timeout: float) -> None:
        self._reader.start()
        uri = self._root.as_uri()
        result = self.request(
            "initialize",
            {
                "processId": None,
                "clientInfo": {"name": "engineering-team"},
                "rootUri": uri,
                "workspaceFolders": [{"uri": uri, "name": self._root.name}],
                "capabilities": {
                    "general": {"positionEncodings": ["utf-16"]},
                    "workspace": {"configuration": True, "workspaceFolders": True},
                    "textDocument": {
                        "synchronization": {"didSave": False},
                        "publishDiagnostics": {},
                        "diagnostic": {"dynamicRegistration": False},
                        "definition": {"linkSupport": True},
                        "references": {},
                    },
                },
            },
            timeout=timeout,
        )
        self._capabilities = (result or {}).get("capabilities") or {}
        self._notify("initialized", {})

    def close(self) -> None:
        if not self._closed:
            try:
                self.request("shutdown", None, timeout=2.0)
                self._notify("exit", None)
            except LspError:
                pass
        self._fail_all("the server was closed")
        self._process.stop()

    def _send(self, message: dict[str, Any]) -> None:
        try:
            with self._write_lock:
                self._process.write(frame(message))
        except StdioClosed as exc:
            self._fail_all(str(exc))
            raise LspUnavailable(f"{self.name} is not running: {exc}.") from exc

    def _notify(self, method: str, params: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: Any, *, timeout: float | None = None) -> Any:
        if self._closed:
            raise LspUnavailable(f"{self.name} is not running ({self._reason}).")
        future: Future[Any] = Future()
        with self._state:
            self._next_id += 1
            request_id = self._next_id
            self._pending[request_id] = future
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + (self._timeout if timeout is None else timeout)
        try:
            while True:
                if self._cancel is not None and self._cancel.is_set():
                    raise LspUnavailable("The run was cancelled.")
                try:
                    return future.result(READ_SLICE)
                except TimeoutError:
                    if time.monotonic() >= deadline:
                        self._notify("$/cancelRequest", {"id": request_id})
                        raise LspError(f"{self.name} did not answer {method} in time.") from None
        finally:
            with self._state:
                self._pending.pop(request_id, None)

    # -- what the server sends ----------------------------------------------------------

    def _read(self) -> None:
        decoder = Decoder()
        try:
            while True:
                chunk = self._process.read(READ_SLICE)
                if chunk is None:
                    break
                for message in decoder.feed(chunk) if chunk else []:
                    self._handle(message)
        except (LspError, ValueError) as exc:  # a broken stream cannot be resynchronised
            self._fail_all(f"a protocol error ({exc})")
            self._process.stop()
            return
        self._fail_all("the server ended")

    def _handle(self, message: dict[str, Any]) -> None:
        method, ident = message.get("method"), message.get("id")
        if method is None:  # a response
            with self._state:
                future = self._pending.get(ident) if isinstance(ident, int) else None
            if future is None or future.done():
                return
            if "error" in message:
                error = message["error"] or {}
                future.set_exception(LspError(f"{self.name}: {error.get('message', 'error')}"))
            else:
                future.set_result(message.get("result"))
        elif ident is not None:  # a request of the server: nothing here needs a real answer
            params = message.get("params") or {}
            items = params.get("items") if method == "workspace/configuration" else None
            result = [None] * len(items) if isinstance(items, list) else None
            with contextlib.suppress(LspUnavailable):
                self._send({"jsonrpc": "2.0", "id": ident, "result": result})
        elif method == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            with self._state:
                self._published[str(params.get("uri"))] = [
                    parsed for item in params.get("diagnostics") or [] if (parsed := _diag(item))
                ]
                self._state.notify_all()

    def _fail_all(self, reason: str) -> None:
        if self._cancel is not None and self._cancel.is_set():
            reason = "the run was cancelled"
        with self._state:
            self._closed = True
            self._reason = self._reason or reason
            pending, self._pending = list(self._pending.values()), {}
            self._state.notify_all()
        for future in pending:
            if not future.done():
                future.set_exception(LspUnavailable(f"{self.name} stopped: {reason}."))

    # -- documents and queries ----------------------------------------------------------

    def open(self, path: Path, text: str, language_id: str) -> str:
        """Tell the server the current text of ``path`` (opening it, or changing it)."""

        uri = path.as_uri()
        with self._state:
            known = self._versions.get(uri)
            self._published.pop(uri, None)
        if known is not None and known[1] == text:
            return uri
        version = (known[0] + 1) if known else 1
        with self._state:
            self._versions[uri] = (version, text)
        if known is None:
            document = {"uri": uri, "languageId": language_id, "version": version, "text": text}
            self._notify("textDocument/didOpen", {"textDocument": document})
        else:
            self._notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": text}],
                },
            )
        return uri

    def diagnostics(self, uri: str) -> list[Diagnostic]:
        """The diagnostics of an opened document: asked for when the server supports pulling
        them, else the ones it pushes within ``diagnostics_wait`` seconds."""

        if self._capabilities.get("diagnosticProvider"):
            result = self.request("textDocument/diagnostic", {"textDocument": {"uri": uri}})
            items = (result or {}).get("items")
            if isinstance(items, list):
                return [parsed for item in items if (parsed := _diag(item))]
        deadline = time.monotonic() + self._wait
        with self._state:
            while uri not in self._published and not self._closed:
                if self._cancel is not None and self._cancel.is_set():
                    raise LspUnavailable("The run was cancelled.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LspError(f"{self.name} sent no diagnostics in {self._wait:g}s.")
                self._state.wait(min(remaining, READ_SLICE))
            if uri not in self._published:
                raise LspUnavailable(f"{self.name} stopped: {self._reason}.")
            return list(self._published[uri])

    def definition(self, uri: str, line: int, character: int) -> list[Location]:
        result = self.request("textDocument/definition", _position(uri, line, character))
        return _locations(result)

    def references(
        self, uri: str, line: int, character: int, *, include_declaration: bool = True
    ) -> list[Location]:
        params = _position(uri, line, character)
        params["context"] = {"includeDeclaration": include_declaration}
        return _locations(self.request("textDocument/references", params))


def _position(uri: str, line: int, character: int) -> dict[str, Any]:
    return {"textDocument": {"uri": uri}, "position": {"line": line, "character": character}}


def _diag(item: Any) -> Diagnostic | None:
    try:
        start = item["range"]["start"]
        code = item.get("code")
        return Diagnostic(
            line=int(start["line"]),
            character=int(start["character"]),
            severity=SEVERITIES.get(item.get("severity"), "error"),
            message=str(item.get("message", "")),
            code=str(code.get("value") if isinstance(code, dict) else code or ""),
            source=str(item.get("source") or ""),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _locations(result: Any) -> list[Location]:
    if result is None:
        return []
    items = result if isinstance(result, list) else [result]
    found: list[Location] = []
    for item in items:
        try:
            uri = item.get("targetUri") or item["uri"]
            where = item.get("targetSelectionRange") or item["range"]
            start = where["start"]
            found.append(Location(path_of(uri), uri, int(start["line"]), int(start["character"])))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return found
