"""``memory list|show|forget|clear``: the repository's verified facts (docs/HARNESS.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from engineering_team.cli.context import Globals, fail, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.memory import FACT_KINDS, Fact, MemoryFileError, MemoryStore
from engineering_team.memory.facts import one_line

memory_app = typer.Typer(
    help="The repository's memory: verified facts the team reuses (docs/HARNESS.md).",
    no_args_is_help=True,
)
RepoOpt = Annotated[
    str | None, typer.Option("--repo", help="The project (default: the current directory).")
]
STATUSES = ("verified", "proposed", "stale")


def _store(repo: str | None) -> MemoryStore:
    root = Path(repo).expanduser().resolve() if repo else Path.cwd()
    if not root.is_dir():
        fail(f"{root} is not a directory.")
    return MemoryStore(root)


def _facts(store: MemoryStore) -> list[Fact]:
    """Every fact, with the ones whose sources changed marked stale first."""

    try:
        store.refresh()
        return store.facts()
    except MemoryFileError as exc:
        fail(str(exc))


@memory_app.command("list")
def memory_list(
    ctx: typer.Context,
    status: Annotated[str | None, typer.Option(help=f"Only {', '.join(STATUSES)}.")] = None,
    kind: Annotated[str | None, typer.Option(help=f"Only {', '.join(FACT_KINDS)}.")] = None,
    repo: RepoOpt = None,
) -> None:
    """List the facts: id, kind, status, text."""

    g: Globals = get_globals(ctx)
    if status is not None and status not in STATUSES:
        fail(f"--status must be one of: {', '.join(STATUSES)}.")
    if kind is not None and kind not in FACT_KINDS:
        fail(f"--kind must be one of: {', '.join(FACT_KINDS)}.")
    facts = [
        f for f in _facts(_store(repo)) if status in (None, f.status) and kind in (None, f.kind)
    ]
    if g.json:
        print_json([f.model_dump(mode="json") for f in facts])
        return
    console = g.console()
    if not facts:
        console.print("No facts yet. They are recorded as runs verify this repository.")
        return
    for fact in facts:
        console.print(
            f"{fact.id:<20}{fact.kind:<11}{fact.status:<9}{one_line(fact.text, 110)}",
            markup=False,
            soft_wrap=True,
        )


@memory_app.command("show")
def memory_show(
    ctx: typer.Context,
    fact_id: Annotated[str, typer.Argument(help="A fact's id (or its unique beginning).")],
    repo: RepoOpt = None,
) -> None:
    """Show one fact with its evidence and the files it rests on."""

    g: Globals = get_globals(ctx)
    store = _store(repo)
    _facts(store)
    fact = store.get(fact_id)
    if fact is None:
        fail(f"No fact {fact_id!r}; `engineering-team memory list` shows the ids.")
    if g.json:
        print_json(fact.model_dump(mode="json"))
        return
    console = g.console()
    evidence = fact.evidence
    used = f"last used {fact.last_used:%Y-%m-%d %H:%M}" if fact.last_used else "never used"
    rows = [
        f"{fact.id}  {fact.kind}  {fact.status}",
        fact.text,
        f"run: {evidence.run_id or '-'}; checks: {', '.join(evidence.check_ids) or '-'}; "
        f"tool calls (trajectory seq): {', '.join(map(str, evidence.tool_seqs)) or '-'}",
        f"observed on tree {fact.revision[:12] or '-'}; created {fact.created:%Y-%m-%d %H:%M}; "
        + used,
        *(f"source {s.path}  sha256 {s.sha256[:12]}" for s in fact.sources),
    ]
    for row in rows:
        console.print(row, markup=False, soft_wrap=True)


@memory_app.command("forget")
def memory_forget(
    ctx: typer.Context,
    fact_id: Annotated[str, typer.Argument(help="A fact's id (or its unique beginning).")],
    repo: RepoOpt = None,
) -> None:
    """Delete one fact."""

    g: Globals = get_globals(ctx)
    store = _store(repo)
    fact = store.get(fact_id)
    if fact is None:
        fail(f"No fact {fact_id!r}; `engineering-team memory list` shows the ids.")
    store.remove(fact.id, reason="forgotten by the user")
    if g.json:
        print_json({"forgotten": fact.id})
    else:
        g.console().print(f"Forgot {fact.id}: {fact.text}", markup=False, soft_wrap=True)


@memory_app.command("clear")
def memory_clear(
    ctx: typer.Context,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask.")] = False,
    repo: RepoOpt = None,
) -> None:
    """Delete every fact of the repository."""

    g: Globals = get_globals(ctx)
    store = _store(repo)
    if not yes and not typer.confirm(f"Delete all remembered facts of {store.root}?"):
        raise typer.Exit(1)
    count = store.clear()
    if g.json:
        print_json({"cleared": count})
    else:
        g.console().print(f"Cleared {count} fact(s).")
