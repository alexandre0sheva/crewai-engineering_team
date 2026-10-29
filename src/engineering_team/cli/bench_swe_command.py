"""``bench import-swe``: turn SWE-bench instances from a local dataset file into benchmark tasks.

Downloading the dataset is the user's step (docs/BENCHMARKS.md); the import reads that file and
writes task directories (by default ``<suite>/tasks-swe``), which ``bench run --tasks-dir`` runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from engineering_team.bench.selection import split_values
from engineering_team.bench.swe import (
    IMAGE_TEMPLATE,
    URL_TEMPLATE,
    ImportOptions,
    import_instances,
    read_ids,
)
from engineering_team.bench.tasks import BenchError
from engineering_team.cli.bench_options import SuiteOption, suite_path
from engineering_team.cli.context import fail, one_of, print_json
from engineering_team.cli.context import get as get_globals

SWE_TASKS = "tasks-swe"


def import_swe(
    ctx: typer.Context,
    dataset: Annotated[
        str,
        typer.Option(help="A SWE-bench dataset file you downloaded (JSON Lines or a JSON list)."),
    ],
    ids: Annotated[
        list[str] | None,
        typer.Option(help="Instance ids, comma-separated or repeated (pallets__flask-4045, ...)."),
    ] = None,
    ids_file: Annotated[
        str | None,
        typer.Option(
            help="A file of instance ids, one per line (# comments), such as "
            "benchmarks/swe-starter.txt."
        ),
    ] = None,
    out: Annotated[
        str | None, typer.Option(help=f"Where the tasks go (default: <suite>/{SWE_TASKS}).")
    ] = None,
    split: Annotated[
        str, typer.Option(callback=one_of(["dev", "heldout"]), help="The tasks' split.")
    ] = "heldout",
    url_template: Annotated[
        str, typer.Option(help="Where each repository is fetched from; {repo} is owner/name.")
    ] = URL_TEMPLATE,
    image_template: Annotated[
        str,
        typer.Option(
            help="The Docker image the checks run in; {key} is the SWE-bench image key, "
            "{instance_id} the id."
        ),
    ] = IMAGE_TEMPLATE,
    force: Annotated[bool, typer.Option(help="Replace task directories that exist.")] = False,
    suite: SuiteOption = None,
) -> None:
    """Import SWE-bench instances (Python, pytest) as benchmark tasks."""

    g = get_globals(ctx)
    wanted = split_values(ids)
    out_dir = Path(out).expanduser().resolve() if out else suite_path(suite) / SWE_TASKS
    options = ImportOptions(
        split=split, url_template=url_template, image_template=image_template, force=force
    )
    try:
        if ids_file:
            wanted += read_ids(Path(ids_file).expanduser())
        result = import_instances(Path(dataset).expanduser(), wanted, out_dir, options)
    except BenchError as exc:
        fail(str(exc))
    if g.json:
        print_json(
            {
                "directory": str(out_dir),
                "imported": [
                    {"instance_id": i.instance_id, "task": i.task_id, "repo": i.repo,
                     "license": i.license, "path": str(path)}
                    for i, path in result.written
                ],
                "skipped": [{"instance_id": i, "reason": why} for i, why in result.skipped],
            }
        )  # fmt: skip
    else:
        console = g.console()
        table = Table("Task", "Repository", "License", "Tests (fail/pass)", box=None)
        for instance, _ in result.written:
            tests = f"{len(instance.fail_to_pass)}/{len(instance.pass_to_pass)}"
            table.add_row(instance.task_id, instance.repo, instance.license, tests)
        console.print(table)
        for instance_id, why in result.skipped:
            console.print(f"Skipped {instance_id}: {why}.", markup=False, soft_wrap=True)
        unknown = sorted({i.repo for i, _ in result.written if i.license == "unknown"})
        if unknown:
            console.print(
                f"Unknown license for {', '.join(unknown)}: check it before you share results.",
                markup=False,
                soft_wrap=True,
            )
        console.print(
            f"{len(result.written)} task(s) in {out_dir}. Run them with `bench run --tasks-dir "
            f"{out_dir}`; their checks need Docker and the SWE-bench images (docs/BENCHMARKS.md).",
            markup=False,
            soft_wrap=True,
        )
    if not result.written:
        fail("Nothing was imported.")
