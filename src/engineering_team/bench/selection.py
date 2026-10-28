"""Which tasks a batch runs: ids, then the filters on ``task.yaml`` (split, intent, language...)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import get_args

from engineering_team.bench.tasks import INTENTS, BenchError, BenchTask, Difficulty, Size

SPLITS = ("all", "dev", "heldout")


def split_values(values: Sequence[str] | None) -> list[str]:
    """``["a,b", "c"]`` -> ``["a", "b", "c"]`` (repeated or comma-separated options)."""

    return [item.strip() for raw in values or [] for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class TaskFilter:
    """Every field narrows the selection; an empty one does not filter."""

    split: str = "all"
    intents: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    sizes: tuple[str, ...] = ()
    difficulties: tuple[str, ...] = ()

    def validate(self) -> None:
        problems = []
        if self.split not in SPLITS:
            problems.append(f"--split must be dev, heldout, or all, not {self.split!r}")
        for flag, chosen, known in (
            ("--intent", self.intents, INTENTS),
            ("--size", self.sizes, get_args(Size)),
            ("--difficulty", self.difficulties, get_args(Difficulty)),
        ):
            unknown = [value for value in chosen if value not in known]
            if unknown:
                problems.append(f"{flag} {', '.join(unknown)}: choose from {', '.join(known)}")
        if problems:
            raise BenchError("; ".join(problems) + ".")

    def matches(self, task: BenchTask) -> bool:
        return (
            (self.split == "all" or task.split == self.split)
            and (not self.intents or task.intent in self.intents)
            and (not self.languages or bool(set(task.languages) & set(self.languages)))
            and (not self.sizes or task.size in self.sizes)
            and (not self.difficulties or task.difficulty in self.difficulties)
        )


def select_tasks(
    tasks: list[BenchTask], names: list[str] | None, task_filter: TaskFilter | None = None
) -> list[BenchTask]:
    """The tasks a run asks for: ``names`` (ids or ``all``) that pass ``task_filter``."""

    wanted = split_values(names)
    by_id = {task.id: task for task in tasks}
    unknown = [name for name in wanted if name != "all" and name not in by_id]
    if unknown:
        raise BenchError(f"Unknown task(s): {', '.join(unknown)}. Known: {', '.join(by_id)}.")
    chosen = (
        tasks if not wanted or "all" in wanted else [by_id[name] for name in dict.fromkeys(wanted)]
    )
    task_filter = task_filter or TaskFilter()
    task_filter.validate()
    chosen = [task for task in chosen if task_filter.matches(task)]
    if not chosen:
        raise BenchError("No task matches that selection.")
    return sorted(chosen, key=lambda task: task.id)
