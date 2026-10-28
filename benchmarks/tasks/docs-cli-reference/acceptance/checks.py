import argparse
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

from engineering_team.bench.checklib import expect, main, python

FIXTURE = Path(__file__).resolve().parents[1] / "fixture"
COMMANDS = ("add", "list", "done", "export")


def parser():
    """The real parser, from the original code (the team may not change it)."""

    sys.path.insert(0, str(FIXTURE))
    from tasktool.cli import build_parser

    return build_parser()


def options(command=None):
    """``[(option, default)]`` for a command (``None``: the global options); no help options."""

    top = parser()
    target = top
    if command:
        sub = next(a for a in top._actions if isinstance(a, argparse._SubParsersAction))
        target = sub.choices[command]
    found = []
    for action in target._actions:
        if not action.option_strings or isinstance(action, argparse._HelpAction):
            continue
        default = action.default
        if isinstance(action, argparse._StoreTrueAction) or default in (None, []):
            default = None
        found.append((action.option_strings[0], default))
    return found


def document(ws):
    path = ws / "docs" / "cli.md"
    expect(path.is_file(), "there is no docs/cli.md")
    return path.read_text(encoding="utf-8", errors="replace")


def section(text, command):
    """The text from the heading naming ``command`` to the next heading of that level or above."""

    lines = text.splitlines()
    for i, line in enumerate(lines):
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading and re.search(rf"(^|[\s`]){command}([\s`]|$)", heading.group(2)):
            level = len(heading.group(1))
            end = next(
                (j for j in range(i + 1, len(lines))
                 if (h := re.match(r"^(#{1,6})\s", lines[j])) and len(h.group(1)) <= level),
                len(lines),
            )  # fmt: skip
            return "\n".join(lines[i:end])
    return None


def preamble(text):
    first = re.search(r"^#{2,6}\s", text, re.MULTILINE)
    return text[: first.start()] if first else text


def check_every_command(ws):
    text = document(ws)
    for command in COMMANDS:
        expect(section(text, command) is not None, f"no heading for the {command} command")


def check_every_option(ws):
    text = document(ws)
    expect("--file" in text, "--file is not documented")
    for command in COMMANDS:
        part = section(text, command) or ""
        for option, _ in options(command):
            expect(option in part, f"{command} {option} is not documented in the {command} section")


def check_defaults_stated(ws):
    text = document(ws)
    places = [(None, preamble(text) + (text if "--file" not in preamble(text) else ""))]
    places += [(command, section(text, command) or "") for command in COMMANDS]
    for command, part in places:
        for option, default in options(command):
            if default is None:
                continue
            at = part.find(option)
            expect(at >= 0, f"{option} is not documented")
            nearby = part[at : at + 300]
            expect(
                re.search(rf"default[^\n]*?`?{re.escape(str(default))}`?", nearby, re.IGNORECASE),
                f"{command or 'global'} {option}: the default {default!r} is not stated",
            )


def check_examples_run(ws):
    text = document(ws)
    examples = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("python -m tasktool ")
        and "[" not in line
        and "COMMAND" not in line
    ]
    expect(len(examples) >= 4, f"found {len(examples)} example command(s); give one per command")
    with tempfile.TemporaryDirectory(prefix="examples-") as scratch:
        project = Path(scratch) / "project"
        shutil.copytree(FIXTURE, project)
        for example in examples:
            argv = shlex.split(example)
            result = python(project, *argv[1:])
            expect(
                result.returncode == 0,
                f"`{example}` failed ({result.returncode}): {result.stderr[-300:]}",
            )


def check_code_unchanged(ws):
    for path in FIXTURE.rglob("*.py"):
        mine = ws / path.relative_to(FIXTURE)
        expect(
            mine.is_file() and mine.read_bytes() == path.read_bytes(),
            f"{mine.relative_to(ws)} changed",
        )
    extra = [p for p in ws.rglob("*.py") if not (FIXTURE / p.relative_to(ws)).exists()]
    expect(not extra, f"code was added: {[p.relative_to(ws).as_posix() for p in extra]}")


if __name__ == "__main__":
    main()
