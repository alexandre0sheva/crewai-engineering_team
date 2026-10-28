"""The command line: ``python -m tasktool [--file FILE] COMMAND ...``."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from tasktool.store import Store

PRIORITIES = ("low", "normal", "high")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tasktool", description="A to-do list in a JSON file.")
    parser.add_argument("--file", default="tasks.json", help="where the tasks are kept")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="add a task")
    add.add_argument("title")
    add.add_argument("--priority", choices=PRIORITIES, default="normal")
    add.add_argument("--due", type=date.fromisoformat, help="YYYY-MM-DD")
    add.add_argument("--tag", action="append", default=[], dest="tags", help="repeatable")

    show = commands.add_parser("list", help="list tasks")
    show.add_argument("--all", action="store_true", help="include finished tasks")
    show.add_argument("--tag", help="only tasks with this tag")
    show.add_argument("--sort", choices=("id", "due", "priority"), default="id")

    done = commands.add_parser("done", help="mark tasks finished")
    done.add_argument("ids", nargs="+", type=int)

    export = commands.add_parser("export", help="write the tasks as CSV")
    export.add_argument("--output", default="-", help="a file, or - for standard output")
    export.add_argument("--delimiter", default=",")
    return parser


def run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    store = Store(Path(args.file))
    if args.command == "add":
        task = store.add(args.title, args.priority, args.due, args.tags)
        store.save()
        print(f"added {task.id}: {task.title}")
    elif args.command == "list":
        rank = {"high": 0, "normal": 1, "low": 2}
        keys = {
            "id": lambda t: t.id,
            "due": lambda t: (t.due is None, t.due or "", t.id),
            "priority": lambda t: (rank[t.priority], t.id),
        }
        for task in sorted(store.tasks, key=keys[args.sort]):
            if (task.done and not args.all) or (args.tag and args.tag not in task.tags):
                continue
            mark = "x" if task.done else " "
            due = f" (due {task.due})" if task.due else ""
            print(f"[{mark}] {task.id} {task.title} [{task.priority}]{due}")
    elif args.command == "done":
        try:
            for task_id in args.ids:
                store.get(task_id).done = True
        except KeyError as exc:
            print(f"error: no task {exc.args[0]}", file=sys.stderr)
            return 2
        store.save()
    else:
        out = sys.stdout if args.output == "-" else open(args.output, "w", newline="")  # noqa: SIM115
        writer = csv.writer(out, delimiter=args.delimiter, lineterminator="\n")
        writer.writerow(["id", "title", "priority", "due", "tags", "done"])
        for t in store.tasks:
            writer.writerow([t.id, t.title, t.priority, t.due or "", " ".join(t.tags), t.done])
        if out is not sys.stdout:
            out.close()
    return 0
