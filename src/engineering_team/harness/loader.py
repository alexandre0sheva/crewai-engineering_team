"""Composing prompts from harness components (docs/HARNESS.md).

The bundled harness is ``harness/data/base/``: ``teammates/<key>/`` (``role.md`` with the
teammate's metadata as front-matter and its ``## Role``, ``## Goal`` and ``## Identity``;
``tool_guidance.md``, how it works; ``guardrails.md``, what it never does), ``stages/<name>.md``
(a stage's task prompt, ``expected_output`` in the front-matter) and ``shared/*.md`` (text several
prompts use). The order is fixed: a teammate's CrewAI backstory is identity, tool guidance,
guardrails; a stage's description is the evidence boundary (when the stage hands evidence to the
model), then the stage's text with ``{{name}}`` replaced by ``shared/<name>.md``.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any

from pydantic import ValidationError

from engineering_team.harness.files import (
    HarnessError,
    fold,
    read,
    sections,
    split_front_matter,
    unknown_fields,
)
from engineering_team.settings import TEAM_KEY, TeamOverride

BASE_LABEL = "harness/data/base"
METADATA = ("tier", "tool_groups", "allow_delegation", "max_iter", "enabled", "modes", "stages")
ROLE_SECTIONS = ("role", "goal", "identity")
COMPONENTS = ("tool_guidance", "guardrails")  # after the identity, in this order
# The always-on text of a teammate (role, goal and backstory, sent with every model call) may not
# exceed this many characters; 0.2.0's longest (code_reviewer) set it. T18 lowers it.
ALWAYS_ON_CAP = 1_075
EVIDENCE_BOUNDARY = "evidence_boundary"
# Everything a stage prompt can name; ``pipeline/stages.py`` fills each one (a test checks).
PLACEHOLDERS = frozenset(
    {
        "project_name", "requirements", "workspace_path", "current_date", "spec", "plan",
        "instructions", "triage", "repro", "fix_note", "bug", "package", "packages", "teammates",
        "context", "card", "notes", "resume_note", "failures", "findings", "base", "chunk",
        "synthesis", "profile", "upgrades", "audit", "coverage", "handoff", "brief", "lead",
    }
)  # fmt: skip
# Placeholders that carry evidence (workspace text, tool and check output, earlier answers): a
# prompt with one of them states that the evidence is data, not instructions.
EVIDENCE = frozenset(
    {
        "context", "handoff", "failures", "findings", "bug", "audit", "synthesis", "profile",
        "coverage", "triage", "repro", "fix_note",
    }
)  # fmt: skip
PLACEHOLDER = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")
INCLUDE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


@dataclass(frozen=True)
class StagePrompt:
    """A stage's task prompt as CrewAI gets it (placeholders still to be filled)."""

    key: str
    description: str
    expected_output: str
    evidence: bool  # the description starts with the evidence boundary


def base() -> Traversable:
    return resources.files("engineering_team") / "harness" / "data" / "base"


def compose_backstory(fields: dict[str, Any]) -> str:
    """The CrewAI backstory: identity, then tool guidance, then guardrails."""

    parts = (fields.get(name) or "" for name in ("identity", *COMPONENTS))
    return " ".join(" ".join(part.split()) for part in parts if part.strip())


def always_on_chars(role: str, goal: str, backstory: str) -> int:
    """What a teammate sends with every model call, in characters (``{project_name}`` unfilled)."""

    return len(role) + len(goal) + len(backstory)


# -- teammates ---------------------------------------------------------------------------------


def teammate_fields(folder: Traversable, where: str, *, complete: bool) -> dict[str, Any]:
    """The fields a teammate folder sets: metadata from ``role.md``'s front-matter, ``role``,
    ``goal``, ``identity``, and the components present. ``complete`` (a built-in teammate) needs
    ``role.md`` with every section. Raises :class:`HarnessError` listing every problem."""

    problems: list[str] = []
    fields = _teammate(folder, where, complete, problems)
    if problems:
        raise HarnessError(problems)
    return fields


