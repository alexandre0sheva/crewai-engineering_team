"""The repo memory store: ``.engineering-team/memory/facts.jsonl`` and its readable ``FACTS.md``.

One store per repository, shared by every run in it. The life of a fact: an agent *proposes* it
(``proposed``, never shown) or the controller *records* it from something it observed
(``verified``); a changed source file makes it ``stale`` (never shown); a later observation that
contradicts it removes it, and one that restates it replaces it. Writes replace the whole file
atomically; two runs writing at the same moment can lose one update (the last writer wins), which
costs a fact that the next run records again.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from engineering_team.atomic_io import atomic_write_text
from engineering_team.contracts import utc_now
from engineering_team.memory.facts import (
    Evidence,
    Fact,
    FactKind,
    SeenCheck,
    Source,
    fact_id,
    hash_file,
)
from engineering_team.memory.view import render_facts
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY

MEMORY_DIRECTORY = Path(CONTROLLER_DIRECTORY) / "memory"
FACTS_FILE = "facts.jsonl"
VIEW_FILE = "FACTS.md"
OBSERVED_FILE = "observed.json"  # the last result of each check, to see a test flip
DEFAULT_MAX_FACTS = 200
MAX_OBSERVED = 400

Emit = Callable[..., None]


class MemoryFileError(ValueError):
    """The memory files cannot be used; the message has one line per problem."""


class MemoryStore:
    """Facts of one repository. ``enabled=False`` makes every write a no-op and every read empty
    (``[memory] enabled = false``)."""

    def __init__(
        self,
        root: Path,
        *,
        enabled: bool = True,
        max_facts: int = DEFAULT_MAX_FACTS,
        emit: Emit | None = None,
        run_id: str = "",
    ) -> None:
        self.root = root
        self.run_id = run_id
        self.directory = root / MEMORY_DIRECTORY
        self.enabled = enabled
        self.max_facts = max_facts
        self._emit = emit
        self._lock = threading.RLock()
        self._used: set[str] = set()  # facts already shown this run (last_used is written once)
        # What the controller saw this run, for confirming proposals at its end (observe.py).
        self.seen_checks: list[SeenCheck] = []
        self.flipped: set[str] = set()  # topics of the tests that flipped on an unchanged tree
        self.verified = False  # the run's verification ended ``verified``

    # -- reading ---------------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self.directory / FACTS_FILE

    def facts(self) -> list[Fact]:
        """Every fact on disk, oldest first. A damaged file raises :class:`MemoryFileError`."""

        if not self.enabled:
            return []
        with self._lock:
            return self._read()

    def get(self, ident: str) -> Fact | None:
        """The fact with this id, or the only one whose id starts with ``ident``."""

        found = [f for f in self.facts() if f.id == ident] or [
            f for f in self.facts() if ident and f.id.startswith(ident)
        ]
        return found[0] if len(found) == 1 else None

    def usable(self) -> list[Fact]:
        """Verified facts whose source files still have the content they had (checked now, so an
        edit made earlier in this run counts): what a prompt may show."""

        return [f for f in self.facts() if f.usable and self.sources_hold(f.sources)]

    def _read(self) -> list[Fact]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError) as exc:
            raise MemoryFileError(
                f"{self.path}: cannot be read ({exc}). Delete it to start over."
            ) from exc
        facts: list[Fact] = []
        problems: list[str] = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                facts.append(Fact.model_validate_json(line))
            except (ValidationError, ValueError) as exc:
                reason = str(exc).splitlines()[0][:120]
                problems.append(
                    f"{self.path} line {number}: not a valid fact ({reason}). Fix or delete the "
                    "line, or run `engineering-team memory clear`."
                )
        if problems:
            raise MemoryFileError("\n".join(problems))
        return facts

    # -- writing ---------------------------------------------------------------------------

    def _write(self, facts: list[Fact]) -> None:
        facts = self._trimmed(facts)
        lines = [fact.model_dump_json() for fact in facts]
        atomic_write_text(self.path, "".join(f"{line}\n" for line in lines))
        atomic_write_text(self.directory / VIEW_FILE, render_facts(facts))

    def _trimmed(self, facts: list[Fact]) -> list[Fact]:
        """At most ``max_facts``: stale, then proposed, then the least recently used go first."""

        if len(facts) <= self.max_facts:
            return facts
        rank = {"stale": 0, "proposed": 1, "verified": 2}

        def keep_order(fact: Fact) -> tuple[int, datetime]:
            return rank[fact.status], fact.last_used or fact.created

        drop = {f.id for f in sorted(facts, key=keep_order)[: len(facts) - self.max_facts]}
        self._announce("memory.trimmed", ids=sorted(drop))
        return [fact for fact in facts if fact.id not in drop]

    def _announce(self, event: str, **data: Any) -> None:
        if self._emit is not None:
            self._emit(event, **data)

    def upsert(self, fact: Fact) -> Fact:
        """Store ``fact``, replacing the fact of the same kind and topic."""

        if not self.enabled:
            return fact
        with self._lock:
            facts = self._read()
            old = next((f for f in facts if f.id == fact.id), None)
            if old is not None and old.text != fact.text:
                self._announce("memory.replaced", id=fact.id, kind=fact.kind, old=old.text)
            kept = [f for f in facts if f.id != fact.id]
            if old is not None and fact.last_used is None:
                fact = fact.model_copy(update={"last_used": old.last_used})
            self._write([*kept, fact])
        self._announce("memory.recorded", id=fact.id, kind=fact.kind, status=fact.status)
        return fact

    def remove(self, ident: str, *, reason: str = "") -> bool:
        """Delete a fact; ``False`` if there is none with that id."""

        if not self.enabled:
            return False
        with self._lock:
            facts = self._read()
            gone = [f for f in facts if f.id == ident]
            if not gone:
                return False
            self._write([f for f in facts if f.id != ident])
        self._announce("memory.removed", id=ident, kind=gone[0].kind, reason=reason)
        return True

    def contradict(self, kind: FactKind, topic: str, reason: str) -> bool:
        """A later observation shows the fact about ``topic`` is no longer true: remove it."""

        return self.remove(fact_id(kind, topic), reason=reason)

    def clear(self) -> int:
        """Delete every fact (and the observed check results); returns how many facts went."""

        with self._lock:
            try:
                count = len(self._read())
            except MemoryFileError:  # clearing is the way out of a damaged file
                count = len(self.path.read_text(encoding="utf-8", errors="replace").splitlines())
            for name in (FACTS_FILE, VIEW_FILE, OBSERVED_FILE):
                (self.directory / name).unlink(missing_ok=True)
        if count:
            self._announce("memory.cleared", count=count)
        return count

    def verify(self, ident: str, evidence: Evidence) -> Fact | None:
        """Promote a proposal the controller found supporting evidence for."""

        with self._lock:
            fact = next((f for f in self._read() if f.id == ident), None)
            if fact is None:
                return None
            promoted = fact.model_copy(update={"status": "verified", "evidence": evidence})
        return self.upsert(promoted)

    def touch(self, facts: Iterable[Fact]) -> None:
        """Mark facts as shown to the team: ``last_used`` is written once per run and fact."""

        fresh = [f for f in facts if f.id not in self._used]
        if not fresh or not self.enabled:
            return
        self._used.update(f.id for f in fresh)
        stamp = utc_now()
        with self._lock:
            ids = {f.id for f in fresh}
            self._write(
                [
                    f.model_copy(update={"last_used": stamp}) if f.id in ids else f
                    for f in self._read()
                ]
            )

    def refresh(self) -> list[Fact]:
        """Mark stale every non-stale fact whose source changed or went missing; returns them."""

        if not self.enabled:
            return []
        with self._lock:
            facts = self._read()
            changed: list[Fact] = []
            result: list[Fact] = []
            for fact in facts:
                if fact.status != "stale" and not self.sources_hold(fact.sources):
                    fact = fact.model_copy(update={"status": "stale"})
                    changed.append(fact)
                result.append(fact)
            if changed:
                self._write(result)
        for fact in changed:
            self._announce("memory.stale", id=fact.id, kind=fact.kind, text=fact.text)
        return changed

    def sources_hold(self, sources: list[Source]) -> bool:
        return all(hash_file(self.root / s.path) == s.sha256 for s in sources)

    def drop_proposals(self, *, except_run: str = "") -> list[Fact]:
        """Remove proposals nobody confirmed (those of other runs than ``except_run``)."""

        if not self.enabled:
            return []
        with self._lock:
            facts = self._read()
            gone = [f for f in facts if f.status == "proposed" and f.evidence.run_id != except_run]
            if gone:
                self._write([f for f in facts if f not in gone])
        for fact in gone:
            self._announce("memory.dropped", id=fact.id, kind=fact.kind, text=fact.text)
        return gone

    # -- what the checks showed last time (to see a test flip) -----------------------------

    def observe(self, key: str, revision: str, failing: list[str], run_id: str) -> list[str]:
        """Record a check's failing tests on a tree; returns the tests whose outcome differs from
        the last observation of the same check on the *same* tree (they flipped with no change)."""

        if not self.enabled or not revision:
            return []
        with self._lock:
            seen = self._observed()
            previous = seen.get(key)
            seen.pop(key, None)
            seen[key] = {"revision": revision, "failing": sorted(failing), "run_id": run_id}
            for stale in list(seen)[: max(0, len(seen) - MAX_OBSERVED)]:
                seen.pop(stale)
            atomic_write_text(self.directory / OBSERVED_FILE, json.dumps(seen, indent=1) + "\n")
        if not previous or previous.get("revision") != revision:
            return []
        before, now = set(previous.get("failing", [])), set(failing)
        return sorted(before ^ now)

    def _observed(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads((self.directory / OBSERVED_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
