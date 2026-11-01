"""Propose–Probe–Compose: tune one domain's harness on the dev split, accept it on held-out.

For every dev task and repeat (a *slot*), the base harness is rolled out; an INV proposer
(procedure) and a DOM proposer (knowledge) read that rollout and each propose a candidate; each
candidate is probed on the same task; a composer merges them, adopts one, or keeps the base, and
the composed harness is probed (parallel mode). In sequential mode the DOM proposer works on top of
the INV candidate (when its probe did not do worse) and the composer picks among the variants
already probed. The controller then composes the slots' choices into one harness, using only
choices whose probe did not do worse than the base, and accepts it only when its pass rate on the
dev tasks is higher than the base's and on the held-out tasks not lower (hidden checks decide both);
otherwise the result is ``KEEP_BASE``. Every rollout and session is in the ledger (``ledger.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from engineering_team.bench.batch import BudgetLedger
from engineering_team.bench.tasks import BenchTask
from engineering_team.learning.ledger import (
    LedgerEntry,
    Mode,
    TuningLedger,
    identity_problems,
    label,
)
from engineering_team.learning.tuning import prompts
from engineering_team.learning.tuning.compose import (
    EMPTY,
    Slot,
    acceptance,
    choice_notes,
    compose_slots,
    permitted,
    rate,
)
from engineering_team.learning.tuning.rollouts import (
    Rollout,
    RolloutRunner,
    redact,
    request_text,
)
from engineering_team.learning.tuning.sessions import Session, compose, propose, worst_case_usd
from engineering_team.learning.tuning.tree import Candidate, Checker, merge

if TYPE_CHECKING:
    from engineering_team.learning.guard import Protected
    from engineering_team.settings import Settings


class BudgetStop(RuntimeError):
    """The tuning budget cannot pay for the next rollout or session."""


@dataclass
class TuneConfig:
    dev: list[BenchTask]
    heldout: list[BenchTask]
    k: int = 1
    mode: Mode = "parallel"
    budget_usd: float | None = None
    run_cap_usd: float | None = None
    parallel: int = 1


@dataclass
class TuneResult:
    decision: Literal["adopt", "keep_base"]
    final: Candidate
    slots: list[Slot]
    notes: list[str]
    rates: dict[str, tuple[int, int]]  # base_dev, final_dev, base_heldout, final_heldout
    stopped: str | None = None
    identity: list[str] = field(default_factory=list)


class Tuner:
    def __init__(
        self,
        config: TuneConfig,
        rollouts: RolloutRunner,
        llm: Any,
        checker: Checker,
        ledger: TuningLedger,
        protected: Protected,
        settings: Settings | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.config, self.run_rollout, self.llm = config, rollouts, llm
        self.checker, self.ledger, self.protected = checker, ledger, protected
        self.settings = settings
        self.domain = checker.domain
        self.progress = progress or (lambda line: None)
        self.budget = BudgetLedger(config.budget_usd)
        self.notes: list[str] = []

    # -- the loop ----------------------------------------------------------------------------

    def run(self) -> TuneResult:
        slots: list[Slot] = []
        rates: dict[str, tuple[int, int]] = {}
        final, stopped = EMPTY, None
        decision: Literal["adopt", "keep_base"] = "keep_base"
        try:
            jobs = [(t, r, "base", EMPTY) for t in self.config.dev for r in self._repeats()]
            slots = [Slot(t, r, b) for (t, r, _, _), b in zip(jobs, self._roll(jobs), strict=True)]
            rates["base_dev"] = rate([s.base for s in slots])
            if self.config.mode == "parallel":
                self._parallel(slots)
            else:
                self._sequential(slots)
            final, notes = compose_slots(slots, self.checker)
            self.notes += notes
            if final.empty:
                self.notes.append("KEEP_BASE: no slot's choice beat the base on its own probe")
            else:
                rates.update(self._evaluate(final))
                adopted, note = acceptance(rates)
                self.notes.append(note)
                decision = "adopt" if adopted else "keep_base"
        except BudgetStop as stop:
            stopped = str(stop)
            self.notes.append(f"KEEP_BASE: stopped early, {stopped}")
            decision = "keep_base"
        identity = []
        if stopped is None:
            tuned = [t.id for t in self.config.dev]
            identity = identity_problems(
                self.ledger.entries(), tuned, self.config.k, self.config.mode
            )
            if identity:
                self.notes.append("KEEP_BASE: the ledger identity does not hold")
                decision = "keep_base"
        return TuneResult(decision, final, slots, self.notes, rates, stopped, identity)

    def _repeats(self) -> range:
        return range(1, self.config.k + 1)

    def _parallel(self, slots: list[Slot]) -> None:
        for slot in slots:
            self._propose(slot, "inv", [slot.base])
            self._propose(slot, "dom", [slot.base])
        probes = [
            (s.task, s.repeat, role, s.candidates[role]) for s in slots for role in ("inv", "dom")
        ]
        for (_, _, role, _), probe, slot in zip(
            probes, self._roll(probes), [s for s in slots for _ in (0, 1)], strict=True
        ):
            slot.probes[role] = probe
        for slot in slots:
            self._compose(slot, sequential=False)
        jobs = [(s.task, s.repeat, "composed", s.chosen) for s in slots]
        for slot, probe in zip(slots, self._roll(jobs), strict=True):
            slot.probes["composed"] = slot.chosen_probe = probe

    def _sequential(self, slots: list[Slot]) -> None:
        for slot in slots:
            self._propose(slot, "inv", [slot.base])
            [slot.probes["inv"]] = self._roll(
                [(slot.task, slot.repeat, "inv", slot.candidates["inv"])]
            )
            inv = slot.candidates["inv"]
            start = inv if not inv.empty and slot.probes["inv"].passed >= slot.base.passed else None
            seen = [slot.base] if inv.empty else [slot.base, slot.probes["inv"]]
            self._propose(slot, "dom", seen, start)
            [slot.probes["dom"]] = self._roll(
                [(slot.task, slot.repeat, "dom", slot.candidates["dom"])]
            )
            self._compose(slot, sequential=True)

    # -- sessions ----------------------------------------------------------------------------

    def _propose(
        self,
        slot: Slot,
        role: Literal["inv", "dom"],
        seen: list[Rollout],
        start: Candidate | None = None,
    ) -> None:
        request = request_text(slot.task, self.protected)
        harness = self.checker.base
        if role == "inv":
            messages = prompts.inv_messages(self.domain, harness, slot.task.id, request, seen[0])
        else:
            messages = prompts.dom_messages(
                self.domain, harness, slot.task.id, request, seen, start
            )
        name = f"{role}:{slot.name}"

        def problems(candidate: Candidate) -> list[str]:
            found = self.checker.problems(candidate)
            if not found and start is not None:
                found = self.checker.problems(merge(name, [candidate, start]))
            return found

        session = self._session(slot, role, messages, lambda: propose(
            self.llm, messages, name, role, problems, self.settings,
            lambda text: redact(text, self.protected),
        ))  # fmt: skip
        candidate = session.candidate or Candidate(name, role)
        if start is not None:
            candidate = Candidate(name, role, merge(name, [candidate, start]).edits)
        slot.sessions[role], slot.candidates[role] = session, candidate

    def _compose(self, slot: Slot, *, sequential: bool) -> None:
        variants = [(r, slot.candidates[r], slot.probes[r]) for r in ("inv", "dom")]
        messages = prompts.compose_messages(self.domain, slot.task.id, slot.base, variants)
        if sequential:
            messages[1]["content"] += "\n\nCandidate dom already holds candidate inv's files."
        session = self._session(slot, "compose", messages, lambda: compose(
            self.llm, messages, self.settings
        ))  # fmt: skip
        slot.sessions["compose"] = session
        wanted = session.composition.decision if session.composition else None
        if sequential and wanted == "merge":
            wanted = "dom"
        choice, why = permitted(slot, wanted, sequential=sequential)
        chosen = slot.candidates.get(choice, EMPTY)
        if choice == "merge":
            chosen = merge(
                f"composed:{slot.name}", [slot.candidates["inv"], slot.candidates["dom"]]
            )
            found = self.checker.problems(chosen)
            if found:
                choice = max(("inv", "dom"), key=lambda r: slot.probes[r].passed)
                why = f"the merged files do not validate ({found[0]}); kept {choice}"
                chosen = slot.candidates[choice]
        slot.choice = choice
        slot.chosen = Candidate(f"composed:{slot.name}", "composed", chosen.edits)
        slot.chosen_probe = slot.probes.get(choice)
        notes = session.composition.notes if session.composition else []
        self.notes += choice_notes(slot, notes, why)

    def _session(
        self, slot: Slot, role: str, messages: list[dict[str, str]], call: Callable[[], Session]
    ) -> Session:
        name = label("session", role, slot.task.id, slot.repeat)
        cost = None
        if self.settings is not None:
            model = str(getattr(self.llm, "model", "") or "")
            cost = worst_case_usd(self.settings, sum(len(m["content"]) for m in messages), model)
        if not self.budget.reserve(cost):
            raise BudgetStop(f"the budget cannot pay for {name}")
        session: Session | None = None
        try:
            session = call()
        finally:
            self.budget.settle(cost, session.cost_usd if session else None)
        self.ledger.record(
            LedgerEntry(
                label=name, kind="session", role=role, task=slot.task.id, repeat=slot.repeat,
                outcome=session.outcome, cost_usd=session.cost_usd, tokens=session.tokens,
                model=session.model, attempts=session.attempts, detail=session.detail,
            )
        )  # fmt: skip
        self.progress(
            f"{name}: {session.outcome}" + (f" ({session.detail})" if session.detail else "")
        )
        return session

    # -- rollouts ----------------------------------------------------------------------------

    def _roll(
        self,
        jobs: list[tuple[BenchTask, int, str, Candidate]],
        kind: Literal["rollout", "eval"] = "rollout",
    ) -> list[Rollout]:
        """Roll out every job (``parallel`` at a time); recorded in the jobs' order."""

        names = [label(kind, role, task.id, repeat) for task, repeat, role, _ in jobs]

        def one(index: int) -> Rollout | BudgetStop:
            task, repeat, _, candidate = jobs[index]
            if not self.budget.reserve(self.config.run_cap_usd):
                return BudgetStop(f"the budget cannot pay for {names[index]}")
            rollout: Rollout | None = None
            try:
                rollout = self.run_rollout(task, candidate, names[index], repeat)
                return rollout
            finally:
                self.budget.settle(self.config.run_cap_usd, rollout.cost_usd if rollout else None)

        with ThreadPoolExecutor(max_workers=max(1, self.config.parallel)) as pool:
            done = list(pool.map(one, range(len(jobs))))
        results: list[Rollout] = []
        for (task, repeat, role, _), name, item in zip(jobs, names, done, strict=True):
            if isinstance(item, BudgetStop):
                continue
            applied = {True: "candidate applied", False: "CANDIDATE NOT APPLIED", None: ""}
            self.ledger.record(
                LedgerEntry(
                    label=name, kind=kind, role=role, task=task.id, repeat=repeat,
                    outcome=item.outcome, cost_usd=item.cost_usd, tokens=item.tokens,
                    run_dir=item.run_dir, detail=applied[item.overlay_applied],
                )
            )  # fmt: skip
            self.progress(f"{name}: {item.outcome}")
            results.append(item)
        stop = next((item for item in done if isinstance(item, BudgetStop)), None)
        if stop is not None:
            raise stop
        return results

    # -- evaluation ----------------------------------------------------------------------------

    def _evaluate(self, final: Candidate) -> dict[str, tuple[int, int]]:
        """The composed harness on every dev task, and the base and it on every held-out one."""

        dev = [(t, r, "final", final) for t in self.config.dev for r in self._repeats()]
        held = [
            (t, r, role, candidate)
            for t in self.config.heldout
            for role, candidate in (("base", EMPTY), ("final", final))
            for r in self._repeats()
        ]
        done = self._roll(dev + held, kind="eval")
        dev_runs, held_runs = done[: len(dev)], done[len(dev) :]
        roles = [role for _, _, role, _ in held]
        return {
            "final_dev": rate(dev_runs),
            "base_heldout": rate(
                [r for r, role in zip(held_runs, roles, strict=True) if role == "base"]
            ),
            "final_heldout": rate(
                [r for r, role in zip(held_runs, roles, strict=True) if role == "final"]
            ),
        }