def _teammate(
    folder: Traversable, where: str, complete: bool, problems: list[str]
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if not folder.is_dir():
        problems.append(f"{where}: not a directory; point it at a folder with role.md.")
        return fields
    present = {item.name for item in folder.iterdir()}
    for name in sorted(present):
        if name.endswith(".md") and name.removesuffix(".md") not in ("role", *COMPONENTS):
            problems.append(
                f"{where}/{name}: unknown component; a teammate has role.md, "
                f"{'.md, '.join(COMPONENTS)}.md."
            )
    if "role.md" in present:
        fields.update(_role(folder / "role.md", f"{where}/role.md", complete, problems))
    elif complete:
        problems.append(f"{where}/role.md: missing; every teammate needs its role, goal, identity.")
    for name in COMPONENTS:
        if f"{name}.md" not in present:
            continue
        path = f"{where}/{name}.md"
        text = read(folder / f"{name}.md", path, problems)
        if text is None:
            continue
        data, body = split_front_matter(text, path, problems)
        unknown_fields(data, (), path, problems)
        fields[name] = fold(body)
    if not fields and not problems:
        problems.append(f"{where}: no component files (role.md, {'.md, '.join(COMPONENTS)}.md).")
    return fields


def _role(path: Traversable, where: str, complete: bool, problems: list[str]) -> dict[str, Any]:
    text = read(path, where, problems)
    if text is None:
        return {}
    data, body = split_front_matter(text, where, problems)
    unknown_fields(data, METADATA, where, problems)
    fields: dict[str, Any] = {}
    known = {name: value for name, value in data.items() if name in METADATA}
    try:
        fields.update(TeamOverride.model_validate(known).model_dump(exclude_none=True))
    except ValidationError as exc:
        for error in exc.errors():
            name = ".".join(str(part) for part in error["loc"])
            problems.append(f"{where}: front-matter field '{name}': {error['msg']}.")
    first_line = text.removesuffix(body).count("\n") + 1 if body else 1
    found = sections(body, ROLE_SECTIONS, where, problems, first_line)
    for name in ROLE_SECTIONS:
        if found.get(name):
            fields[name] = found[name]
        elif complete or name in found:
            problems.append(f"{where}: the '## {name.capitalize()}' section is missing or empty.")
    return fields


@functools.cache
def builtin_teammates() -> dict[str, dict[str, Any]]:
    """Every bundled teammate's fields by key (``backstory`` composed), in a stable order."""

    problems: list[str] = []
    teammates: dict[str, dict[str, Any]] = {}
    root = base() / "teammates"
    for folder in sorted(root.iterdir(), key=lambda item: item.name):
        where = f"{BASE_LABEL}/teammates/{folder.name}"
        if not TEAM_KEY.fullmatch(folder.name):
            problems.append(f"{where}: a teammate folder is named like a key (data_engineer).")
            continue
        fields = _teammate(folder, where, True, problems)
        fields["backstory"] = compose_backstory(fields)
        teammates[folder.name] = fields
    if problems:
        raise HarnessError(problems)
    return teammates


# -- stages and shared text --------------------------------------------------------------------


def load_shared(root: Traversable, label: str) -> dict[str, str]:
    """``shared/*.md`` by name, each folded."""

    problems: list[str] = []
    shared = _shared(root, label, problems)
    if problems:
        raise HarnessError(problems)
    return shared


def _shared(root: Traversable, label: str, problems: list[str]) -> dict[str, str]:
    shared: dict[str, str] = {}
    folder = root / "shared"
    if not folder.is_dir():
        return shared
    for item in sorted(folder.iterdir(), key=lambda entry: entry.name):
        if not item.name.endswith(".md"):
            continue
        where = f"{label}/shared/{item.name}"
        text = read(item, where, problems)
        if text is None:
            continue
        data, body = split_front_matter(text, where, problems)
        unknown_fields(data, (), where, problems)
        shared[item.name.removesuffix(".md")] = body
    return shared


def load_stages(root: Traversable, label: str) -> dict[str, StagePrompt]:
    """``stages/*.md`` under ``root`` composed with its ``shared/`` text, by key. Raises
    :class:`HarnessError` listing every problem."""

    problems: list[str] = []
    shared = _shared(root, label, problems)
    if EVIDENCE_BOUNDARY not in shared:
        problems.append(f"{label}/shared/{EVIDENCE_BOUNDARY}.md: missing; stages need it.")
    stages: dict[str, StagePrompt] = {}
    folder = root / "stages"
    for item in sorted(folder.iterdir(), key=lambda entry: entry.name):
        if item.name.endswith(".md"):
            key = item.name.removesuffix(".md")
            prompt = _stage(item, key, f"{label}/stages/{item.name}", shared, problems)
            if prompt is not None:
                stages[key] = prompt
    if problems:
        raise HarnessError(problems)
    return stages


def _stage(
    path: Traversable, key: str, where: str, shared: dict[str, str], problems: list[str]
) -> StagePrompt | None:
    if not TEAM_KEY.fullmatch(key):
        problems.append(f"{where}: a stage file is named like a key (review_change.md).")
        return None
    text = read(path, where, problems)
    if text is None:
        return None
    data, body = split_front_matter(text, where, problems)
    unknown_fields(data, ("expected_output",), where, problems)
    expected = data.get("expected_output")
    if not isinstance(expected, str) or not expected.strip():
        problems.append(f"{where}: front-matter needs expected_output: what the stage returns.")
        expected = ""
    body = fold(body)
    if not body:
        problems.append(f"{where}: the task text is empty.")

    def include(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in shared or name == EVIDENCE_BOUNDARY:
            problems.append(
                f"{where}: {{{{{name}}}}} names no shared text; known: "
                f"{', '.join(sorted(set(shared) - {EVIDENCE_BOUNDARY})) or 'none'}."
            )
            return match.group(0)
        return fold(shared[name])

    body = INCLUDE.sub(include, body)
    named = set(PLACEHOLDER.findall(body))
    unknown = sorted(named - PLACEHOLDERS)
    if unknown:
        problems.append(
            f"{where}: unknown placeholder(s) {', '.join('{' + n + '}' for n in unknown)}; "
            "docs/HARNESS.md lists the known ones."
        )
    evidence = bool(named & EVIDENCE)
    if evidence and EVIDENCE_BOUNDARY in shared:
        body = f"{fold(shared[EVIDENCE_BOUNDARY])}\n{body}"
    return StagePrompt(key, f"{body}\n", expected, evidence)


@functools.cache
def stage_prompts() -> dict[str, StagePrompt]:
    """The bundled stage prompts by key."""

    return load_stages(base(), BASE_LABEL)


@functools.cache
def lead_guidance() -> tuple[str, ...]:
    """``shared/lead_guidance.md``: one item per ``- `` bullet, each on one line."""

    text = load_shared(base(), BASE_LABEL).get("lead_guidance", "")
    items: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif line.strip() and items:
            items[-1] = f"{items[-1]} {line.strip()}"
        elif line.strip():
            raise HarnessError(
                [f"{BASE_LABEL}/shared/lead_guidance.md, line {number}: start each item with '- '."]
            )
    return tuple(items)
