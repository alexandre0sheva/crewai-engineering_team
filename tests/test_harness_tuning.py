"""Harness tuning (T21): the Propose–Probe–Compose loop with scripted proposers and rollouts that
pass when the candidate holds the cure, so every rule can be shown without a team process."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from bench_helpers import SUITE
from typer.testing import CliRunner

from engineering_team.bench.tasks import BenchTask, load_suite
from engineering_team.cli.app import app
from engineering_team.harness.domains import Domain
from engineering_team.harness.library import Source, bundled, resolve
from engineering_team.learning.guard import Protected, leak_in
from engineering_team.learning.ledger import (
    LedgerEntry,
    LedgerError,
    TuningLedger,
    identity_problems,
)
from engineering_team.learning.tuning.compose import acceptance
from engineering_team.learning.tuning.loop import TuneConfig, Tuner, TuneResult
from engineering_team.learning.tuning.output import AdoptError, adopt, write_tuning
from engineering_team.learning.tuning.prompts import COMPOSE_PROMPT, DOM_PROMPT, INV_PROMPT
from engineering_team.learning.tuning.rollouts import WITHHELD, Rollout, protected_texts
from engineering_team.learning.tuning.tree import Candidate, Checker
from engineering_team.settings import load_settings
from engineering_team.testing import ScriptedLLM

pytestmark = pytest.mark.skipif(not (SUITE / "tasks").is_dir(), reason="no benchmarks/ directory")

DOMAIN = Domain("bugfix", "python")
CURE = "Run the reproduction before and after the fix"
SKILL = f"""---
name: repro-loop
description: Fixing a bug that a command reproduces.
teammates: [quality_engineer]
---
{CURE}; the fix is done when the reproduction passes.
"""
COMPLETION = "Finish only after the checks you ran pass; the controller runs them again.\n"
TRACE = (
    "== stage fix ==\n#1 debugger Run(cmd=python -m shop report) -> ERROR: ZeroDivisionError\n"
    "#2 debugger said (final): fixed"
)


@pytest.fixture(scope="module")
def tasks() -> dict[str, BenchTask]:
    return {t.id: t for t in load_suite(SUITE)}


def edits(*pairs: tuple[str, str]) -> str:
    return json.dumps({"edits": [{"path": p, "content": c} for p, c in pairs], "notes": "#1"})


def decide(decision: str, note: str = "#1 the base failed its reproduction") -> str:
    return json.dumps({"decision": decision, "notes": [note]})


class Router:
    """The scripted model: answers by which prompt it was sent (the last answer repeats)."""

    def __init__(self, **answers: list[str]) -> None:
        self.answers = answers
        self.seen: dict[str, list[list[dict[str, Any]]]] = {"inv": [], "dom": [], "compose": []}

    def __call__(self, messages: list[dict[str, Any]], tools: Any) -> str:
        role = {INV_PROMPT: "inv", DOM_PROMPT: "dom", COMPOSE_PROMPT: "compose"}[
            messages[0]["content"]
        ]
        self.seen[role].append(messages)
        queue = self.answers[role]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def llm(self) -> ScriptedLLM:
        return ScriptedLLM([self] * 40)

    def prompts(self) -> list[str]:
        return [str(m["content"]) for calls in self.seen.values() for c in calls for m in c]


@dataclass
class FakeRollouts:
    """A dev rollout passes when a candidate file holds ``cure`` (or the base passes); a held-out
    one passes; either fails when a file holds ``breaks`` (``held_breaks``: held-out only)."""

    cure: str = CURE
    breaks: str | None = None
    held_breaks: str | None = None
    base_passes: bool = False
    trace: str = TRACE
    calls: list[str] = field(default_factory=list)

    def __call__(self, task: BenchTask, candidate: Candidate, name: str, repeat: int) -> Rollout:
        self.calls.append(name)
        text = "\n".join(candidate.edits.values())
        passed = self.cure in text or self.base_passes
        if task.split == "heldout":
            passed = not (self.held_breaks and self.held_breaks in text)
        if self.breaks and self.breaks in text:
            passed = False
        return Rollout(
            label=name, task=task.id, repeat=repeat, outcome="passed" if passed else "failed",
            cost_usd=0.01, tokens=100, trace=self.trace, seqs=frozenset({1, 2}),
            teammates=("debugger",),
        )  # fmt: skip


def run_tuner(
    tmp_path: Path,
    tasks: dict[str, BenchTask],
    router: Router,
    rollouts: Callable[..., Rollout],
    *,
    mode: str = "parallel",
    k: int = 1,
    budget: float | None = None,
    protected: Protected | None = None,
) -> tuple[TuneResult, TuningLedger, Tuner]:
    settings = load_settings()
    guard = protected or Protected()
    checker = Checker(DOMAIN, resolve(DOMAIN), settings, guard)
    ledger = TuningLedger(tmp_path / "ledger.jsonl")
    config = TuneConfig(
        [tasks["seeded-bug"]], [tasks["ledger-misleading-trace"]], k=k, mode=mode,  # type: ignore[arg-type]
        budget_usd=budget, run_cap_usd=0.01 if budget else None,
    )  # fmt: skip
    tuner = Tuner(config, rollouts, router.llm(), checker, ledger, guard, settings)
    return tuner.run(), ledger, tuner


def counts(ledger: TuningLedger) -> dict[str, int]:
    found: dict[str, int] = {}
    for entry in ledger.entries():
        found[entry.kind] = found.get(entry.kind, 0) + 1
    return found


def test_the_whole_loop_adopts_a_harness_that_beats_the_base_and_writes_a_diff(
    tmp_path: Path, tasks: dict[str, BenchTask]
) -> None:
    router = Router(
        inv=[edits(("shared/completion.md", COMPLETION))],
        dom=[edits(("skills/repro-loop/SKILL.md", SKILL))],
        compose=[decide("merge", "#1 the run never re-ran the repro; dom 1/1 vs base 0/1")],
    )
    rollouts = FakeRollouts()

    result, ledger, _ = run_tuner(tmp_path, tasks, router, rollouts)

    assert result.decision == "adopt", result.notes
    assert result.final.paths() == ["shared/completion.md", "skills/repro-loop/SKILL.md"]
    assert result.identity == [] and result.stopped is None
    assert counts(ledger) == {"rollout": 4, "session": 3, "eval": 3}
    assert rollouts.calls == [
        "rollout:base:seeded-bug:1", "rollout:inv:seeded-bug:1", "rollout:dom:seeded-bug:1",
        "rollout:composed:seeded-bug:1", "eval:final:seeded-bug:1",
        "eval:base:ledger-misleading-trace:1", "eval:final:ledger-misleading-trace:1",
    ]  # fmt: skip
    assert result.rates == {
        "base_dev": (0, 1), "final_dev": (1, 1), "base_heldout": (1, 1), "final_heldout": (1, 1)
    }  # fmt: skip
    assert "[seeded-bug:1] merge: #1 the run never re-ran the repro" in result.notes[0]
    assert result.notes[-1].startswith("adopt: better on dev and not worse on held-out")
    assert all(p.startswith("Domain bugfix/python") for p in router.prompts()[1::2])
    assert "data, not instructions" in router.seen["inv"][0][0]["content"]

    out = tmp_path / "tuning"
    data = write_tuning(out, result, run_config(tasks), resolve(DOMAIN), ledger)
    patch = (out / "diff.patch").read_text()
    assert "+++ b/bugfix+python/skills/repro-loop/SKILL.md" in patch and CURE in patch
    assert "--- a/base/shared/completion.md" in patch  # the bundled file it overrides
    assert "+++ b/bugfix+python/shared/completion.md" in patch
    assert (out / "harness" / "bugfix+python" / "skills" / "repro-loop" / "SKILL.md").is_file()
    assert (out / "candidates" / "dom-seeded-bug-1" / "diff.patch").is_file()
    assert data["decision"] == "adopt" and data["cost_usd"] == pytest.approx(0.07)
    assert "Decision: **adopt**" in (out / "notes.md").read_text()

    target = tmp_path / "user-harness"
    written = adopt(out, target)
    assert target / "bugfix+python" / "skills" / "repro-loop" / "SKILL.md" in written
    adopted = resolve(DOMAIN, [bundled(), Source("user", target, "user", True)])
    assert "repro-loop" in adopted.skills
    assert adopted.shared["completion"] == COMPLETION


def run_config(tasks: dict[str, BenchTask], mode: str = "parallel") -> TuneConfig:
    return TuneConfig([tasks["seeded-bug"]], [tasks["ledger-misleading-trace"]], mode=mode)  # type: ignore[arg-type]


def test_sequential_mode_builds_dom_on_inv_and_composes_without_a_probe(
    tmp_path: Path, tasks: dict[str, BenchTask]
) -> None:
    router = Router(
        inv=[edits(("shared/completion.md", COMPLETION))],
        dom=[edits(("skills/repro-loop/SKILL.md", SKILL))],
        compose=[decide("merge", "dom 1/1 vs base 0/1")],
    )

    result, ledger, _ = run_tuner(tmp_path, tasks, router, FakeRollouts(), mode="sequential", k=2)

    assert result.decision == "adopt", result.notes
    assert identity_problems(ledger.entries(), ["seeded-bug"], 2, "sequential") == []
    assert counts(ledger) == {"rollout": 6, "session": 6, "eval": 6}
    slot = result.slots[0]
    assert slot.choice == "dom"  # merge means dom here: it already holds inv's files
    assert slot.candidates["dom"].paths() == ["shared/completion.md", "skills/repro-loop/SKILL.md"]
    dom_prompt = router.seen["dom"][0][1]["content"]
    assert "Already changed in this candidate" in dom_prompt and "rollout:inv:" in dom_prompt


def test_a_harness_worse_on_held_out_is_not_adopted(
    tmp_path: Path, tasks: dict[str, BenchTask]
) -> None:
    router = Router(
        inv=[edits()], dom=[edits(("skills/repro-loop/SKILL.md", SKILL))], compose=[decide("dom")]
    )

    result, _, _ = run_tuner(tmp_path, tasks, router, FakeRollouts(held_breaks="fix is done"))

    assert result.decision == "keep_base"
    assert result.notes[-1].startswith("KEEP_BASE: worse on the held-out tasks")
    assert result.rates["final_dev"] == (1, 1) and result.rates["final_heldout"] == (0, 1)


@pytest.mark.parametrize(
    ("rates", "adopted", "why"),
    [
        ({"base_dev": (0, 2), "final_dev": (1, 2), "base_heldout": (1, 2), "final_heldout": (1, 2)},
         True, "adopt"),
        ({"base_dev": (1, 2), "final_dev": (1, 2), "base_heldout": (1, 2), "final_heldout": (2, 2)},
         False, "did not rise"),
        ({"base_dev": (0, 2), "final_dev": (2, 2), "base_heldout": (2, 2), "final_heldout": (1, 2)},
         False, "worse on the held-out"),
        ({"base_dev": (0, 2), "final_dev": (2, 2)}, False, "no held-out result"),
        ({"base_dev": (0, 0), "final_dev": (1, 1)}, False, "could not be judged"),
    ],
)  # fmt: skip
def test_the_adoption_rule(rates: dict[str, tuple[int, int]], adopted: bool, why: str) -> None:
    decision, note = acceptance(rates)

    assert decision is adopted and why in note


def test_an_invalid_proposal_gets_one_retry_with_the_validators_message(
    tmp_path: Path, tasks: dict[str, BenchTask]
) -> None:
    router = Router(
        inv=[
            edits(("stages/fix.md", "Reproduce first.")),
            edits(("shared/completion.md", COMPLETION)),
            edits(),
        ],
        dom=[edits()],
        compose=[decide("inv")],
    )

    result, ledger, _ = run_tuner(tmp_path, tasks, router, FakeRollouts())

    retry = router.seen["inv"][1]
    assert retry[-1]["role"] == "user" and "stages/fix.md: not a procedure component" in str(
        retry[-1]["content"]
    )
    session = next(e for e in ledger.entries() if e.label == "session:inv:seeded-bug:1")
    assert (session.outcome, session.attempts) == ("proposed", 2)
    assert result.slots[0].candidates["inv"].paths() == ["shared/completion.md"]


def test_a_proposal_invalid_twice_inherits_the_base(
    tmp_path: Path, tasks: dict[str, BenchTask]
) -> None:
    for_everyone = SKILL.replace("teammates: [quality_engineer]\n", "").replace(
        "Fixing a bug that a command reproduces.",
        "Fixing any bug that a shell command reproduces, with a regression test written first.",
    )
    router = Router(inv=[edits()], dom=[edits(("skills/repro-loop/SKILL.md", for_everyone))],
                    compose=[decide("dom")])  # fmt: skip

    result, ledger, _ = run_tuner(tmp_path, tasks, router, FakeRollouts())

    session = next(e for e in ledger.entries() if e.label == "session:dom:seeded-bug:1")
    assert session.outcome == "invalid" and session.attempts == 2
    assert session.detail.startswith("invalid twice, so it inherits the base")
    assert "always-on text would be" in session.detail
    assert result.slots[0].candidates["dom"].empty
    assert result.decision == "keep_base"
    assert result.notes[-1] == "KEEP_BASE: no slot's choice beat the base on its own probe"


def test_proposers_never_see_hidden_checks_or_references_and_cannot_write_them(
    tmp_path: Path, tasks: dict[str, BenchTask]
) -> None:
    task = tasks["seeded-bug"]
    protected = protected_texts([task, tasks["ledger-misleading-trace"]])
    check_line = next(
        line.strip()
        for line in task.checks_file.read_text().splitlines()
        if "average_price() of an empty cart must be None" in line
    )
    reference = next(
        line.strip()
        for path in sorted(task.reference_dir.rglob("*.py"))
        for line in path.read_text().splitlines()
        if len(line.strip()) > 30
    )
    leaky = (
        f"{TRACE}\n#3 debugger Read(path=checks.py) -> OK: {check_line}\n#4 debugger: {reference}"
    )
    quoting = SKILL.replace(CURE, f"{CURE}. {reference}")
    router = Router(
        inv=[edits()], dom=[edits(("skills/repro-loop/SKILL.md", quoting))], compose=[decide("dom")]
    )
    rollouts = FakeRollouts(trace=leaky)
    from engineering_team.learning.tuning.rollouts import redact

    rollouts.trace = redact(leaky, protected)  # as BenchRollouts redacts a team's trajectory

    result, ledger, _ = run_tuner(tmp_path, tasks, router, rollouts, protected=protected)

    prompts = router.prompts()
    assert prompts and WITHHELD in "\n".join(prompts)
    for prompt in prompts:
        assert check_line not in prompt and reference not in prompt
        assert leak_in(prompt, protected) is None
    assert "Items: 0, total: 0.00, average: n/a" in prompts[1]  # the request itself is shown
    session = next(e for e in ledger.entries() if e.label == "session:dom:seeded-bug:1")
    assert session.outcome == "invalid" and "protected file" in session.detail
    assert result.decision == "keep_base"


def test_the_controller_overrules_a_composer_the_probes_do_not_support(
    tmp_path: Path, tasks: dict[str, BenchTask]
) -> None:
    breaking = SKILL.replace(CURE, "Skip the reproduction")
    router = Router(
        inv=[edits()],
        dom=[edits(("skills/repro-loop/SKILL.md", breaking))],
        compose=[decide("dom", "#2 the skill helps")],
    )
    rollouts = FakeRollouts(base_passes=True, breaks="Skip the reproduction")

    result, ledger, _ = run_tuner(tmp_path, tasks, router, rollouts)

    assert result.slots[0].choice == "keep_base"
    assert result.notes[0].startswith(
        "[seeded-bug:1] keep_base: controller: the composer chose dom, which the probes do not "
        "support; keep_base (base 1/1, inv 1/1, dom 0/1)"
    )
    assert result.decision == "keep_base" and "eval" not in counts(ledger)


def test_the_budget_stops_the_loop_and_keeps_the_base(
    tmp_path: Path, tasks: dict[str, BenchTask]
) -> None:
    router = Router(inv=[edits()], dom=[edits()], compose=[decide("keep_base")])

    result, ledger, _ = run_tuner(tmp_path, tasks, router, FakeRollouts(), budget=0.015)

    assert result.stopped == "the budget cannot pay for rollout:inv:seeded-bug:1"
    assert result.decision == "keep_base" and result.identity == []
    assert [e.label for e in ledger.entries() if e.kind == "rollout"] == [
        "rollout:base:seeded-bug:1"
    ]
    assert result.notes[-1].startswith("KEEP_BASE: stopped early")


def test_a_duplicate_label_fails_loudly_and_the_identity_names_what_is_off(tmp_path: Path) -> None:
    ledger = TuningLedger(tmp_path / "ledger.jsonl")
    entry = LedgerEntry(label="rollout:base:t:1", kind="rollout", role="base", task="t", repeat=1)
    ledger.record(entry)

    with pytest.raises(LedgerError, match="already has 'rollout:base:t:1'"):
        ledger.record(entry)
    with pytest.raises(LedgerError):
        TuningLedger(tmp_path / "ledger.jsonl").record(entry)  # also across a reload
    assert identity_problems(ledger.entries(), ["t"], 1, "parallel") == [
        "t: 1 rollouts, the identity says 4",
        "t: 0 sessions, the identity says 3",
    ]


def test_adopt_refuses_keep_base_and_a_base_that_changed(
    tmp_path: Path, tasks: dict[str, BenchTask]
) -> None:
    router = Router(
        inv=[edits()],
        dom=[edits(("skills/repro-loop/SKILL.md", SKILL))],
        compose=[decide("dom", "dom 1/1 vs base 0/1")],
    )
    result, ledger, _ = run_tuner(tmp_path, tasks, router, FakeRollouts())
    out = tmp_path / "tuning"
    write_tuning(out, result, run_config(tasks), resolve(DOMAIN), ledger)
    decision = json.loads((out / "decision.json").read_text())

    decision["base_packs"][0]["sha256"] = "000000000000"
    (out / "decision.json").write_text(json.dumps(decision))
    with pytest.raises(AdoptError, match="changed since the tuning"):
        adopt(out, tmp_path / "user")

    decision["decision"] = "keep_base"
    (out / "decision.json").write_text(json.dumps(decision))
    with pytest.raises(AdoptError, match="KEEP_BASE"):
        adopt(out, tmp_path / "user")
    assert adopt(out, tmp_path / "user", force=True)


def test_harness_adopt_and_tune_dry_run_from_the_command_line(
    tmp_path: Path, tasks: dict[str, BenchTask]
) -> None:
    router = Router(
        inv=[edits()],
        dom=[edits(("skills/repro-loop/SKILL.md", SKILL))],
        compose=[decide("dom", "dom 1/1 vs base 0/1")],
    )
    result, ledger, _ = run_tuner(tmp_path, tasks, router, FakeRollouts())
    out = tmp_path / "tuning"
    write_tuning(out, result, run_config(tasks), resolve(DOMAIN), ledger)

    adopted = CliRunner().invoke(app, ["--json", "harness", "adopt", str(out)])
    shown = CliRunner().invoke(app, ["--json", "harness", "show", "--domain", "bugfix/python"])
    planned = CliRunner().invoke(
        app,
        ["harness", "tune", "--domain", "bugfix/python", "--fake", "--dry-run",
         "--suite", str(SUITE), "--k", "2"],
    )  # fmt: skip

    assert adopted.exit_code == 0, adopted.output
    assert json.loads(adopted.stdout)["files"][0].endswith("repro-loop/SKILL.md")
    assert "repro-loop" in json.loads(shown.stdout)["skills_listed"]
    assert planned.exit_code == 0, planned.output
    assert "up to 14 rollouts and 6 sessions" in planned.output
