"""Running the ``analyze`` stage: analysts read the chunks side by side, one more reads them all.

Each chunk goes to a read-only analyst (the parallel read-only helper, one lane each; their only
writable path is scratch space under ``.engineering-team/tmp``). The synthesis step combines their
analyses with the facts the controller found, and the controller renders the map. A chunk whose
analysis fails is reported and left out; the stage fails only when none was analysed or the
synthesis fails. A map that already describes exactly this tree is reused, which costs nothing.
"""

from __future__ import annotations

from typing import Protocol

from crewai.tools import BaseTool

from engineering_team.modes.codebase_map import (
    SCRATCH,
    Chunk,
    ChunkAnalysis,
    CodebaseMap,
    cached_map,
    plan_chunks,
    render_map,
    write_map,
)
from engineering_team.modes.profile_render import render_profile
from engineering_team.modes.repo_analyzer import analyze_repo
from engineering_team.pipeline.parallel import (
    ReadOnlyJob,
    readonly_tools,
    run_parallel_readonly,
)
from engineering_team.pipeline.stages import StageError, StageOutput
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.snapshot import workspace_revision

MAX_SYNTHESIS_CHARS = 60_000


class Call(Protocol):
    """One analyst's turn: its read-only tools, its lane, and the inputs of its prompt, and what
    it returned (the executor wires this to the stage runner)."""

    def __call__(
        self,
        *,
        tools: list[BaseTool],
        lane: int | None,
        chunk: str = "",
        synthesis: str = "",
        profile: str = "",
    ) -> StageOutput: ...


def run_map(ctx: RunContext, state: PipelineState, teammate: str, call: Call) -> str:
    """Build (or reuse) the codebase map; returns a one-line summary for the stage record."""

    root = ctx.workspace.root
    tree = workspace_revision(ctx.workspace)
    if (path := cached_map(root, tree)) is not None:
        ctx.events.emit("map.cached", tree=tree)
        return f"The codebase map already describes this tree ({path.relative_to(root)})."
    profile = state.profile or analyze_repo(root, ctx.git)
    facts = render_profile(profile)
    chunks = plan_chunks(ctx.workspace, ctx.settings.analysis.max_chunks)
    if not chunks:
        raise StageError("There is no source code to map: no files of a known language.")
    ctx.events.emit("map.chunks", chunks=[(c.name, len(c.files), c.bytes) for c in chunks])
    by_name = {chunk.slug: chunk for chunk in chunks}
    analyses: dict[str, ChunkAnalysis] = {}

    def analyse(job: ReadOnlyJob, tools: list[BaseTool], lane: int) -> str:
        chunk = by_name[job.name]
        output = call(tools=tools, lane=lane, chunk=chunk.brief(), profile=facts)
        found = output.contracts.get("analysis")
        if not isinstance(found, ChunkAnalysis):
            raise StageError(f"The analysis of {chunk.name} was not a valid analysis.")
        analyses[job.name] = found
        return found.summary

    jobs = [ReadOnlyJob(chunk.slug, teammate, chunk.report_path) for chunk in chunks]
    results = run_parallel_readonly(ctx, jobs, analyse)
    failed = [by_name[r.name].name for r in results if r.status != "succeeded"]
    for result in results:
        if result.status != "succeeded":
            ctx.events.emit("map.chunk_failed", chunk=by_name[result.name].name, error=result.error)
    if not analyses:
        detail = "; ".join(f"{r.name}: {r.error}" for r in results)
        raise StageError(f"No chunk could be analysed ({detail}).")

    synthesis = _synthesise(ctx, teammate, call, chunks, analyses, facts)
    text = render_map(synthesis, profile, chunks, failed)
    path = write_map(root, tree, text)
    ctx.events.emit("map.written", tree=tree, chunks=len(chunks), failed=len(failed))
    note = f" {len(failed)} chunk(s) could not be analysed." if failed else ""
    return f"Mapped {len(chunks)} chunk(s) into {path.relative_to(root)}.{note}"


def _synthesise(
    ctx: RunContext,
    teammate: str,
    call: Call,
    chunks: list[Chunk],
    analyses: dict[str, ChunkAnalysis],
    facts: str,
) -> CodebaseMap:
    """One more read-only agent combines the analyses (in chunk order, so the prompt is stable)."""

    blocks = []
    for chunk in chunks:
        analysis = analyses.get(chunk.slug)
        if analysis is not None:
            dumped = analysis.model_dump_json(indent=1, exclude={"schema_version"})
            blocks.append(f"### {chunk.name}\n{dumped}")
    text = "\n\n".join(blocks)
    if len(text) > MAX_SYNTHESIS_CHARS:
        text = text[:MAX_SYNTHESIS_CHARS] + "\n... (the rest of the analyses was cut)"
    tools = readonly_tools(ctx, ReadOnlyJob("synthesis", teammate, f"{SCRATCH}/synthesis.md"))
    output = call(tools=tools, lane=None, synthesis=text, profile=facts)
    found = output.contracts.get("analysis")
    if not isinstance(found, CodebaseMap):
        raise StageError("The synthesis did not return a valid codebase map.")
    return found
