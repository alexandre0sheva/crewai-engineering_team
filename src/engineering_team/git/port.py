"""``GitPort``: the controller's one way to use Git.

Every command is a fixed argv built here (never agent input) and runs through the run's
execution backend, so the Docker sandbox covers it and cancellation and timeouts apply. Hard
rules, enforced by what the port can do rather than by a filter:

* it has no method that pushes, fetches, pulls, touches remotes, or forces anything;
* hooks never run (``core.hooksPath=/dev/null``), nor do commands a repository's own config
  names (``core.fsmonitor``, external diff and text conversion are switched off);
* no pager, no credential helper, no prompt, no system or global configuration;
* it refuses a repository whose top level is not the project itself: a project that sits inside
  some other repository (this tool's own checkout, say) is not a repository of ours;
* the real index is never touched by reads: diffs, patches, and the tree hash use a temporary
  index.

Agent-chosen text (refs, paths, search patterns) is validated before it reaches an argv.
"""

from __future__ import annotations

from engineering_team.git.core import (
    DEFAULT_EMAIL,
    DEFAULT_NAME,
    Change,
    GitError,
)
from engineering_team.git.writes import GitWrites

__all__ = ["DEFAULT_EMAIL", "DEFAULT_NAME", "Change", "GitError", "GitPort"]


class GitPort(GitWrites):
    """Git for one project: plumbing (``GitCore``), reads (``GitReads``), changes (``GitWrites``).

    Thread-safe; see the module docstring for the rules.
    """
