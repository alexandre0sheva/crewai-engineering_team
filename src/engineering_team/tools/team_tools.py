"""Group ``team``: the lead calls teammates for bounded, typed jobs.

Only the ``team`` strategy's lead gets these tools, and only during its own session: they reach
the controller's delegator through the run's ``DelegationHub`` (``runtime/delegation.py``), which
checks every call (who asks, the cap, owned paths, the roster) and runs the teammate as a small
crew of its own (``pipeline/delegation.py``). A teammate never has a delegator to reach, so
delegation is one level deep.
"""

from __future__ import annotations

from typing import Any

from crewai.tools import BaseTool, tool

from engineering_team.tools.support import ToolEnv, ToolError

NOT_THE_LEAD = (
    "Only the lead can call teammates, and only in its own session; do this job yourself and "
    "report what you did."
)


def make_team_tools(env: ToolEnv) -> dict[str, BaseTool]:
    def call(name: str, arguments: dict[str, Any]) -> str:
        delegator = env.ctx.delegation.active
        if delegator is None or env.agent != delegator.lead or env.lane is not None:
            raise ToolError(NOT_THE_LEAD)
        return delegator.handle(name, arguments)

    def run(name: str, arguments: dict[str, Any]) -> str:
        return env.run(name, lambda: call(name, arguments), arguments=arguments)

    @tool("Delegate")
    def delegate(
        teammate: str,
        brief: str,
        owned_paths: list[str],
        criteria: list[str] | None = None,
        max_steps: int = 0,
        why: str = "",
    ) -> str:
        """Give one bounded job to a teammate and wait for its result.

        The teammate gets your brief (not your history), may change only owned_paths (globs), and
        returns a summary, the files it changed and the checks it ran; the controller then runs
        the project's checks. Use it for an independent part or a different stack, not for small
        edits. max_steps caps its model calls (0: the default). Example: teammate=
        'frontend_engineer', brief='Add the notes page ...', owned_paths=['web/**'], why='UI work'.
        """

        return run(
            "Delegate",
            {
                "teammate": teammate,
                "brief": brief,
                "owned_paths": owned_paths,
                "criteria": criteria or [],
                "max_steps": max_steps,
                "why": why,
            },
        )

    @tool("Delegate Parallel")
    def delegate_parallel(briefs: list[dict[str, Any]]) -> str:
        """Run several independent jobs side by side, one teammate each, and wait for all.

        Each brief is {teammate, brief, owned_paths, criteria, max_steps, why}, like Delegate;
        owned_paths must not overlap between briefs (an overlapping call is refused). Each job
        counts against the delegation limit. Example: briefs=[{'teammate': 'backend_engineer',
        'brief': 'API ...', 'owned_paths': ['api/**']}, {'teammate': 'frontend_engineer',
        'brief': 'UI ...', 'owned_paths': ['web/**']}].
        """

        return run("Delegate Parallel", {"briefs": briefs})

    @tool("Ask Reviewer")
    def ask_reviewer(focus: str = "", why: str = "") -> str:
        """Have the code reviewer read the current change and return findings.

        Read-only: it reports defects with file, line, severity and a fix; it changes nothing.
        Use it once a non-trivial change is ready, then fix what matters. focus narrows it.
        Example: focus='the new storage module'.
        """

        return run("Ask Reviewer", {"focus": focus, "why": why})

    @tool("Ask Security Review")
    def ask_security_review(focus: str = "", why: str = "") -> str:
        """Have the security engineer read the current change and return findings.

        Read-only. Use it for changes touching authentication, secrets, crypto, SQL,
        subprocesses, file uploads or deserialisation (the controller reviews such changes
        anyway if you do not). Example: focus='the login handler'.
        """

        return run("Ask Security Review", {"focus": focus, "why": why})

    @tool("Ask Debugger")
    def ask_debugger(check_id: str, paths: list[str] | None = None, why: str = "") -> str:
        """Hand a failing check you could not fix to the debugger for a diagnosis.

        check_id names the controller's check (from its check results). Without paths the
        debugger only reads and diagnoses; with paths (globs) it may also fix them. Use it after
        two failed tries of your own. Example: check_id='tests', paths=['src/store.py'].
        """

        return run("Ask Debugger", {"check_id": check_id, "paths": paths or [], "why": why})

    @tool("Ask Architect")
    def ask_architect(question: str, why: str = "") -> str:
        """Ask the solution architect for a decision on a design question or a plan draft.

        Read-only; returns the decision and its reasons. Use it for large or unclear work before
        you build, not for routine choices. Example: question='One module or a package per
        command for this CLI? Draft: ...'.
        """

        return run("Ask Architect", {"question": question, "why": why})

    @tool("Ask Analyst")
    def ask_analyst(question: str, why: str = "") -> str:
        """Ask the product analyst to turn an unclear request into criteria or settle a question.

        Read-only; returns acceptance criteria or an answer with its assumptions. Use it when the
        request is ambiguous about behaviour. Example: question='What should list print when
        there are no notes?'.
        """

        return run("Ask Analyst", {"question": question, "why": why})

    return {
        "Delegate": delegate,
        "Delegate Parallel": delegate_parallel,
        "Ask Reviewer": ask_reviewer,
        "Ask Security Review": ask_security_review,
        "Ask Debugger": ask_debugger,
        "Ask Architect": ask_architect,
        "Ask Analyst": ask_analyst,
    }
