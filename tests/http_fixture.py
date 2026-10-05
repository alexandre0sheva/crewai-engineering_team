"""A static file server for tests that start a real server as a background process.

``python -m http.server`` is not used: ``HTTPServer.server_bind`` calls ``socket.getfqdn()``, a
reverse DNS lookup, *before* the server listens or prints anything. On a CI machine whose resolver
is slow that stalls the start for tens of seconds, and the tests would then be measuring the
network instead of the tool under test. This server prints the same banner without the lookup.
"""

from __future__ import annotations

SERVER_FILE = "serve.py"
SERVER_SCRIPT = """\
import functools
import http.server
import socketserver
import sys


class Server(http.server.ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)  # no getfqdn()
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]


port, directory = int(sys.argv[1]), sys.argv[2]
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
server = Server(("127.0.0.1", port), handler)
print(f"Serving HTTP on 127.0.0.1 port {port} (http://127.0.0.1:{port}/) ...", flush=True)
server.serve_forever()
"""


def server_command(port: int | str, directory: str = "site") -> str:
    """The command that serves ``directory`` on ``port`` (needs ``SERVER_FILE`` in the project)."""

    return f"python -u {SERVER_FILE} {port} {directory}"
