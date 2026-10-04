import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from shortener.store import Store


def valid_url(value) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.hostname) and " " not in value


def make_handler(store: Store, port: int):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/shorten":
                return self.send_json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"null")
            except ValueError:
                return self.send_json(400, {"error": "the body must be JSON"})
            url = body.get("url") if isinstance(body, dict) else None
            if not valid_url(url):
                return self.send_json(400, {"error": "url must be an absolute http(s) URL"})
            code, created = store.shorten(url)
            self.send_json(
                201 if created else 200,
                {"code": code, "url": url, "short_url": f"http://127.0.0.1:{port}/{code}"},
            )

        def do_GET(self) -> None:
            if self.path.startswith("/stats/"):
                stats = store.stats(self.path[len("/stats/"):])
                return self.send_json(200, stats) if stats else self.send_json(404, {"error": "unknown code"})
            url = store.visit(self.path.lstrip("/")) if self.path.count("/") == 1 else None
            if url is None:
                return self.send_json(404, {"error": "unknown code"})
            self.send_response(302)
            self.send_header("Location", url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(prog="shortener")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--db", default=":memory:")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Store(args.db), args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
