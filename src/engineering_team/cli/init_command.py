"""``init``: write ``engineering-team.toml`` and a request template into the current directory."""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from engineering_team.cli.context import fail, one_of, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.intake.templates import MODES, template_for
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.pipeline.strategies import STRATEGY_NAMES

CONFIG_NAME = "engineering-team.toml"
REQUEST_NAME = "PROJECT_REQUEST.md"


def render_config(
    provider: str, profile: str, project_name: str, strategy: str, sandbox: str
) -> str:
    """The text of ``engineering-team.toml`` for these choices."""

    return f"""# engineering-team configuration (every setting: docs/CONFIGURATION.md).
provider = "{provider}"      # {" | ".join(PROVIDERS)}
profile = "{profile}"   # {" | ".join(PROFILE_NAMES)}
project_name = "{project_name}"
strategy = "{strategy}"   # {" | ".join(STRATEGY_NAMES)}

[execution]
backend = "{sandbox}"   # local | docker (docker needs Docker; see docs/SAFETY.md)
"""


def init(
    ctx: typer.Context,
    provider: Annotated[str | None, typer.Option(callback=one_of(list(PROVIDERS)))] = None,
    profile: Annotated[str | None, typer.Option(callback=one_of(list(PROFILE_NAMES)))] = None,
    project_name: Annotated[str | None, typer.Option(help="Workspace directory name.")] = None,
    strategy: Annotated[str | None, typer.Option(callback=one_of(list(STRATEGY_NAMES)))] = None,
    sandbox: Annotated[str | None, typer.Option(callback=one_of(["local", "docker"]))] = None,
    mode: Annotated[
        str | None,
        typer.Option(
            callback=one_of(list(MODES)),
            help="Kind of request template to write (default: new).",
        ),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Take defaults, do not ask.")] = False,
    force: Annotated[bool, typer.Option(help="Overwrite files that already exist.")] = False,
) -> None:
    """Create engineering-team.toml and a request template here (asks unless flags or --yes)."""

    g = get_globals(ctx)
    ask = not yes and g.interactive
    existing = [name for name in (CONFIG_NAME, REQUEST_NAME) if Path(name).exists()]
    if existing and not force:
        fail(f"{' and '.join(existing)} already exist here; pass --force to overwrite.")

    def pick(value: str | None, label: str, choices: Sequence[str], default: str) -> str:
        if value is not None:
            return value
        if not ask:
            return default
        while True:
            answer = str(typer.prompt(f"{label} [{'/'.join(choices)}]", default=default)).strip()
            if answer in choices:
                return answer
            typer.echo(f"Please answer one of: {', '.join(choices)}.", err=True)

    chosen_provider = pick(provider, "Model provider", list(PROVIDERS), "openai")
    chosen_profile = pick(profile, "Profile (quality vs cost)", list(PROFILE_NAMES), "standard")
    chosen_strategy = pick(strategy, "Strategy", list(STRATEGY_NAMES), "hierarchical")
    chosen_sandbox = pick(sandbox, "Where commands run", ["local", "docker"], "local")
    name = project_name or (
        str(typer.prompt("Project name", default="mvp-app")) if ask else "mvp-app"
    )

    text = render_config(chosen_provider, chosen_profile, name, chosen_strategy, chosen_sandbox)
    tomllib.loads(text)  # what we write must be valid TOML
    Path(CONFIG_NAME).write_text(text, encoding="utf-8")
    Path(REQUEST_NAME).write_text(template_for(mode or "new"), encoding="utf-8")
    if g.json:
        print_json({"written": [CONFIG_NAME, REQUEST_NAME], "mode": mode or "new"})
        return
    console = g.console()
    console.print(f"Wrote {CONFIG_NAME} and {REQUEST_NAME}.", markup=False)
    console.print(
        f"Next: edit {REQUEST_NAME}, then run `engineering-team new`. "
        "Check the setup with `engineering-team doctor`.",
        markup=False,
    )
