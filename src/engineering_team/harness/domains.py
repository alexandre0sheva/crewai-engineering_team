"""Domains: which packs of the harness library a run uses (docs/HARNESS.md#domains).

A domain is ``<intent>/<stack>`` (``bugfix/python``); either part may be ``*`` (unknown, or no
pack for it). Its packs, least specific first, are ``base``, the stack's pack (``python``), the
intent's pack (``bugfix``) and the pair's pack (``bugfix+python``); each is a partial harness
tree, and a pack that does not exist is skipped, so ``bugfix/python`` falls back through
``bugfix/*`` and ``*/python`` to ``base``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engineering_team.contracts import TaskProfile
    from engineering_team.pipeline.recipes import Recipe

ANY = "*"
BASE = "base"
# Stack names as triage, the repository profile and people write them, to the pack's name.
STACKS: dict[str, str] = {
    "python": "python", "py": "python", "django": "python", "flask": "python",
    "fastapi": "python",
    "typescript": "typescript", "ts": "typescript", "javascript": "typescript", "js": "typescript",
    "node": "typescript", "nodejs": "typescript", "react": "typescript", "vue": "typescript",
    "go": "go", "golang": "go",
    "rust": "rust", "cargo": "rust",
    "java": "java", "kotlin": "java", "maven": "java", "gradle": "java", "spring": "java",
}  # fmt: skip
# A run without a triage profile: the intent its recipe (or mode) stands for.
RECIPE_INTENTS: dict[str, str] = {
    "new": "new_project", "feature": "feature", "fix": "bugfix", "refactor": "refactor",
    "add-tests": "tests", "docs": "docs", "upgrade-deps": "upgrade_deps",
    "security-audit": "security_audit", "review": "review", "adopt": "question",
    "build": "new_project", "answer": "question",  # the run modes of `new` and the answer path
}  # fmt: skip


@dataclass(frozen=True)
class Domain:
    intent: str = ANY
    stack: str = ANY

    @property
    def key(self) -> str:
        return f"{self.intent}/{self.stack}"

    def packs(self) -> tuple[str, ...]:
        """Pack names, least specific first (``base`` always)."""

        names = [BASE]
        if self.stack != ANY:
            names.append(self.stack)
        if self.intent != ANY:
            names.append(self.intent)
            if self.stack != ANY:
                names.append(f"{self.intent}+{self.stack}")
        return tuple(names)

    @classmethod
    def parse(cls, key: str) -> Domain:
        intent, _, stack = key.partition("/")
        return cls(intent.strip() or ANY, normal_stack([stack]) if stack.strip() else ANY)


def normal_stack(names: Iterable[str]) -> str:
    """The first stack among ``names`` that has a pack name (``python/uv`` -> ``python``)."""

    for name in names:
        for part in str(name).lower().replace("\\", "/").split("/"):
            found = STACKS.get(part.strip())
            if found:
                return found
    return ANY


def detected_stack(root: Path | None) -> str:
    """The stack of the project at ``root``, from its manifests (``*`` when none is known)."""

    if root is None or not root.is_dir():
        return ANY
    from engineering_team.devtools.detect import find_stacks

    try:
        return normal_stack(stack.language for stack in find_stacks(root, depth=2))
    except OSError:
        return ANY


def domain_for(
    profile: TaskProfile | None = None,
    *,
    mode: str | None = None,
    recipe: Recipe | None = None,
    root: Path | None = None,
) -> Domain:
    """The domain of a run: the triage profile's primary intent and stack when there is one,
    else the intent the recipe (or the mode) stands for; the stack falls back to the project's
    manifests."""

    intent: str | None = profile.primary if profile is not None else None
    if intent is None and recipe is not None:
        intent = RECIPE_INTENTS.get(recipe.name) or RECIPE_INTENTS.get(recipe.mode or "")
    if intent is None and mode:
        intent = RECIPE_INTENTS.get(mode)
    stack = normal_stack(profile.stack) if profile is not None else ANY
    if stack == ANY:
        stack = detected_stack(root)
    return Domain(intent or ANY, stack)
