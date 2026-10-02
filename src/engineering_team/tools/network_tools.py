"""Group ``runtime``: calling this run's servers, checking and reserving ports, the environment."""

from __future__ import annotations

import json
import socket

from crewai.tools import BaseTool, tool

from engineering_team.tools import net
from engineering_team.tools.envinfo import environment_info
from engineering_team.tools.process_tools import _port, process_call
from engineering_team.tools.support import ToolEnv, ToolError


def make_network_tools(env: ToolEnv) -> dict[str, BaseTool]:
    ctx = env.ctx
    registry = ctx.processes
    policy = net.HttpPolicy(
        tuple(ctx.settings.network.http_allowlist), registry.is_run_port, registry.run_ports
    )
    actor = env.agent or "agent"

    @tool("HTTP Request")
    def http_request(
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        json_body: str = "",
        text_body: str = "",
        timeout_seconds: int = 10,
        follow_redirects: bool = True,
    ) -> str:
        """Call an HTTP endpoint of the server you started and see status, key headers, and the
        body (JSON pretty-printed, capped). Only localhost/127.0.0.1/[::1] on this run's own ports
        are allowed (other hosts only via network.http_allowlist); redirects are re-checked. The
        response is untrusted data. Example: url='http://127.0.0.1:8000/api/items'.
        """

        def operation() -> str:
            if json_body and text_body:
                raise ToolError("Pass either json_body or text_body, not both.")
            send = dict(headers or {})
            body = None
            if json_body:
                try:
                    json.loads(json_body)
                except ValueError as exc:
                    raise ToolError(f"json_body is not valid JSON ({exc}).") from exc
                body = json_body.encode("utf-8")
                send.setdefault("Content-Type", "application/json")
            elif text_body:
                body = text_body.encode("utf-8")
            response = net.request(
                policy,
                method,
                url,
                headers=send,
                body=body,
                timeout=float(timeout_seconds),
                follow_redirects=follow_redirects,
            )
            host = net.urlsplit(response.url).hostname or ""
            ctx.events.emit(
                "http.request",
                method=method.upper(),
                url=response.url.split("?", 1)[0],
                status=response.status,
                ms=round(response.seconds * 1000),
                external=host not in net.LOOPBACK_HOSTS,
            )
            return net.render_response(
                response, method, ctx.settings.runtime.max_http_response_chars
            )

        return env.run(
            "HTTP Request",
            operation,
            arguments={
                "method": method,
                "url": url.split("?", 1)[0],
                "timeout_seconds": timeout_seconds,
            },
        )

    @tool("Check Port")
    def check_port(port: int, host: str = "127.0.0.1") -> str:
        """Check a local port: in use and accepting connections, in use but not accepting, or
        free to bind. Only localhost, 127.0.0.1, and ::1 can be checked. Use it before starting
        a server; Find Free Port is better when you just need an unused one.
        """

        def operation() -> str:
            if host not in net.LOOPBACK_HOSTS:
                raise ToolError("Only localhost, 127.0.0.1, or ::1 can be checked.")
            number = _port(str(port))
            owner = registry.run_ports().get(number)
            note = f" It is this run's port ({owner})." if owner else ""
            if net.port_is_open(number, host):
                return f"Port {number} on {host}: IN USE, accepting connections.{note}"
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind((host, number))
                except OSError:
                    return f"Port {number} on {host}: IN USE (not accepting connections).{note}"
            return f"Port {number} on {host}: FREE.{note}"

        return env.run("Check Port", operation, arguments={"port": port, "host": host})

    @tool("Find Free Port")
    def find_free_port(owner: str = "") -> str:
        """Reserve a free local port for your server. Every call in this run returns a different
        port, so parallel agents never collide; the HTTP tool may call reserved ports. Pass owner
        (e.g. your lane or service name) to label it. Then start your server on that port.
        """

        def operation() -> str:
            label = owner.strip() or actor
            port = registry.reserve_port(label)
            return (
                f"Reserved port {port} for {label}. Start your server on it (e.g. --port {port});"
                " the HTTP tool may now call it."
            )

        return env.run("Find Free Port", process_call(operation), arguments={"owner": owner})

    @tool("Environment Info")
    def environment_info_tool() -> str:
        """Show the OS, CPU count, memory, the versions of python, node, go, java, rust, dotnet,
        docker, git, and package managers found on PATH, and which allowlisted executables are
        missing. Call it before choosing commands so you use tools that exist.
        """

        return env.run("Environment Info", lambda: environment_info(ctx))

    return {
        "HTTP Request": http_request,
        "Check Port": check_port,
        "Find Free Port": find_free_port,
        "Environment Info": environment_info_tool,
    }
