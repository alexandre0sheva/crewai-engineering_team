"""A tiny language server for tests, speaking LSP over stdio (no dependencies).

Behaviour: a line containing ``BAD`` is an error at that column; ``TODO`` a warning. Definition of
a word is the line ``def <word>`` in the same file; references are every whole-word occurrence in
the open documents. Flags: ``--pull`` (diagnostics are pulled, not pushed), ``--hang`` (never
answers definition/references), ``--ask`` (asks the client for its configuration first),
``--die`` (exits right after ``initialize``), ``--garbage`` (sends an invalid frame).
"""

from __future__ import annotations

import json
import re
import sys

FLAGS = set(sys.argv[1:])
documents: dict[str, str] = {}
stdin, stdout = sys.stdin.buffer, sys.stdout.buffer


def send(message: dict) -> None:
    body = json.dumps(message).encode()
    stdout.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    stdout.flush()


def read() -> dict | None:
    length = 0
    while True:
        line = stdin.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        name, _, value = line.decode().partition(":")
        if name.lower() == "content-length":
            length = int(value)
    return json.loads(stdin.read(length))


def diagnostics(text: str) -> list[dict]:
    found = []
    for number, line in enumerate(text.splitlines()):
        for marker, severity in (("BAD", 1), ("TODO", 2)):
            if marker in line:
                column = line.index(marker)
                found.append(
                    {
                        "range": {
                            "start": {"line": number, "character": column},
                            "end": {"line": number, "character": column + len(marker)},
                        },
                        "severity": severity,
                        "code": f"{marker.lower()}-found",
                        "source": "fake",
                        "message": f"{marker} found on this line",
                    }
                )
    return found


def word_at(uri: str, position: dict) -> str:
    line = documents[uri].splitlines()[position["line"]]
    for match in re.finditer(r"\w+", line):
        if match.start() <= position["character"] <= match.end():
            return match.group()
    return ""


def location(uri: str, number: int, column: int, width: int) -> dict:
    start = {"line": number, "character": column}
    end = {"line": number, "character": column + width}
    return {"uri": uri, "range": {"start": start, "end": end}}


def handle(message: dict) -> bool:
    method, ident, params = message.get("method"), message.get("id"), message.get("params") or {}
    if method == "initialize":
        capabilities: dict = {"definitionProvider": True, "referencesProvider": True}
        if "--pull" in FLAGS:
            capabilities["diagnosticProvider"] = {"interFileDependencies": False}
        send({"jsonrpc": "2.0", "id": ident, "result": {"capabilities": capabilities}})
        if "--die" in FLAGS:
            return False
        if "--garbage" in FLAGS:
            stdout.write(b"Content-Length: 5\r\n\r\n{nope")
            stdout.flush()
        if "--ask" in FLAGS:
            send({"jsonrpc": "2.0", "id": 900, "method": "workspace/configuration",
                  "params": {"items": [{"section": "a"}, {"section": "b"}]}})  # fmt: skip
    elif method in ("textDocument/didOpen", "textDocument/didChange"):
        document = params["textDocument"]
        uri = document["uri"]
        text = (
            document["text"] if method.endswith("didOpen") else params["contentChanges"][0]["text"]
        )
        documents[uri] = text
        if "--pull" not in FLAGS:
            send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                  "params": {"uri": uri, "diagnostics": diagnostics(text)}})  # fmt: skip
    elif method == "textDocument/diagnostic":
        uri = params["textDocument"]["uri"]
        result = {"kind": "full", "items": diagnostics(documents.get(uri, ""))}
        send({"jsonrpc": "2.0", "id": ident, "result": result})
    elif method in ("textDocument/definition", "textDocument/references") and "--hang" in FLAGS:
        pass
    elif method == "textDocument/definition":
        uri = params["textDocument"]["uri"]
        word = word_at(uri, params["position"])
        found = None
        for number, line in enumerate(documents[uri].splitlines()):
            if match := re.search(rf"def ({re.escape(word)})\b", line):
                found = location(uri, number, match.start(1), len(word))
                break
        send({"jsonrpc": "2.0", "id": ident, "result": found})
    elif method == "textDocument/references":
        word = word_at(params["textDocument"]["uri"], params["position"])
        found = []
        for uri, text in sorted(documents.items()):
            for number, line in enumerate(text.splitlines()):
                for match in re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", line):
                    found.append(location(uri, number, match.start(), len(word)))
        send({"jsonrpc": "2.0", "id": ident, "result": found})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": ident, "result": None})
    elif method == "exit":
        return False
    elif ident is not None and method is not None:
        send({"jsonrpc": "2.0", "id": ident, "result": None})
    return True


def main() -> None:
    while (message := read()) is not None:
        if not handle(message):
            break


if __name__ == "__main__":
    main()
