"""``harness show`` and ``harness import``: which packs and skills a domain resolves to, and
bringing a repository's own skills and conventions into its harness (docs/HARNESS.md)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any, get_args

import typer

from engineering_team.cli.context import Globals, fail, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.contracts import Intent
from engineering_team.harness import HarnessError
from engineering_team.harness.domains import ANY, Domain, detected_stack
from engineering_team.harness.library import (
    REPOSITORY_HARNESS,
    ResolvedHarness,
    resolve_for_run,
)
from engineering_team.harness.loader import always_on_chars, compose_backstory
from engineering_team.harness.skills import NAME, SKILL_FILE, load_skill

harness_app = typer.Typer(
    help="The harness: domains, packs and skills (docs/HARNESS.md).", no_args_is_help=True
)
# Parts of a benchmark task the team must never see; nothing under them is imported. Inside a skill
# folder ``reference/`` is the Agent Skills layout's own, so only the others are refused there.
SKILL_HIDDEN = frozenset({"checks", "acceptance", "hidden"})
HIDDEN_PARTS = SKILL_HIDDEN | {"reference"}
CONVENTION_FILES = {"AGENTS.md": "agents-md", "CLAUDE.md": "claude-md"}

RepoOpt = Annotated[
    str | None, typer.Option("--repo", help="The project (default: the current directory).")
]


def _root(repo: str | None) -> Path:
    root = Path(repo).expanduser().resolve() if repo else Path.cwd()
    if not root.is_dir():
        fail(f"{root} is not a directory.")
    return root


def _domain(text: str | None, root: Path) -> Domain:
    if not text:
        return Domain(ANY, detected_stack(root))
    domain = Domain.parse(text)
    intents = get_args(Intent)
    if domain.intent not in (ANY, *intents):
        fail(f"Unknown intent {domain.intent!r} in --domain; use one of: {', '.join(intents)}.")
    if "/" in text and text.split("/", 1)[1].strip() not in ("", ANY) and domain.stack == ANY:
        fail(f"No pack knows the stack in {text!r}; there are python, typescript, go, rust, java.")
    return domain


def _resolve(domain: Domain, root: Path) -> ResolvedHarness:
    try:
        return resolve_for_run(domain, repo_root=root)
    except HarnessError as exc:
        fail(str(exc))


def _data(harness: ResolvedHarness) -> dict[str, Any]:
    sizes = {
        key: always_on_chars(f["role"], f["goal"], compose_backstory(f, harness.skill_index(key)))
        for key, f in sorted(harness.teammates.items())
        if f.get("role") and f.get("goal")
    }
    return {
        **harness.record(),
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "pack": s.pack,
                "source": s.source,
                "teammates": list(s.teammates),
            }
            for s in harness.skills.values()
        ],
        "always_on_chars": sizes,
    }


@harness_app.command("show")
def harness_show(
    ctx: typer.Context,
    domain: Annotated[
        str | None,
        typer.Option(
            "--domain", help="intent/stack, e.g. bugfix/python (default: */ the repo's stack)."
        ),
    ] = None,
    repo: RepoOpt = None,
) -> None:
    """Show what a domain resolves to: its packs (with content hashes), skills and the always-on
    prompt size of every teammate."""

    g: Globals = get_globals(ctx)
    root = _root(repo)
    harness = _resolve(_domain(domain, root), root)
    data = _data(harness)
    if g.json:
        print_json(data)
        return
    console = g.console()
    console.print(f"Domain {harness.domain.key}", style="bold", markup=False)
    for pack in harness.packs:
        console.print(f"  pack {pack.name:<16}{pack.source:<11}{pack.sha256}", markup=False)
    for skill in harness.skills.values():
        who = ", ".join(skill.teammates) or "every teammate"
        console.print(f"  skill {skill.name} ({skill.pack}, for {who})", markup=False)
        console.print(f"        {skill.description}", markup=False, style="dim")
    sizes = ", ".join(f"{k} {v:,}" for k, v in data["always_on_chars"].items())
    console.print(f"Always-on characters: {sizes}", markup=False, soft_wrap=True)


def _hidden(path: Path, parts: frozenset[str] = HIDDEN_PARTS) -> bool:
    """A path through a benchmark task's hidden part (checks, references, acceptance files)."""

    return bool(parts & {part.lower() for part in path.parts})


