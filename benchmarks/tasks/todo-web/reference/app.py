import argparse
import html
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

TODOS: list[dict] = []
PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Todos</title></head><body>
<h1>Todos</h1>
<form method="post" action="/add"><input type="text" name="text"><button>Add</button></form>
<ul>{items}</ul>
</body></html>"""
ITEM = (
    '<li data-id="{id}"{cls}>{text} '
    '<form method="post" action="/toggle/{id}" style="display:inline"><button>toggle</button></form>'
    '<form method="post" action="/delete/{id}" style="display:inline"><button>delete</button></form></li>'
)


def render() -> str:
    items = "".join(
        ITEM.format(id=t["id"], cls=' class="done"' if t["done"] else "", text=html.escape(t["text"]))
        for t in TODOS
    )
    return PAGE.format(items=items)


def add(text) -> dict | None:
    if not isinstance(text, str) or not text.strip():
        return None
    todo = {"id": max((t["id"] for t in TODOS), default=0) + 1, "text": text.strip(), "done": False}
    TODOS.append(todo)
    return todo


def find(todo_id: int) -> dict | None:
    return next((t for t in TODOS if t["id"] == todo_id), None)


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, body: bytes = b"", kind: str = "text/plain", location: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def json(self, status: int, payload):
        self.reply(status, json.dumps(payload).encode(), "application/json")

    def body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self):
        if self.path == "/":
            self.reply(200, render().encode(), "text/html; charset=utf-8")
        elif self.path == "/api/todos":
            self.json(200, TODOS)
        else:
            self.reply(404, b"not found")

    def do_POST(self):
        data = self.body()
        if self.path == "/api/todos":
            try:
                payload = json.loads(data or b"null")
            except ValueError:
                payload = None
            todo = add(payload.get("text") if isinstance(payload, dict) else None)
            return self.json(201, todo) if todo else self.json(400, {"error": "text is required"})
        if self.path == "/add":
            add(parse_qs(data.decode()).get("text", [""])[0])
            return self.reply(303, location="/")
        match = re.fullmatch(r"/(toggle|delete)/(\d+)", self.path)
        todo = find(int(match.group(2))) if match else None
        if todo is None:
            return self.reply(404, b"not found")
        if match.group(1) == "toggle":
            todo['done'] = not todo['done']
        else:
            TODOS.remove(todo)
        self.reply(303, location="/")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    ThreadingHTTPServer(("127.0.0.1", parser.parse_args().port), Handler).serve_forever()
