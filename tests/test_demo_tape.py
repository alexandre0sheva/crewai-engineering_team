"""The terminal demo (``docs/assets/demo.tape``) cannot be rendered in the gate (it needs ``vhs``
and makes a live run), so its ``engineering-team`` command lines are checked statically: every
option it types must exist on the command it calls."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from typer.testing import CliRunner

from engineering_team.cli.app import app

TAPE = Path(__file__).resolve().parent.parent / "docs" / "assets" / "demo.tape"
TYPED = re.compile(r'^Type\s+(?:"(.*)"|`(.*)`)\s*(?:Enter)?\s*$')


def typed_lines() -> list[str]:
    lines = []
    for raw in TAPE.read_text(encoding="utf-8").splitlines():
        match = TYPED.match(raw.strip())
        if match:
            lines.append(match.group(1) or match.group(2))
    return lines


def help_flags(command: str) -> set[str]:
    result = CliRunner().invoke(app, [command, "--help"], terminal_width=200)
    assert result.exit_code == 0, result.output
    return set(re.findall(r"--[a-z][a-z0-9-]*", result.output))


def test_every_option_the_demo_types_exists() -> None:
    commands = [shlex.split(line) for line in typed_lines() if line.startswith("engineering-team ")]

    assert commands, "the tape types no engineering-team command"
    for tokens in commands:
        flags = {token.split("=", 1)[0] for token in tokens if token.startswith("--")}
        missing = flags - help_flags(tokens[1])
        assert not missing, f"`{' '.join(tokens[:2])}` has no {sorted(missing)}"
