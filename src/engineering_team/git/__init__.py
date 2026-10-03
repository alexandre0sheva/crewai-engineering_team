"""Git for the controller (``GitPort``): checkpoints, patches, and bounded read-only history."""

# ``git.port`` needs the tool package, whose registry imports code intelligence, which needs
# ``GitError`` from ``git.port``. Loading the tool package first fixes the order, so importing
# ``engineering_team.git`` on its own cannot hit a half-initialised module.
import engineering_team.tools  # noqa: F401
from engineering_team.git.port import GitError, GitPort

__all__ = ["GitError", "GitPort"]
