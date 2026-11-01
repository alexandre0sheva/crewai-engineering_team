"""The controller's side of composing: what a composer may keep, the notes, and acceptance.

The hidden checks decide, not the composer: a candidate is kept only when it changes something
and its probe did not do worse than the base on the same task; a decision the probes do not
support is replaced by the controller's, with a note that says so. Every decision gets one notes
line citing a trajectory moment (``#seq`` of the base rollout) or the pass counts.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from engineering_team.bench.tasks import BenchTask
from engineering_team.learning.tuning.rollouts import Rollout
from engineering_team.learning.tuning.sessions import Session
from engineering_team.learning.tuning.tree import Candidate, Checker, merge

CITES = re.compile(r"#(\d+)|\b(\d+)/(\d+)\b")
EMPTY = Candidate("base", "composed")


@dataclass
class Slot:
    """One dev task and repeat: its base rollout, proposals, probes and what was chosen."""

    task: BenchTask
    repeat: int
    base: Rollout
    sessions: dict[str, Session] = field(default_factory=dict)
    candidates: dict[str, Candidate] = field(default_factory=dict)
    probes: dict[str, Rollout] = field(default_factory=dict)
    choice: str = "keep_base"
    chosen: Candidate = EMPTY
    chosen_probe: Rollout | None = None  # the probe of the chosen harness (the base's: none)

    @property
    def name(self) -> str:
        return f"{self.task.id}:{self.repeat}"

    @property
    def gain(self) -> int:
        probe = self.chosen_probe or self.base
        return int(probe.passed) - int(self.base.passed)

    def counts(self) -> str:
        pairs = [("base", self.base), *self.probes.items()]
        return ", ".join(f"{role} {int(p.passed)}/1" for role, p in pairs)


def permitted(slot: Slot, wanted: str | None, *, sequential: bool) -> tuple[str, str]:
    """``(choice, why the composer's was replaced)``: the composer's decision when the probes
    allow it, else the controller's (merge, then one candidate, then the base)."""

    ok = {
        r: not slot.candidates[r].empty and slot.probes[r].passed >= slot.base.passed
        for r in ("inv", "dom")
    }
    allowed = {
        "keep_base": True,
        "inv": ok["inv"],
        "dom": ok["dom"],
        "merge": all(ok.values()) and not sequential,  # sequential: dom already holds inv
    }
    if wanted is not None and allowed.get(wanted, False):
        return wanted, ""
    order = ("dom", "inv") if sequential else ("merge", "inv", "dom")
    fallback = next((c for c in order if allowed[c]), "keep_base")
    if wanted is None:
        return fallback, "the composer gave no decision"
    return fallback, f"the composer chose {wanted}, which the probes do not support"


def cites(slot: Slot, note: str) -> bool:
    """``note`` names a ``#seq`` of the base rollout or a pass count."""

    return any(
        (seq and int(seq) in slot.base.seqs) or (a and b) for seq, a, b in CITES.findall(note)
    )


def choice_notes(slot: Slot, notes: Iterable[str], why: str) -> list[str]:
    """The decision's notes lines: the composer's that cite their evidence, else the
    controller's with the pass counts."""

    kept = [" ".join(n.split()) for n in notes if cites(slot, n)]
    if why or not kept:
        reason = why or "no note cited a #seq or the pass counts"
        kept = [f"controller: {reason}; {slot.choice} ({slot.counts()})"]
    return [f"[{slot.name}] {slot.choice}: {note}" for note in kept]


def compose_slots(slots: Sequence[Slot], checker: Checker) -> tuple[Candidate, list[str]]:
    """One harness from the slots' choices: those that did not do worse than the base on their
    own probe, best gain first (the first to set a file keeps it). Empty when none gained."""

    kept = sorted(
        (s for s in slots if not s.chosen.empty and s.gain >= 0), key=lambda s: (-s.gain, s.name)
    )
    if not any(s.gain > 0 for s in kept):
        return EMPTY, []
    notes: list[str] = []
    owner: dict[str, str] = {}
    for slot in kept:
        for path in slot.chosen.paths():
            owner.setdefault(path, slot.name)
        dropped = [p for p in slot.chosen.paths() if owner[p] != slot.name]
        probe = slot.chosen_probe or slot.base
        line = (
            f"[compose] {slot.name}: kept its {slot.choice} choice ({int(probe.passed)}/1 vs base "
            f"{int(slot.base.passed)}/1)"
        )
        if dropped:
            line += f"; {', '.join(dropped)} already set by an earlier choice"
        notes.append(line)
    final = merge("final", [s.chosen for s in kept])
    found = checker.problems(final)
    if found:
        final = Candidate("final", "composed", kept[0].chosen.edits)
        notes.append(
            f"[compose] the union does not validate ({found[0]}); kept {kept[0].name} alone"
        )
    return final, notes


def rate(rollouts: Sequence[Rollout]) -> tuple[int, int]:
    """``(passed, counted)``: rollouts the checks could not judge are left out."""

    counted = [r for r in rollouts if r.counted]
    return sum(r.passed for r in counted), len(counted)


def acceptance(rates: dict[str, tuple[int, int]]) -> tuple[bool, str]:
    """Adopt only when the dev pass rate rises and the held-out one does not fall."""

    def show(name: str) -> str:
        passed, counted = rates.get(name, (0, 0))
        return f"{passed}/{counted}"

    def value(name: str) -> float | None:
        passed, counted = rates.get(name, (0, 0))
        return passed / counted if counted else None

    base_dev, final_dev = value("base_dev"), value("final_dev")
    base_held, final_held = value("base_heldout"), value("final_heldout")
    counts = (
        f"dev {show('final_dev')} vs base {show('base_dev')}; held-out "
        f"{show('final_heldout')} vs base {show('base_heldout')}"
    )
    if base_dev is None or final_dev is None:
        return False, f"KEEP_BASE: the dev runs could not be judged ({counts})"
    if final_dev <= base_dev:
        return False, f"KEEP_BASE: the dev pass rate did not rise ({counts})"
    if base_held is None or final_held is None:
        return False, f"KEEP_BASE: no held-out result to check it against ({counts})"
    if final_held < base_held:
        return False, f"KEEP_BASE: worse on the held-out tasks ({counts})"
    return True, f"adopt: better on dev and not worse on held-out ({counts})"
