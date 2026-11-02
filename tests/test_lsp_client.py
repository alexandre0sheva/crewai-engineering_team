"""The LSP client against a fake server script: framing, queries, failures, cancellation."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from engineering_team.codeintel.lsp import (
    Decoder,
    LspClient,
    LspError,
    LspUnavailable,
    frame,
    from_utf16,
    path_of,
    to_utf16,
)
from engineering_team.execution.backend import CommandSpec, ExecutionError, open_stdio
from engineering_team.execution.local import LocalBackend
from engineering_team.execution.stdio import StdioClosed

SERVER = Path(__file__).with_name("fake_lsp_server.py")
SOURCE = "def load(path):\n    return path  # BAD\n\n\nvalue = load('x')  # TODO\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text(SOURCE, "utf-8")
    return root


@pytest.fixture
def start(project: Path, tmp_path: Path) -> Iterator[Callable[..., LspClient]]:
    clients: list[LspClient] = []
    cancel = threading.Event()

    def make(
        *flags: str, timeout: float = 5.0, cancel_event: threading.Event | None = None
    ) -> LspClient:
        backend = LocalBackend(tmp_path / "logs", cancel_event or cancel)
        process = open_stdio(
            backend, CommandSpec(argv=(sys.executable, str(SERVER), *flags), cwd=project)
        )
        client = LspClient(
            process,
            project,
            name="fake-ls",
            request_timeout=timeout,
            diagnostics_wait=timeout,
            cancel_event=cancel_event or cancel,
        )
        clients.append(client)
        client.start(10.0)
        return client

    yield make
    for client in clients:
        client.close()


def test_messages_are_framed_and_decoded_across_arbitrary_chunks() -> None:
    first = frame({"jsonrpc": "2.0", "id": 1, "result": "é"})
    second = frame({"jsonrpc": "2.0", "method": "x"})
    stream = first + second
    decoder = Decoder()

    out = []
    for index in range(0, len(stream), 7):  # a byte stream, not a message stream
        out += decoder.feed(stream[index : index + 7])

    assert [m.get("result") or m.get("method") for m in out] == ["é", "x"]


def test_a_frame_without_a_usable_length_is_a_protocol_error() -> None:
    with pytest.raises(LspError):
        Decoder().feed(b"Content-Length: 0\r\n\r\n")


def test_utf16_columns_count_astral_characters_twice() -> None:
    text = "a😀b = 1"

    assert to_utf16(text, 2) == 3  # 'a' + the emoji (two units)
    assert from_utf16(text, 3) == 2
    assert from_utf16(text, 4) == 3
    assert path_of("file:///tmp/a%20b.py") == Path("/tmp/a b.py")
    assert path_of("untitled:1") is None


def test_pushed_diagnostics_arrive_after_a_document_is_opened(
    start: Callable[..., LspClient], project: Path
) -> None:
    client = start()
    uri = client.open(project / "app.py", SOURCE, "python")

    found = client.diagnostics(uri)

    assert [(d.line, d.character, d.severity, d.code) for d in found] == [
        (1, 19, "error", "bad-found"),
        (4, 21, "warning", "todo-found"),
    ]


def test_pulled_diagnostics_are_used_when_the_server_supports_them(
    start: Callable[..., LspClient], project: Path
) -> None:
    client = start("--pull")
    uri = client.open(project / "app.py", SOURCE, "python")

    assert [d.severity for d in client.diagnostics(uri)] == ["error", "warning"]


def test_changed_text_is_sent_again_and_unchanged_text_is_not(
    start: Callable[..., LspClient], project: Path
) -> None:
    client = start()
    path = project / "app.py"
    uri = client.open(path, SOURCE, "python")
    assert len(client.diagnostics(uri)) == 2

    assert client.open(path, SOURCE, "python") == uri  # same text: nothing sent
    client.open(path, SOURCE.replace("BAD", "ok").replace("TODO", "ok"), "python")

    assert client.diagnostics(uri) == []


def test_definition_and_references_use_zero_based_positions(
    start: Callable[..., LspClient], project: Path
) -> None:
    client = start()
    uri = client.open(project / "app.py", SOURCE, "python")

    (definition,) = client.definition(uri, 4, 8)  # `load` in `value = load('x')`
    uses = client.references(uri, 0, 4)

    assert (definition.line, definition.character) == (0, 4)
    assert definition.path == project / "app.py"
    assert [(u.line, u.character) for u in uses] == [(0, 4), (4, 8)]


def test_requests_of_the_server_are_answered(
    start: Callable[..., LspClient], project: Path
) -> None:
    client = start("--ask")  # the server asks for configuration and waits for no reply
    uri = client.open(project / "app.py", SOURCE, "python")

    assert client.definition(uri, 4, 8)  # the connection still works afterwards


def test_a_request_the_server_never_answers_times_out(
    start: Callable[..., LspClient], project: Path
) -> None:
    client = start("--hang", timeout=0.6)
    uri = client.open(project / "app.py", SOURCE, "python")

    started = time.monotonic()
    with pytest.raises(LspError, match="did not answer"):
        client.definition(uri, 4, 8)

    assert time.monotonic() - started < 5


def test_a_server_that_exits_makes_every_later_call_unavailable(
    start: Callable[..., LspClient], project: Path
) -> None:
    client = start("--die")
    time.sleep(0.5)

    with pytest.raises(LspUnavailable):
        client.definition(client.open(project / "app.py", SOURCE, "python"), 0, 0)


def test_a_garbled_stream_ends_the_connection(
    start: Callable[..., LspClient], project: Path
) -> None:
    with pytest.raises(LspUnavailable):  # the handshake may or may not finish first
        client = start("--garbage")
        time.sleep(0.5)
        client.request("textDocument/definition", {})


def test_cancelling_the_run_kills_the_server_and_ends_waiting_calls(
    start: Callable[..., LspClient], project: Path
) -> None:
    cancel = threading.Event()
    client = start("--hang", timeout=30, cancel_event=cancel)
    uri = client.open(project / "app.py", SOURCE, "python")
    threading.Timer(0.3, cancel.set).start()

    started = time.monotonic()
    with pytest.raises(LspUnavailable, match="cancelled"):
        client.definition(uri, 4, 8)

    assert time.monotonic() - started < 5
    deadline = time.monotonic() + 5
    while client._process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert client._process.poll() is not None  # the process is gone, not left running


def test_closing_stops_the_process(start: Callable[..., LspClient]) -> None:
    client = start()

    client.close()

    assert client._process.poll() is not None
    with pytest.raises(LspUnavailable):
        client.request("textDocument/definition", {})


def test_a_stdio_process_reports_a_command_that_does_not_exist(tmp_path: Path) -> None:
    backend = LocalBackend(tmp_path / "logs")

    with pytest.raises(OSError):
        open_stdio(backend, CommandSpec(argv=("no-such-language-server-xyz",), cwd=tmp_path))


def test_only_backends_that_can_talk_to_a_process_open_one(tmp_path: Path) -> None:
    class Plain:
        pass

    with pytest.raises(ExecutionError, match="interactive"):
        open_stdio(Plain(), CommandSpec(argv=("x",), cwd=tmp_path))


def test_writing_to_a_stopped_process_raises(tmp_path: Path) -> None:
    backend = LocalBackend(tmp_path / "logs")
    process = backend.open_stdio(
        CommandSpec(argv=(sys.executable, "-c", "import time; time.sleep(30)"), cwd=tmp_path)
    )

    process.stop(grace=1.0)

    with pytest.raises(StdioClosed):
        process.write(b"x")
    assert process.read(0.1) in (b"", None)
