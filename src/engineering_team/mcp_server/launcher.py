"""Starting ``do`` runs for the MCP server: the UI's launcher, with one more way to start.

A run is ``python -m engineering_team <globals> do ...`` in a process of its own (its own
session, so stopping the server does not stop it), recorded in the workspace root's
``.engineering-team-ui/`` like a run the web UI started, so ``status``, ``board``, ``cancel`` and
the UI see it too.
"""

from __future__ import annotations

from pathlib import Path

from engineering_team.runtime.context import new_run_id
from engineering_team.ui.launcher import (
    Budget,
    RunLauncher,
    RunOptions,
    StartError,
    StartRecord,
    overrides_for,
)

# Modes that only read: nothing is changed, so there is no branch or worktree to make.
READ_ONLY_MODES = ("analyze", "answer", "review")


class McpLauncher(RunLauncher):
    def _check_capacity(self) -> None:
        busy = len(self.active())
        if busy >= self.max_concurrent:
            raise StartError(
                f"{busy} run(s) are already going (mcp_serve.max_concurrent_runs is "
                f"{self.max_concurrent}). Wait for one to finish (task_status), or cancel it.",
                429,
            )

    def start_do(self, request: str, repo: Path, mode: str | None, budget: Budget) -> StartRecord:
        """Start ``do`` on ``repo`` without a preview or questions. A change is made in a
        separate worktree (the calling agent is usually working in the checkout itself)."""

        self._check_capacity()
        overrides = overrides_for(RunOptions(budget=budget))
        self._validate_settings(overrides)
        run_id = new_run_id()
        saved = self._write(self.state_dir / "uploads" / run_id, "request.md", request)
        argv = ["--workspace-root", str(self.root), "--run-id", run_id, "do"]
        argv += ["--request-file", str(saved), "--repo", str(repo), "--yes", "--non-interactive"]
        argv.append("--no-open-pr")  # whatever `github.open_pr` says: nothing is pushed from here
        if mode:
            argv.append(f"--mode={mode}")
        if mode not in READ_ONLY_MODES:
            argv.append("--worktree")
        if self.config_file:
            argv += ["--config", self.config_file]
        return self._spawn(run_id, "do", argv, overrides, resume=False)
