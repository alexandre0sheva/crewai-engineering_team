"""Controller actions: what a ``controller`` stage runs (code in the controller, no model).

An action receives the run context and the pipeline state and returns a one-line summary; it may
raise to fail the stage. The recipe names it with ``action:``. Later tasks register more (the
verifier); keeping them in a registry means a recipe stays data.
"""

from __future__ import annotations

from collections.abc import Callable

from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext

ControllerAction = Callable[[RunContext, PipelineState], str]

CONTROLLER_ACTIONS: dict[str, ControllerAction] = {}


def register_action(name: str) -> Callable[[ControllerAction], ControllerAction]:
    def decorate(action: ControllerAction) -> ControllerAction:
        CONTROLLER_ACTIONS[name] = action
        return action

    return decorate