def _in_task(path: Path) -> bool:
    """``path`` is in a benchmark task (a ``task.yaml`` in it or in one of its parents)."""

    return any((folder / "task.yaml").is_file() for folder in [path, *path.parents][:6])


@harness_app.command("import")
def harness_import(
    ctx: typer.Context,
    source: Annotated[
        str, typer.Argument(help="A skill folder (with SKILL.md), or AGENTS.md / CLAUDE.md.")
    ],
    name: Annotated[str | None, typer.Option("--name", help="The skill's name.")] = None,
    pack: Annotated[str, typer.Option("--pack", help="The pack it joins.")] = "base",
    repo: RepoOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Replace a skill of that name.")] = False,
) -> None:
    """Import a skill, or a repository's AGENTS.md / CLAUDE.md conventions as a skill, into the
    repository's harness (.engineering-team/harness/<pack>/skills/). Repository skills are
    untrusted: they are listed by name and loaded on demand, never given more rights."""

    g = get_globals(ctx)
    root = _root(repo)
    origin = Path(source).expanduser().resolve()
    if not origin.exists():
        fail(f"{origin} does not exist.")
    if _hidden(origin) or _in_task(origin):
        fail(f"{origin} belongs to a benchmark task (its checks or references): never imported.")
    if not NAME.fullmatch(pack.replace("_", "-").replace("+", "-")):
        fail(f"--pack {pack!r} is not a pack name (base, python, bugfix, bugfix+python, ...).")
    skill_name = name or (origin.name if origin.is_dir() else CONVENTION_FILES.get(origin.name, ""))
    if not skill_name or not NAME.fullmatch(skill_name):
        fail("Give the skill a --name: lowercase letters, digits and hyphens.")
    target = root / REPOSITORY_HARNESS / pack / "skills" / skill_name
    if target.exists() and not force:
        fail(f"{target} exists; pass --force to replace it.")
    with tempfile.TemporaryDirectory() as scratch:
        staged = Path(scratch) / skill_name
        _stage(origin, staged, skill_name)
        problems: list[str] = []
        load_skill(
            staged, str(target), pack=pack, source="repository", trusted=False, problems=problems
        )
        if problems:
            fail("\n".join(problems))
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staged, target)
    try:
        resolve_for_run(Domain(), repo_root=root)
    except HarnessError as exc:
        shutil.rmtree(target)
        fail(f"The imported skill breaks the repository's harness, so it was removed:\n{exc}")
    if g.json:
        print_json({"skill": skill_name, "path": str(target)})
        return
    g.console().print(f"Imported skill {skill_name} into {target}.", markup=False)


def _stage(origin: Path, staged: Path, skill_name: str) -> None:
    if origin.is_dir():
        if not (origin / SKILL_FILE).is_file():
            fail(f"{origin} has no {SKILL_FILE}; a skill folder needs one.")
        hidden = [p for p in origin.rglob("*") if _hidden(p.relative_to(origin), SKILL_HIDDEN)]
        if hidden:
            fail(f"{origin} contains benchmark-hidden files ({hidden[0].name}): never imported.")
        shutil.copytree(origin, staged, ignore=shutil.ignore_patterns(".*"))
        return
    try:
        text = origin.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        fail(f"{origin} is not readable UTF-8 text: {exc}.")
    staged.mkdir(parents=True)
    description = f"The repository's conventions from {origin.name}: read before changing code."
    (staged / SKILL_FILE).write_text(
        f'---\nname: {skill_name}\ndescription: "{description}"\n---\n{text.strip()}\n',
        encoding="utf-8",
    )
