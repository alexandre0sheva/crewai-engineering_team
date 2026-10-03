"""Options every command shares, and how output is produced (human, plain, or JSON)."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, NoReturn

import typer
from rich.console import Console

EXIT_USAGE = 2


@dataclass
class Globals:
    """The global options: ``--json``, ``--quiet``, ``-v``, ``--no-color``, ``--workspace-root``."""

    json: bool = False
    quiet: bool = False
    verbose: bool = False
    no_color: bool = False
    workspace_root: str | None = None

    def console(self, *, stderr: bool = False) -> Console:
        """A Rich console. With ``--json`` stdout belongs to the JSON, so humans get stderr."""

        return Console(stderr=stderr or self.json, no_color=self.no_color, highlight=False)

    @property
    def interactive(self) -> bool:
        """Live views need a terminal that is not asked to be quiet or machine-readable."""

        return sys.stdout.isatty() and not (self.json or self.quiet)

    def overrides(self) -> dict[str, Any]:
        """Settings overrides the global options imply."""

        found: dict[str, Any] = {"verbose": self.verbose}
        if self.workspace_root:
            found["workspace_root"] = self.workspace_root
        return found


def get(ctx: typer.Context) -> Globals:
    obj = ctx.find_root().obj
    return obj if isinstance(obj, Globals) else Globals()


def one_of(choices: Sequence[str]) -> Callable[[str | None], str | None]:
    """A Typer option callback that accepts only ``choices`` (a usage error otherwise).

    Typer's own ``Choice`` type differs between releases, so the check is done here.
    """

    def check(value: str | None) -> str | None:
        if value is not None and value not in choices:
            raise typer.BadParameter(f"{value!r} is not one of {', '.join(choices)}.")
        return value

    return check


def print_json(data: Any) -> None:
    """One JSON document on stdout (the only thing a ``--json`` command writes there)."""

    sys.stdout.write(json.dumps(data, indent=2, default=str, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def fail(message: str, code: int = EXIT_USAGE) -> NoReturn:
    """Say what is wrong on stderr in one line and exit."""

    sys.stderr.write(f"Error: {message}\n")
    raise typer.Exit(code)
