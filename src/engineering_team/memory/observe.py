"""What the controller writes into repo memory: facts it observed, never facts an agent claims.

Three kinds of observation: a check the controller ran and what it showed (the command that runs
the tests, a test whose outcome flipped on an unchanged tree), a tool call the controller
recorded in the trajectory (a dependency install that worked), and, at the end of the run, the
agents' *proposals* (``Remember Fact``) that one of those records supports. Nothing here reads an
agent's own account as evidence; a proposal without a record stays unverified and is dropped.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_team.contracts import CheckResult
from engineering_team.memory.facts import (
    Evidence,
    Fact,
    FactKind,
    SeenCheck,
    Source,
    fact_id,
    hash_sources,
    one_line,
    secret_in,
)
from engineering_team.runtime.trajectory import TrajectoryEntry, read_trajectory

if TYPE_CHECKING:
    from engineering_team.modes.baseline_report import BaselineReport
    from engineering_team.modes.repo_profile import RepoProfile
    from engineering_team.runtime.context import RunContext

CHECK_KINDS = ("test", "lint", "typecheck", "build")
MANIFESTS = (
    "pyproject.toml", "setup.cfg", "tox.ini", "package.json", "go.mod", "Cargo.toml",
    "Makefile", "justfile", "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile",
    "composer.json", "uv.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
)  # fmt: skip
READ_TOOLS = ("Read Project File", "Read File Range", "Read Many Files", "Search Project Files")
COMMAND_TOOLS = ("Run Project Command", "Run Script")
INSTALL_PASSED = re.compile(r"Install \(([^)]+)\): PASSED\b")


def manifest_sources(root: Path, directory: str) -> list[str]:
    """The manifest files in ``directory`` a command fact depends on (relative to ``root``)."""

    base = root / directory
    found = [name for name in MANIFESTS if (base / name).is_file()]
    return [(Path(directory) / name).as_posix() for name in found[:5]]


def _sources(ctx: RunContext, paths: Sequence[str]) -> list[Source]:
    result = hash_sources(ctx.workspace.root, list(paths))
    return [] if isinstance(result, str) else result


def _record(
    ctx: RunContext,
    kind: FactKind,
    topic: str,
    text: str,
    *,
    evidence: Evidence,
    paths: Sequence[str] = (),
    revision: str = "",
) -> Fact | None:
    """Store a verified fact from the controller's own observation (a secret-looking text is
    dropped: the controller quotes commands and test names, which may hold one by accident)."""

    from engineering_team.settings import secret_values

    text = one_line(text)
    if secret_in(text, tuple(secret_values())):
        return None
    fact = Fact(
        id=fact_id(kind, topic),
        kind=kind,
        topic=topic,
        text=text,
        status="verified",
        evidence=evidence,
        sources=_sources(ctx, paths),
        revision=revision,
    )
    return ctx.memory.upsert(fact)


# -- checks the controller ran ------------------------------------------------------------------


LABELS = {"test": "Test", "lint": "Lint", "typecheck": "Type-check", "build": "Build"}


def shown_command(
    profile: RepoProfile | None, kind: str, directory: str, command: str
) -> tuple[str, str]:
    """The command as a person would type it, and where the project says so: the repository
    profile's detected command for this check if there is one, else the check's own command
    without the controller's report placeholders."""

    if profile is not None:
        for detected in profile.commands_of(kind):  # type: ignore[arg-type]
            if detected.directory == directory and detected.command:
                return detected.command, detected.source
    return " ".join(part for part in command.split() if "<report>" not in part), ""


def _command_fact(
    ctx: RunContext,
    profile: RepoProfile | None,
    kind: str,
    directory: str,
    command: str,
    check_id: str,
    revision: str,
) -> None:
    shown, source = shown_command(profile, kind, directory, command)
    where = "" if directory in ("", ".") else f" in `{directory}`"
    origin = f" ({one_line(source, 60)})" if source else ""
    _record(
        ctx,
        "command",
        f"command:{kind}:{directory}",
        f"{LABELS.get(kind, kind)} command{where}: `{one_line(shown, 150)}`{origin}. "
        "The controller ran this check and it passed.",
        evidence=Evidence(run_id=ctx.run_id, check_ids=[check_id]),
        paths=manifest_sources(ctx.workspace.root, directory),
        revision=revision,
    )


def _flaky(
    ctx: RunContext, check_id: str, directory: str, keys: Sequence[str], revision: str
) -> None:
    prefix = f"test:{directory}:"
    for key in keys:
        if not key.startswith(prefix):
            continue  # a failure with no test id is not a test that flipped
        test_id = key[len(prefix) :]
        topic = f"flaky:{key}"
        source = test_id.split("::", 1)[0]
        paths = [(Path(directory) / source).as_posix()] if "::" in test_id else []
        made = _record(
            ctx,
            "flaky_test",
            topic,
            f"`{one_line(test_id, 120)}` passed and failed on the same unchanged tree; a lone "
            "failure of it is not evidence that your change broke something.",
            evidence=Evidence(run_id=ctx.run_id, check_ids=[check_id]),
            paths=paths,
            revision=revision,
        )
        if made is not None:
            ctx.memory.flipped.add(topic)


def _flips(
    ctx: RunContext,
    check_id: str,
    kind: str,
    directory: str,
    revision: str,
    failing: list[str],
) -> None:
    if kind != "test":
        return
    flipped = ctx.memory.observe(check_id, revision, failing, ctx.run_id)
    if flipped:
        _flaky(ctx, check_id, directory, flipped, revision)


