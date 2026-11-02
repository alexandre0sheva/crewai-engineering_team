"""``[github]``: what the controller may do on GitHub (``docs/CONFIGURATION.md``)."""

from __future__ import annotations

from engineering_team.settings.model_section import _Frozen


class GithubSettings(_Frozen):
    """Nothing is pushed unless asked for: ``open_pr`` is the standing form of ``--open-pr``."""

    open_pr: bool = False  # after a passing repository run: push its branch, open a draft PR
