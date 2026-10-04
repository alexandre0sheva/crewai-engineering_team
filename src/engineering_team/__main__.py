"""``python -m engineering_team``: the command line (how the web UI starts a run)."""

import sys

from engineering_team.main import run

sys.exit(run())