def record_baseline(
    ctx: RunContext, report: BaselineReport, profile: RepoProfile | None = None
) -> None:
    """The baseline's checks: the commands that ran, and tests that differ from the last
    observation of the same tree."""

    store = ctx.memory
    if not store.enabled:
        return
    for row in report.checks:
        store.seen_checks.append(
            SeenCheck(row.id, row.kind, row.command, row.directory, row.status)
        )
        if row.kind not in CHECK_KINDS:
            continue
        topic = f"command:{row.kind}:{row.directory}"
        if row.status == "passed" and row.command:
            _command_fact(
                ctx, profile, row.kind, row.directory, row.command, row.id, report.revision
            )
        elif row.status == "unavailable" or row.summary == "no tests found":
            store.contradict(
                "command", topic, f"the baseline of {ctx.run_id} could not run it: {row.summary}"
            )
        if row.status in ("passed", "failed") and row.command:
            _flips(ctx, row.id, row.kind, row.directory, report.revision, row.failing)


def record_verification(
    ctx: RunContext,
    directories: dict[str, str],
    results: Sequence[CheckResult],
    *,
    verified: bool,
    profile: RepoProfile | None = None,
) -> None:
    """The verifier's results: passing commands, commands that no longer run, flipped tests."""

    store = ctx.memory
    if not store.enabled:
        return
    store.verified = store.verified or verified
    for result in results:
        directory = directories.get(result.id, ".")
        store.seen_checks.append(
            SeenCheck(result.id, result.kind, result.command, directory, result.status)
        )
        if result.source != "detected" or result.kind not in CHECK_KINDS:
            continue
        topic = f"command:{result.kind}:{directory}"
        revision = result.revision or ""
        if result.status == "unavailable":
            store.contradict(
                "command", topic, f"{ctx.run_id}: the check could not run ({result.summary})"
            )
            continue
        if result.status == "passed" and result.command and not result.new_failures:
            _command_fact(ctx, profile, result.kind, directory, result.command, result.id, revision)
        if result.status in ("passed", "failed") and result.command:
            failing = [*result.known_failures, *result.new_failures]
            if result.status == "failed" and not failing and result.report:
                from engineering_team.modes.baseline_report import failure_keys

                failing = failure_keys(result, directory)
            _flips(ctx, result.id, result.kind, directory, revision, failing)


# -- the end of a run ---------------------------------------------------------------------------


def _entries(ctx: RunContext) -> list[TrajectoryEntry]:
    try:
        return [e for e in read_trajectory(ctx.trajectory.path) if e.kind == "tool"]
    except OSError:
        return []


def _record_setup(ctx: RunContext, entries: Sequence[TrajectoryEntry]) -> None:
    """A dependency install that worked, from the trajectory's record of the tool call."""

    for entry in entries:
        if entry.tool != "Install Dependencies" or not entry.ok:
            continue
        head = entry.output.splitlines()[0] if entry.output else ""
        matched = INSTALL_PASSED.match(head)
        if matched is None:
            continue
        manager = matched.group(1)
        directory = str(entry.args.get("working_directory") or ".")
        where = "the project root" if directory in ("", ".") else f"`{directory}`"
        _record(
            ctx,
            "setup",
            f"setup:install:{directory}",
            f"Dependencies install with `Install Dependencies` ({manager}) in {where}.",
            evidence=Evidence(run_id=ctx.run_id, tool_seqs=[entry.seq]),
            paths=manifest_sources(ctx.workspace.root, directory),
        )


def _supports(ctx: RunContext, fact: Fact, entries: Sequence[TrajectoryEntry]) -> Evidence | None:
    """The controller's record that backs a proposal, if there is one."""

    store = ctx.memory
    run = Evidence(run_id=ctx.run_id)
    if fact.kind in ("command", "setup") and fact.command:
        wanted = " ".join(fact.command.split())
        checks = [
            c.id
            for c in store.seen_checks
            if " ".join(c.command.split()) == wanted and c.status == "passed"
        ]
        seqs = [
            e.seq
            for e in entries
            if e.tool in COMMAND_TOOLS
            and e.ok
            and " ".join(str(e.args.get("command", "")).split()) == wanted
            and e.output.startswith("Exit code: 0")
        ]
        if checks or seqs:
            return run.model_copy(update={"check_ids": checks[:3], "tool_seqs": seqs[:3]})
        return None
    if fact.kind == "flaky_test":
        return run if fact.topic in store.flipped else None
    # convention, layout, gotcha: only a verified run that read every file the claim rests on.
    if not store.verified or not fact.sources:
        return None
    read_seqs: list[int] = []
    for source in fact.sources:
        reads = [e.seq for e in entries if e.tool in READ_TOOLS and source.path in str(e.args)]
        if not reads or not store.sources_hold([source]):
            return None
        read_seqs.append(reads[0])
    return run.model_copy(update={"tool_seqs": sorted(set(read_seqs))[:6]})


def finish_run(ctx: RunContext) -> None:
    """Close the run's part of memory: setup facts from the trajectory, the proposals the
    controller can back (promoted) or not (dropped), and facts whose sources the run changed
    (stale). Never raises: memory must not fail a run."""

    store = ctx.memory
    if not store.enabled:
        return
    try:
        entries = _entries(ctx)
        _record_setup(ctx, entries)
        for fact in store.facts():
            if fact.status == "proposed" and fact.evidence.run_id == ctx.run_id:
                evidence = _supports(ctx, fact, entries)
                if evidence is not None:
                    store.verify(fact.id, evidence)
        store.drop_proposals()
        store.refresh()
    except Exception as exc:  # a damaged file or a full disk is a warning, not a failed run
        ctx.events.emit("memory.error", error=f"{type(exc).__name__}: {exc}"[:300])
