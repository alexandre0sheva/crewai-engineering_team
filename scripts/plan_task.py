#!/usr/bin/env python
"""Helper for docs/IMPLEMENTATION_PLAN_0.2.0.md.

    uv run python scripts/plan_task.py list          # index with statuses
    uv run python scripts/plan_task.py next          # first todo task whose dependencies are done
    uv run python scripts/plan_task.py show N        # rules + task N + dependency statuses
    uv run python scripts/plan_task.py show N --task-only
    uv run python scripts/plan_task.py done N        # mark task N done (today's date)

Standard library only. The plan's index table is the machine-read source of status.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PLAN = Path(__file__).resolve().parents[1] / "docs" / "IMPLEMENTATION_PLAN_0.2.0.md"
ROW = re.compile(r"^\|\s*(\d+)\s*\|(.+)\|\s*([^|]+?)\s*\|\s*$")


@dataclass
class Row:
    number: int
    title: str
    model: str
    depends: list[int]
    phase: str
    status: str


def parse_index(text: str) -> list[Row]:
    rows: list[Row] = []
    in_index = False
    for line in text.splitlines():
        if line.startswith("## 5. Task index"):
            in_index = True
            continue
        if in_index and line.startswith("## 6."):
            break
        match = ROW.match(line) if in_index else None
        if not match:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        number, title, model, depends, phase, status = cells
        if depends in {"—", "-", ""}:
            deps: list[int] = []
        elif depends == "all":
            deps = list(range(1, int(number)))
        else:
            deps = [int(item) for item in depends.split(",")]
        rows.append(Row(int(number), title, model, deps, phase, status))
    return rows


def task_section(text: str, number: int) -> str:
    start = re.search(rf"^#### Task {number} — .*$", text, re.MULTILINE)
    if not start:
        raise SystemExit(f"Task {number} not found in {PLAN}")
    boundary = r"^(#### Task \d+ — |### Phase |## 7\. |---$)"
    end = re.search(boundary, text[start.end() :], re.MULTILINE)
    stop = start.end() + end.start() if end else len(text)
    return text[start.start() : stop].rstrip() + "\n"


def rules_section(text: str) -> str:
    start = text.index("## 4. Rules for every task")
    stop = text.index("## 5. Task index")
    return text[start:stop].rstrip().removesuffix("---").rstrip() + "\n"


def is_done(row: Row) -> bool:
    return row.status.lower().startswith("done")


def cmd_list(rows: list[Row]) -> None:
    for row in rows:
        mark = "x" if is_done(row) else " "
        deps = ",".join(map(str, row.depends)) or "-"
        if len(row.depends) > 8:
            deps = "all"
        print(f"[{mark}] {row.number:>2}  {row.model}  deps:{deps:<12} {row.title}  ({row.status})")


def cmd_next(rows: list[Row]) -> None:
    done = {row.number for row in rows if is_done(row)}
    for row in rows:
        if not is_done(row) and set(row.depends) <= done:
            print(f"Next: task {row.number} — {row.title}\nRun: show {row.number}")
            return
    print("No runnable task: all done, or the remaining tasks have unfinished dependencies.")


def cmd_show(text: str, rows: list[Row], number: int, task_only: bool) -> None:
    by_number = {row.number: row for row in rows}
    if number not in by_number:
        raise SystemExit(f"Task {number} is not in the index.")
    row = by_number[number]
    print(f"Status: {row.status} · Recommended model: {row.model}\n")
    if row.depends:
        print("Dependencies:")
        for dep in row.depends:
            dep_row = by_number[dep]
            print(f"  - task {dep} ({dep_row.status}): {dep_row.title}")
        blocked = [dep for dep in row.depends if not is_done(by_number[dep])]
        if blocked:
            print(f"\nSTOP: unfinished dependencies {blocked}. Tell the user before proceeding.")
        print()
    if not task_only:
        print(rules_section(text))
    print(task_section(text, number))
    print(f"When finished: uv run python scripts/plan_task.py done {number}   (no git commit)")


def cmd_done(text: str, rows: list[Row], number: int, force: bool) -> None:
    by_number = {row.number: row for row in rows}
    row = by_number.get(number)
    if row is None:
        raise SystemExit(f"Task {number} is not in the index.")
    pending = [dep for dep in row.depends if not is_done(by_number[dep])]
    if pending and not force:
        raise SystemExit(f"Dependencies not done: {pending}. Use --force to override.")
    stamp = f"done ({date.today().isoformat()})"
    pattern = re.compile(rf"^(\|\s*{number}\s*\|.*\|\s*)([^|]+?)(\s*\|)$", re.MULTILINE)
    new_text, count = pattern.subn(lambda m: f"{m.group(1)}{stamp}{m.group(3)}", text, count=1)
    if count != 1:
        raise SystemExit("Could not update the index row.")
    PLAN.write_text(new_text, encoding="utf-8")
    print(f"Task {number} marked {stamp}.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("next")
    show = sub.add_parser("show")
    show.add_argument("number", type=int)
    show.add_argument("--task-only", action="store_true")
    done = sub.add_parser("done")
    done.add_argument("number", type=int)
    done.add_argument("--force", action="store_true")
    args = parser.parse_args()

    text = PLAN.read_text(encoding="utf-8")
    rows = parse_index(text)
    if not rows:
        sys.exit("Could not parse the task index.")
    if args.command == "list":
        cmd_list(rows)
    elif args.command == "next":
        cmd_next(rows)
    elif args.command == "show":
        cmd_show(text, rows, args.number, args.task_only)
    else:
        cmd_done(text, rows, args.number, args.force)


if __name__ == "__main__":
    main()
