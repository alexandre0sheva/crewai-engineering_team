"""The harness: every teammate's and stage's prompt text, as files you can diff and override.

The bundled components live in ``harness/data/base/``; ``loader.py`` composes them in a fixed
order (docs/HARNESS.md).
"""

from engineering_team.harness.files import HarnessError
from engineering_team.harness.loader import (
    ALWAYS_ON_CAP,
    StagePrompt,
    always_on_chars,
    builtin_teammates,
    lead_guidance,
    stage_prompts,
    teammate_fields,
)

__all__ = [
    "ALWAYS_ON_CAP",
    "HarnessError",
    "StagePrompt",
    "always_on_chars",
    "builtin_teammates",
    "lead_guidance",
    "stage_prompts",
    "teammate_fields",
]
