"""Tasks kept in a JSON file."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class Task:
    id: int
    title: str
    priority: str = "normal"
    due: str | None = None
    tags: list[str] = field(default_factory=list)
    done: bool = False


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.tasks: list[Task] = []
        if path.is_file():
            self.tasks = [Task(**item) for item in json.loads(path.read_text(encoding="utf-8"))]

    def save(self) -> None:
        self.path.write_text(json.dumps([asdict(t) for t in self.tasks], indent=2), encoding="utf-8")

    def add(self, title: str, priority: str, due: date | None, tags: list[str]) -> Task:
        task = Task(
            max((t.id for t in self.tasks), default=0) + 1,
            title,
            priority,
            due.isoformat() if due else None,
            sorted(set(tags)),
        )
        self.tasks.append(task)
        return task

    def get(self, task_id: int) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)
