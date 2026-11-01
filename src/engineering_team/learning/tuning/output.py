"""The tuning directory, ``benchmarks/tuning/<date>/<intent>/<stack>/``, and ``harness adopt``.

It holds ``decision.json`` (the decision, pass rates, notes, the base packs' content hashes and
the cost), ``notes.md`` (one line per decision), ``diff.patch`` and ``harness/<pack>/...`` (the
composed harness), ``candidates/<label>/diff.patch`` (every proposal, for review) and
``ledger.jsonl``. ``harness adopt`` copies ``harness/`` into the user's harness or the bundled
library, and only when the decision was ``adopt`` and the base has not changed since.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from engineering_team.atomic_io import atomic_write_json
from engineering_team.harness import HarnessError
from engineering_team.harness.domains import Domain
from engineering_team.harness.library import ResolvedHarness, Source, bundled, resolve
from engineering_team.learning.ledger import LEDGER_FILE, TuningLedger
from engineering_team.learning.library import domain_folder
from engineering_team.learning.tuning.loop import TuneConfig, TuneResult
from engineering_team.learning.tuning.tree import Candidate, diff, pack_of, write_overlay

DECISION_FILE = "decision.json"
NOTES_FILE = "notes.md"
DIFF_FILE = "diff.patch"
HARNESS_DIR = "harness"
CANDIDATES_DIR = "candidates"


class AdoptError(ValueError):
    """A tuning directory that cannot be adopted; the message says why and what to do."""


def default_directory(suite: Path, domain: Domain, day: date | None = None) -> Path:
    day = day or datetime.now(UTC).date()
    return suite / "tuning" / day.isoformat() / domain_folder(domain.key)


def write_tuning(
    out: Path,
    result: TuneResult,
    config: TuneConfig,
    base: ResolvedHarness,
    ledger: TuningLedger,
    **extra: Any,
) -> dict[str, Any]:
    """Write everything but the ledger (written as it ran); returns ``decision.json``'s data."""

    domain = base.domain
    out.mkdir(parents=True, exist_ok=True)
    if not result.final.empty:
        write_overlay(result.final, pack_of(domain), out / HARNESS_DIR)
        (out / DIFF_FILE).write_text(diff(result.final, domain), encoding="utf-8")
    seen: list[Candidate] = []
    for slot in result.slots:
        seen += [c for c in (*slot.candidates.values(), slot.chosen) if not c.empty]
    for candidate in seen:
        folder = out / CANDIDATES_DIR / candidate.label.replace(":", "-")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / DIFF_FILE).write_text(diff(candidate, domain), encoding="utf-8")
    cost, unknown = ledger.cost()
    data: dict[str, Any] = {
        "schema_version": 1,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "domain": domain.key,
        "pack": pack_of(domain),
        "mode": config.mode,
        "k": config.k,
        "dev_tasks": [t.id for t in config.dev],
        "heldout_tasks": [t.id for t in config.heldout],
        "decision": result.decision,
        "files": result.final.paths(),
        "rates": {name: {"passed": p, "counted": c} for name, (p, c) in result.rates.items()},
        "notes": result.notes,
        "stopped": result.stopped,
        "identity_problems": result.identity,
        "cost_usd": cost,
        "unknown_cost_entries": unknown,
        "base_packs": [{"name": p.name, "sha256": p.sha256} for p in base.packs],
        "ledger": LEDGER_FILE,
        **extra,
    }
    atomic_write_json(out / DECISION_FILE, data)
    lines = [
        f"# Harness tuning: {domain.key} ({config.mode}, k={config.k})",
        "",
        f"Decision: **{'adopt' if result.decision == 'adopt' else 'KEEP_BASE'}**. "
        f"Cost ${cost:.4f}" + (f" ({unknown} unknown)" if unknown else "") + ".",
        "",
        *(f"- {note}" for note in result.notes),
    ]
    (out / NOTES_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return data


def read_decision(folder: Path) -> dict[str, Any]:
    try:
        data = json.loads((folder / DECISION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdoptError(f"{folder} has no readable {DECISION_FILE}: {exc}") from None
    if not isinstance(data, dict) or "domain" not in data:
        raise AdoptError(f"{folder / DECISION_FILE} is not a tuning decision.")
    return data


def adopt(folder: Path, target: Path, *, force: bool = False) -> list[Path]:
    """Copy ``folder/harness/`` into ``target`` (a harness library root); returns the files
    written. Refuses a ``KEEP_BASE`` decision or a base that changed, unless ``force``."""

    data = read_decision(folder)
    source = folder / HARNESS_DIR
    if not source.is_dir():
        raise AdoptError(f"{folder} has no {HARNESS_DIR}/: the tuning composed no change.")
    if data.get("decision") != "adopt" and not force:
        raise AdoptError(
            f"The tuning in {folder} decided KEEP_BASE ({_last_note(data)}); pass --force to "
            "adopt its harness anyway."
        )
    domain = Domain.parse(str(data["domain"]))
    now = {p.name: p.sha256 for p in resolve(domain).packs}
    then = {str(p.get("name")): str(p.get("sha256")) for p in data.get("base_packs", [])}
    changed = sorted(name for name in set(now) | set(then) if now.get(name) != then.get(name))
    if changed and not force:
        raise AdoptError(
            f"The bundled packs {', '.join(changed)} changed since the tuning: tune again, or "
            "pass --force."
        )
    files = sorted(p for p in source.rglob("*") if p.is_file())
    previous = {p: (target / p.relative_to(source)) for p in files}
    backup = {dest: dest.read_bytes() for dest in previous.values() if dest.is_file()}
    for origin, dest in previous.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, dest)
    layers = [bundled()]
    if not _is_bundled(target):
        layers.append(Source("adopted", target, str(target), True))
    try:
        resolve(domain, layers)
    except HarnessError as exc:
        for dest in previous.values():
            if dest in backup:
                dest.write_bytes(backup[dest])
            else:
                dest.unlink(missing_ok=True)
        raise AdoptError(
            f"The adopted harness does not load, so nothing was changed:\n{exc}"
        ) from exc
    return list(previous.values())


def bundled_root() -> Path:
    """The bundled library as a directory (a source checkout or an unpacked install)."""

    root = Path(str(bundled().root))
    if not root.is_dir():
        raise AdoptError("The bundled harness is not a directory here; adopt into --to user.")
    return root


def _is_bundled(target: Path) -> bool:
    try:
        return target.resolve() == bundled_root().resolve()
    except AdoptError:
        return False


def _last_note(data: dict[str, Any]) -> str:
    notes = data.get("notes") or []
    return str(notes[-1]) if notes else "no note"
