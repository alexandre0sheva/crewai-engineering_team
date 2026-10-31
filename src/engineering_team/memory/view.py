"""``FACTS.md``: the readable view of ``facts.jsonl`` (regenerated on every write, never read)."""

from __future__ import annotations

from collections.abc import Sequence

from engineering_team.memory.facts import Fact

HEADER = (
    "# Repo memory\n\n"
    "Facts the team verified about this repository, written by the controller. This file is a "
    "view of `facts.jsonl`; remove a fact with `engineering-team memory forget ID`, not here. "
    "Stale facts (a file they rest on changed) are never shown to the team.\n"
)


def render_facts(facts: Sequence[Fact]) -> str:
    if not facts:
        return f"{HEADER}\nNo facts yet.\n"
    lines = [HEADER]
    for status in ("verified", "proposed", "stale"):
        chosen = [f for f in facts if f.status == status]
        if not chosen:
            continue
        lines.append(f"\n## {status.capitalize()}\n")
        for fact in sorted(chosen, key=lambda f: (f.kind, f.created)):
            sources = ", ".join(f"`{s.path}`" for s in fact.sources) or "none"
            checks = ", ".join(fact.evidence.check_ids) or "-"
            lines.append(
                f"- **{fact.kind}** `{fact.id}`: {fact.text}  \n"
                f"  run {fact.evidence.run_id or '-'}, checks {checks}; sources {sources}"
            )
    return "\n".join(lines) + "\n"
