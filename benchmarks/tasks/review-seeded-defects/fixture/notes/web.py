"""A JSON WSGI app. The user comes from the ``X-User`` header set by the auth proxy in front."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from typing import Any

from notes.service import NoteService, NotFound

Start = Callable[[str, list[tuple[str, str]]], Any]


def respond(start: Start, status: str, payload: Any) -> Iterable[bytes]:
    start(status, [("Content-Type", "application/json")])
    return [json.dumps(payload).encode()]


def as_dict(note: Any) -> dict[str, Any]:
    return {"id": note.id, "title": note.title, "body": note.body}


class App:
    def __init__(self, service: NoteService) -> None:
        self.service = service

    def __call__(self, environ: dict[str, Any], start: Start) -> Iterable[bytes]:
        user = environ.get("HTTP_X_USER", "")
        if not user:
            return respond(start, "401 Unauthorized", {"error": "sign in first"})
        method, path = environ["REQUEST_METHOD"], environ.get("PATH_INFO", "/")
        try:
            if method == "GET" and path == "/notes":
                return respond(start, "200 OK", [as_dict(n) for n in self.service.mine(user)])
            if method == "POST" and path == "/notes":
                size = int(environ.get("CONTENT_LENGTH") or 0)
                data = json.loads(environ["wsgi.input"].read(size) or b"{}")
                note = self.service.create(user, str(data.get("title", "")), str(data.get("body", "")))
                return respond(start, "201 Created", as_dict(note))
            match = re.fullmatch(r"/notes/(\d+)", path)
            if method == "GET" and match:
                return respond(start, "200 OK", as_dict(self.service.read(user, int(match[1]))))
        except NotFound:
            return respond(start, "404 Not Found", {"error": "no such note"})
        except ValueError as exc:
            return respond(start, "400 Bad Request", {"error": str(exc)})
        return respond(start, "404 Not Found", {"error": "not found"})
