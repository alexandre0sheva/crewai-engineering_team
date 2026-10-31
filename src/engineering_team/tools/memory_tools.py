"""Group ``notes``: Remember Fact, an agent's proposal for the repository's memory
(docs/HARNESS.md#repo-memory).

A proposal is stored as ``proposed`` and shown to nobody. At the end of the run the controller
keeps it only if its own records support it (``memory/observe.py``); otherwise it is dropped.
"""

from __future__ import annotations

from crewai.tools import BaseTool, tool

from engineering_team.tools.support import ToolEnv, ToolError

MAX_PROPOSALS_PER_RUN = 5
PROPOSABLE = ("command", "setup", "convention", "layout", "gotcha")  # flaky tests: controller only
NEEDS_COMMAND = ("command", "setup")


def make_memory_tools(env: ToolEnv) -> dict[str, BaseTool]:
    # Imported here: ``memory`` reaches ``tools`` (the workspace), and the catalogue imports this.
    from engineering_team.memory.facts import (
        MAX_COMMAND_CHARS,
        MAX_TEXT_CHARS,
        Evidence,
        Fact,
        fact_id,
        hash_sources,
        one_line,
        secret_in,
    )
    from engineering_team.settings import secret_values
    from engineering_team.verification.revision import verification_revision

    ctx = env.ctx
    store = ctx.memory

    @tool("Remember Fact")
    def remember_fact(kind: str, text: str, paths: str = "", command: str = "") -> str:
        """Propose a fact about this repository for later runs: one sentence another engineer
        could not guess. kind: command (how to run something; give command=), setup (a step that
        made the project work; give command=), convention, layout or gotcha (give paths= of the
        files it rests on, comma-separated). It is kept only if the controller's own records
        back it by the end of the run. Never put secrets in it.
        Example: kind='gotcha', text='Migrations run only through make db', paths='Makefile'.
        """

        def operation() -> str:
            if not store.enabled:
                raise ToolError("Repo memory is switched off for this run ([memory] enabled).")
            name = kind.strip().lower()
            if name == "flaky_test":
                raise ToolError(
                    "Flaky tests are recorded by the controller when a test flips on an unchanged "
                    f"tree; propose one of: {', '.join(PROPOSABLE)}."
                )
            if name not in PROPOSABLE:
                raise ToolError(f"Unknown kind {kind!r}; use one of: {', '.join(PROPOSABLE)}.")
            line = one_line(text, 10_000)
            if not line:
                raise ToolError("text is empty; state the fact in one sentence.")
            if len(line) > MAX_TEXT_CHARS:
                raise ToolError(
                    f"text is {len(line)} characters; keep it under {MAX_TEXT_CHARS}, one sentence."
                )
            run_command = one_line(command, 10_000)
            if len(run_command) > MAX_COMMAND_CHARS:
                raise ToolError(
                    f"command is over {MAX_COMMAND_CHARS} characters; give the command only."
                )
            if name in NEEDS_COMMAND and not run_command:
                raise ToolError(f"A {name} fact needs command=, so the controller can look for it.")
            wanted = [p.strip() for p in paths.split(",") if p.strip()]
            if name not in NEEDS_COMMAND and not wanted:
                raise ToolError(
                    f"A {name} fact needs paths= (the files it rests on) to be checked."
                )
            for part in (line, run_command):
                if (why := secret_in(part, tuple(secret_values()))) is not None:
                    raise ToolError(f"Not stored: {why}. Facts never hold secrets.")
            sources = hash_sources(ctx.workspace.root, wanted)
            if isinstance(sources, str):
                raise ToolError(f"{sources}.")
            topic = f"proposal:{fact_id(name, line.lower())}"
            ident = fact_id(name, topic)
            known = store.get(ident)
            if known is not None and known.status == "verified":
                return f"Already remembered ({ident}). Nothing to do."
            mine = [
                f
                for f in store.facts()
                if f.status == "proposed" and f.evidence.run_id == ctx.run_id
            ]
            if len(mine) >= MAX_PROPOSALS_PER_RUN and known is None:
                raise ToolError(
                    f"At most {MAX_PROPOSALS_PER_RUN} proposals per run; keep the ones that matter."
                )
            store.upsert(
                Fact(
                    id=ident,
                    kind=name,  # type: ignore[arg-type]
                    topic=topic,
                    text=line,
                    status="proposed",
                    command=run_command,
                    evidence=Evidence(run_id=ctx.run_id),
                    sources=sources,
                    revision=verification_revision(ctx.workspace),
                    proposed_by=env.agent or "",
                )
            )
            return (
                f"Proposed fact {ident}. It is kept only if the controller's records back it by "
                "the end of this run (a passing check or command with that command; for "
                "convention, layout and gotcha, a verified run in which you read the files you "
                "cited). Until then nobody is shown it."
            )

        return env.run(
            "Remember Fact",
            operation,
            arguments={"kind": kind, "text": text, "paths": paths, "command": command},
        )

    return {"Remember Fact": remember_fact}
