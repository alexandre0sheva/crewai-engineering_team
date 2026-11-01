"""``lessons list|show|approve|forget``: the experience library (docs/HARNESS.md#lessons)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from engineering_team.cli.context import Globals, fail, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.harness.domains import Domain
from engineering_team.learning.lessons import Lesson
from engineering_team.learning.library import (
    ENTRY_FILE,
    REPOSITORY_LIBRARY,
    LessonLibrary,
    LibraryError,
    parse_entry,
    user_library,
)
from engineering_team.learning.retrieve import covering
from engineering_team.memory.facts import one_line

lessons_app = typer.Typer(
    help="The experience library: lessons learned from verified runs (docs/HARNESS.md).",
    no_args_is_help=True,
)
RepoOpt = Annotated[
    str | None,
    typer.Option(
        "--repo", help="Use this project's library (.engineering-team/library/), not yours."
    ),
]
IdArg = Annotated[str, typer.Argument(help="A lesson's id (or its unique beginning).")]


def _library(repo: str | None) -> LessonLibrary:
    if repo is not None:
        root = Path(repo).expanduser().resolve()
        if not root.is_dir():
            fail(f"{root} is not a directory.")
        return LessonLibrary(root / REPOSITORY_LIBRARY, name="repository")
    found = user_library()
    if found is None:
        fail("There is no home directory, so there is no user library.")
    return LessonLibrary(found)


def _lesson(library: LessonLibrary, ident: str) -> tuple[Lesson, bool]:
    found = library.find(ident)
    if found is None:
        fail(f"No lesson {ident!r} in the {library.name} library; `lessons list` shows the ids.")
    folder, pending = found
    problems: list[str] = []
    entry = folder / ENTRY_FILE
    lesson = parse_entry(entry.read_text(encoding="utf-8"), str(entry), problems)
    if lesson is None:
        fail("\n".join(problems))
    return lesson, pending


@lessons_app.command("list")
def lessons_list(
    ctx: typer.Context,
    domain: Annotated[
        str | None,
        typer.Option(help="Only the lessons a run of this domain reads (bugfix/python)."),
    ] = None,
    pending: Annotated[
        bool, typer.Option("--pending", help="The lessons waiting for approval.")
    ] = False,
    repo: RepoOpt = None,
) -> None:
    """List the lessons: id, domain, confidence, component, cue."""

    g: Globals = get_globals(ctx)
    library = _library(repo)
    domains = covering(Domain.parse(domain)) if domain else None
    lessons, problems = library.load(domains, pending=pending)
    lessons.sort(key=lambda item: (item.domain, item.created))
    console = g.console()
    for problem in problems:
        console.print(f"Warning: {problem}", markup=False, soft_wrap=True, style="yellow")
    if g.json:
        print_json([item.model_dump(mode="json") for item in lessons])
        return
    if not lessons:
        what = "pending lessons" if pending else "lessons"
        console.print(f"No {what} in {library.root}.", markup=False)
        return
    for item in lessons:
        console.print(
            f"{item.id:<14}{item.domain:<22}{item.confidence:<8}{item.component:<34}"
            f"{one_line(item.cue, 90)}",
            markup=False,
            soft_wrap=True,
        )


@lessons_app.command("show")
def lessons_show(ctx: typer.Context, lesson_id: IdArg, repo: RepoOpt = None) -> None:
    """Show one lesson with the evidence it rests on."""

    g: Globals = get_globals(ctx)
    lesson, pending = _lesson(_library(repo), lesson_id)
    if g.json:
        print_json({**lesson.model_dump(mode="json"), "pending": pending})
        return
    evidence = lesson.evidence
    rows = [
        f"{lesson.id}  {lesson.domain}  confidence {lesson.confidence}"
        + ("  (pending approval)" if pending else ""),
        f"Cue: {lesson.cue}",
        f"Lesson ({lesson.component}): {lesson.lesson}",
        f"Evidence: run {evidence.run_id} ({evidence.verdict}); checks "
        f"{', '.join(evidence.check_ids) or '-'}; tree {evidence.revision[:12] or '-'}",
        *(
            f"  [{row.verdict}] {row.behaviour} -> {row.effect}"
            + (f" ({', '.join(row.evidence)})" if row.evidence else "")
            for row in evidence.rows
        ),
    ]
    for row in rows:
        g.console().print(row, markup=False, soft_wrap=True)


@lessons_app.command("approve")
def lessons_approve(ctx: typer.Context, lesson_id: IdArg, repo: RepoOpt = None) -> None:
    """Approve a pending lesson (learning.review = "manual"): later runs then receive it."""

    g: Globals = get_globals(ctx)
    try:
        lesson = _library(repo).approve(lesson_id)
    except LibraryError as exc:
        fail(str(exc))
    if g.json:
        print_json({"approved": lesson.id, "domain": lesson.domain})
    else:
        g.console().print(f"Approved {lesson.id} ({lesson.domain}).", markup=False)


@lessons_app.command("forget")
def lessons_forget(ctx: typer.Context, lesson_id: IdArg, repo: RepoOpt = None) -> None:
    """Delete one lesson (pending or not)."""

    g: Globals = get_globals(ctx)
    removed = _library(repo).remove(lesson_id)
    if removed is None:
        fail(f"No lesson {lesson_id!r}; `engineering-team lessons list` shows the ids.")
    if g.json:
        print_json({"forgotten": removed})
    else:
        g.console().print(f"Forgot {removed}.", markup=False)
