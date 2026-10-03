"""Group ``runtime``: start a server, wait for it, read its logs, stop it.

Everything that runs goes through the run's :class:`ProcessRegistry` and execution backend, and
the registry stops every process when its stage or the run ends, so an agent cannot leak one.
There is deliberately no interactive shell or PTY tool: long-lived processes plus their logs cover
the real needs, and an unbounded interactive session is neither safe nor deterministic.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path

from crewai.tools import BaseTool, tool

from engineering_team.runtime.processes import ManagedProcess, ProcessError, ProcessRegistry
from engineering_team.tools import net
from engineering_team.tools.commands import prepare_command
from engineering_team.tools.support import ToolEnv, ToolError, bounded

NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
MAX_READY_TIMEOUT = 120.0
POLL = 0.1
LOG_WINDOW_BYTES = 256_000
FIRST_LOG_LINES = 15
READY_KINDS = ("", "port", "url", "log_regex", "delay")


def process_call(operation: Callable[[], str]) -> Callable[[], str]:
    """Report a refused process operation as a tool error."""

    def run() -> str:
        try:
            return operation()
        except ProcessError as exc:
            raise ToolError(str(exc)) from exc

    return run


def _port(text: str) -> int:
    value = text.strip()
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise ToolError(f"{text!r} is not a port number (1-65535).")
    return int(value)


def read_log(
    path: Path, *, tail: int = 50, since: int = -1, grep: str = "", limit: int = 8000
) -> tuple[str, int, str]:
    """(text, next offset, what was shown) from a process log, by tail, offset, or pattern."""

    try:
        size = path.stat().st_size
    except OSError:
        return "", 0, "no log yet"
    pattern = None
    if grep:
        try:
            pattern = re.compile(grep)
        except re.error as exc:
            raise ToolError(f"grep is not a valid regular expression ({exc}).") from exc
    with path.open("rb") as handle:
        if since >= 0:
            handle.seek(min(since, size))
            data = handle.read(LOG_WINDOW_BYTES)
            start = min(since, size)
            what = f"output since offset {start}"
        else:
            start = max(size - LOG_WINDOW_BYTES, 0)
            handle.seek(start)
            data = handle.read()
            what = f"last {tail} lines"
    lines = data.decode("utf-8", errors="replace").splitlines()
    if pattern is not None:
        lines = [line for line in lines if pattern.search(line)]
        what += f" matching /{grep}/"
    if since < 0:
        lines = lines[-max(1, tail) :]
    return (
        bounded("\n".join(lines), limit, hint="use tail, grep, or since_offset"),
        start + len(data),
        what,
    )


def make_process_tools(env: ToolEnv) -> dict[str, BaseTool]:
    ctx = env.ctx
    registry: ProcessRegistry = ctx.processes
    workspace = ctx.workspace
    policy = net.HttpPolicy(
        tuple(ctx.settings.network.http_allowlist), registry.is_run_port, registry.run_ports
    )
    actor = env.owner

    def describe(process: ManagedProcess) -> str:
        state = "running" if process.running else f"stopped ({process.stop_reason or 'ended'})"
        exit_text = process.record.exit_code if process.record else None
        ports = ",".join(str(p) for p in sorted(process.ports)) or "-"
        age = int(process.age(time.monotonic()))
        return (
            f"{process.id:<8} {process.name:<16} {state:<34} {age:>5}s  "
            f"exit {'-' if exit_text is None else exit_text:<4} ports {ports:<10} {process.command}"
        )

    def output_head(process: ManagedProcess, lines: int = FIRST_LOG_LINES) -> str:
        """The first output; a process that just became ready may not have flushed it yet."""

        deadline = time.monotonic() + 0.4
        while True:
            text, _, _ = read_log(process.handle.log_path, tail=lines)
            if text or time.monotonic() >= deadline or not process.running:
                return text or "(no output yet)"
            time.sleep(POLL)

    # -- readiness ---------------------------------------------------------------------

    def wait_ready(
        process: ManagedProcess, kind: str, target: str, timeout: float
    ) -> tuple[bool, str]:
        started = time.monotonic()
        deadline = started + timeout
        pattern = re.compile(target) if kind == "log_regex" else None
        delay = float(target or 0) if kind == "delay" else 0.0
        while True:
            elapsed = time.monotonic() - started
            if ctx.cancel_event.is_set():
                return False, "the run was cancelled"
            exit_code = process.handle.poll()
            if exit_code is not None:
                return False, f"the process exited with code {exit_code} before it was ready"
            if kind == "port" and net.port_is_open(int(target)):
                return True, f"port {target} accepted a connection after {elapsed:.1f}s"
            if kind == "url" and _url_ready(target):
                return True, f"{target} answered after {elapsed:.1f}s"
            if kind == "log_regex" and pattern is not None:
                text, _, _ = read_log(process.handle.log_path, tail=1000, limit=200_000)
                if pattern.search(text):
                    return True, f"the log matched /{target}/ after {elapsed:.1f}s"
            if kind == "delay" and elapsed >= delay:
                return True, f"waited {delay:g}s"
            if kind == "" and elapsed >= 0.5:
                return True, "no readiness check requested; still running after 0.5s"
            if time.monotonic() >= deadline:
                return False, f"not ready after {timeout:g}s"
            time.sleep(POLL)

    def _url_ready(url: str, expect: int = 0) -> bool:
        try:
            response = net.request(policy, "GET", url, timeout=2.0, follow_redirects=False)
        except ToolError:
            return False
        return response.status == expect if expect else response.status < 500

    # -- tools -------------------------------------------------------------------------

    @tool("Start Background Process")
    def start_background_process(
        name: str,
        command: str,
        working_directory: str = ".",
        ready_when: str = "",
        ready_target: str = "",
        ready_timeout: int = 30,
        ports: str = "",
    ) -> str:
        """Start a long-running command (dev server, watcher, database) in the background and
        wait until it is ready, without blocking forever.

        ready_when: 'port' (ready_target='8000'), 'url' (ready_target='http://127.0.0.1:8000/'),
        'log_regex' (a pattern in its output) or 'delay' (seconds); empty returns at once. Also
        list ports= it serves (comma-separated; in the Docker sandbox it must listen on 0.0.0.0).
        It is stopped when your stage or the run ends.
        """

        def operation() -> str:
            if not NAME_PATTERN.fullmatch(name):
                raise ToolError("name must be 1-40 letters, digits, '.', '_' or '-' (e.g. 'api').")
            if ready_when not in READY_KINDS:
                raise ToolError(
                    f"ready_when must be one of: {', '.join(k or '(empty)' for k in READY_KINDS)}."
                )
            target = ready_target.strip()
            if ready_when and not target:
                raise ToolError(f"ready_when='{ready_when}' needs ready_target.")
            declared = [_port(item) for item in ports.split(",") if item.strip()]
            if ready_when == "port":
                declared.append(_port(target))
            elif ready_when == "url":
                parts = net.urlsplit(target)
                if parts.hostname in net.LOOPBACK_HOSTS and parts.port:
                    declared.append(parts.port)
            elif ready_when == "log_regex":
                try:
                    re.compile(target)
                except re.error as exc:
                    raise ToolError(
                        f"ready_target is not a valid regular expression ({exc})."
                    ) from exc
            elif ready_when == "delay":
                try:
                    float(target)
                except ValueError as exc:
                    raise ToolError(
                        "ready_target must be a number of seconds for 'delay'."
                    ) from exc
            timeout = max(1.0, min(float(ready_timeout), MAX_READY_TIMEOUT))
            spec = prepare_command(workspace, command, working_directory, 300, backend=ctx.backend)
            process = registry.start(
                spec, name=name, command=command, cwd=working_directory, agent=actor, ports=declared
            )
            ready, why = wait_ready(process, ready_when, target, timeout)
            ctx.events.emit(
                "process.ready", process_id=process.id, name=name, ready=ready, detail=why
            )
            head = f"{process.id} ({name})"
            if ready:
                return f"STARTED {head}: ready - {why}.\nFirst output:\n{output_head(process)}"
            if process.running:
                return (
                    f"STARTED {head} but NOT READY: {why}. It is still running; read its output "
                    "with Read Process Logs or end it with Stop Process.\nOutput:\n"
                    f"{output_head(process)}"
                )
            record = process.handle.wait()
            return (
                f"FAILED {head}: {why}.\nOutput:\n{output_head(process, 30)}\n"
                f"(exit code {record.exit_code}; "
                f"full log: {_relative_log(process)})"
            )

        return env.run(
            "Start Background Process",
            process_call(operation),
            arguments={
                "name": name,
                "command": command,
                "ready_when": ready_when,
                "ready_target": ready_target,
            },
        )

    def _relative_log(process: ManagedProcess) -> str:
        try:
            return process.handle.log_path.relative_to(workspace.root).as_posix()
        except ValueError:
            return str(process.handle.log_path)

    @tool("List Processes")
    def list_processes() -> str:
        """List this run's background processes (running and recently stopped) with id, name,
        state, age, exit code, ports, and command. Use it to find ids and see what is still up.
        """

        def operation() -> str:
            processes = registry.processes()
            if not processes:
                return "No background processes. Start one with Start Background Process."
            running = sum(p.running for p in processes)
            stopped = len(processes) - running
            header = (
                f"{running} running, {stopped} stopped (limit {registry.max_processes} running):"
            )
            return "\n".join([header, *(describe(p) for p in processes)])

        return env.run("List Processes", process_call(operation))

    @tool("Read Process Logs")
    def read_process_logs(
        process: str, tail: int = 50, since_offset: int = -1, grep: str = ""
    ) -> str:
        """Read a background process's output: the last tail lines (default 50), only what is
        new since a byte offset from an earlier call, or only lines matching a regex (grep).
        Works after the process has stopped. Example: process='api', grep='ERROR|Traceback'.
        """

        def operation() -> str:
            managed = registry.get(process)
            text, offset, what = read_log(
                managed.handle.log_path, tail=tail, since=since_offset, grep=grep
            )
            state = "running" if managed.running else "stopped"
            return (
                f"{managed.id} ({managed.name}), {state}; {what}:\n{text or '(nothing)'}\n"
                f"next offset: {offset} (pass as since_offset for only newer output)"
            )

        return env.run(
            "Read Process Logs",
            process_call(operation),
            arguments={"process": process, "tail": tail, "grep": grep},
        )

    @tool("Stop Process")
    def stop_process(process: str) -> str:
        """Stop a background process by id or name: SIGTERM to its whole process group, then
        SIGKILL after three seconds. Returns its exit code and last output. Stop what you start
        when done; anything still running is stopped when the stage or run ends.
        """

        def operation() -> str:
            managed = registry.stop(process, "stopped by an agent")
            code = managed.record.exit_code if managed.record else "?"
            return (
                f"Stopped {managed.id} ({managed.name}); exit code {code}. Last output:\n"
                f"{output_head(managed, 10)}"
            )

        return env.run("Stop Process", process_call(operation), arguments={"process": process})

    @tool("Wait For Service")
    def wait_for_service(target: str, timeout_seconds: int = 30, expect_status: int = 0) -> str:
        """Wait until something answers: a port ('8000' or '127.0.0.1:8000') accepts a
        connection, or a URL returns a response below 500 (or exactly expect_status). Gives up
        after timeout_seconds (max 120) and says what it last saw; it never blocks forever.
        """

        def operation() -> str:
            timeout = max(1.0, min(float(timeout_seconds), MAX_READY_TIMEOUT))
            text = target.strip()
            is_url = "://" in text
            if is_url:
                policy.check(text)  # refuse a target the HTTP tool would refuse
            else:
                host, _, port_text = text.rpartition(":")
                if host and host not in net.LOOPBACK_HOSTS:
                    raise ToolError("Only localhost, 127.0.0.1, or ::1 ports can be waited for.")
                port = _port(port_text or text)
                registry.allow_port(port, "waited for")
            started = time.monotonic()
            while True:
                if ctx.cancel_event.is_set():
                    raise ToolError("The run was cancelled.")
                if is_url and _url_ready(text, expect_status):
                    return f"READY: {text} answered after {time.monotonic() - started:.1f}s."
                if not is_url and net.port_is_open(port):
                    waited = time.monotonic() - started
                    return f"READY: port {port} accepts connections after {waited:.1f}s."
                if time.monotonic() - started >= timeout:
                    return (
                        f"NOT READY: {text} did not answer within {timeout:g}s. Check List "
                        "Processes "
                        "and Read Process Logs for why the server is not up."
                    )
                time.sleep(0.2)

        return env.run("Wait For Service", process_call(operation), arguments={"target": target})

    return {
        "Start Background Process": start_background_process,
        "List Processes": list_processes,
        "Read Process Logs": read_process_logs,
        "Stop Process": stop_process,
        "Wait For Service": wait_for_service,
    }
