"""Fake agent binaries for the benchmark tests: scripts named ``claude``, ``codex``, ``opencode``
and ``aider`` in a temp directory that print what the real tool prints (its flags, its JSON) and, in
``pass`` mode, write the task's reference solution into the directory they run in. The task is
found by matching the prompt against the suite's ``request.md`` files, so one fake serves every
task. Environment: ``FAKE_SUITE`` (the suite), ``FAKE_AGENT_MODE`` (pass, empty, crash, hang),
``FAKE_AGENT_LOG`` (a file that gets one JSON line per call: argv, cwd, directory listing)."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

SCRIPT = r"""
import json, os, shutil, sys, time
from pathlib import Path

dialect = os.environ["FAKE_DIALECT"]
argv = sys.argv[1:]
if "--version" in argv:
    print(f"{dialect} 9.9.9 (fake)")
    sys.exit(0)
prompt = {
    "claude": lambda: argv[argv.index("-p") + 1],
    "codex": lambda: argv[argv.index("--") + 1],
    "opencode": lambda: argv[-1],
    "aider": lambda: argv[argv.index("--message") + 1],
}[dialect]()
cwd = Path.cwd()
if os.environ.get("FAKE_AGENT_LOG"):
    entry = {"dialect": dialect, "argv": argv, "cwd": str(cwd), "listing": sorted(os.listdir(cwd))}
    with open(os.environ["FAKE_AGENT_LOG"], "a") as log:
        log.write(json.dumps(entry) + "\n")
mode = os.environ.get("FAKE_AGENT_MODE", "pass")
if mode == "hang":
    time.sleep(60)
if mode == "crash":
    print("the fake agent crashed", file=sys.stderr)
    sys.exit(3)
tasks = Path(os.environ["FAKE_SUITE"]) / "tasks"
if mode == "pass" and tasks.is_dir():
    for task in sorted(tasks.iterdir()):
        request = task / "request.md"
        if request.is_file() and prompt.startswith(request.read_text().strip()):
            shutil.copytree(task / "reference", cwd, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
            break
if dialect == "claude":
    print(json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.42,
        "num_turns": 3, "result": "done",
        "usage": {"input_tokens": 100, "cache_read_input_tokens": 900,
                  "cache_creation_input_tokens": 0, "output_tokens": 50},
        "modelUsage": {"claude-sonnet-5-5": {}},
    }))
elif dialect == "codex":
    print(json.dumps({"type": "thread.started", "thread_id": "t"}))
    print(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 200}}))
elif dialect == "opencode":
    for _ in range(2):
        print(json.dumps({"type": "step_finish", "part": {
            "type": "step-finish", "cost": 0.03,
            "tokens": {"input": 100, "output": 20, "reasoning": 0,
                       "cache": {"read": 10, "write": 0}}}}))
else:
    print("Tokens: 1.2k sent, 300 received. Cost: $0.01 message, $0.02 session.")
sys.exit(1 if mode == "fail-exit" else 0)
"""

DIALECTS = ("claude", "codex", "opencode", "aider")


def install_fake_agents(bin_dir: Path, suite: Path, names: tuple[str, ...] = DIALECTS) -> Path:
    """Write the fake binaries into ``bin_dir`` (created); returns it, for ``PATH``."""

    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "fake_agent.py"
    script.write_text(SCRIPT)
    for name in names:
        wrapper = bin_dir / name
        wrapper.write_text(
            f'#!/bin/sh\nFAKE_DIALECT={name} FAKE_SUITE="{suite}" exec "{sys.executable}" '
            f'"{script}" "$@"\n'
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return bin_dir
