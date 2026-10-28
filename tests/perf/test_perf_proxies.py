"""Offline performance proxies: sessions, model calls, tool-schema and prompt size per scenario.

These are the deterministic stand-ins for the speed and cost targets in the 0.3.0 plan
("Performance targets"): a change that adds agent sessions, model calls or per-call prompt weight
fails the gate without a paid benchmark. Two kinds of test:

* ``test_*_does_not_grow`` pin today's structure (0.2.0 behaviour): a ceiling a little above the
  measured value. When a task makes the team leaner, lower the ceiling to the new value in the
  same change.
* ``test_*_meets_the_target`` state the target for each scenario and are ``xfail`` until T6 (lean
  team) and T12 (lead agent) meet them. ``strict`` makes the gate fail the day a target is met,
  so the marker is removed then.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scenarios import (
    Measured,
    medium_feature_pipeline,
    small_bugfix_fix_mode,
    small_new_project_pipeline,
    small_new_project_single,
)
from test_baseline import interpreter_on_path  # noqa: F401  (the repro command needs pytest)

# The targets of the plan, in the units of ``Measured``.
SMALL_SESSIONS = 3
SMALL_MODEL_CALLS = 25
SCHEMA_TOKENS = 6_000  # per agent
ALWAYS_ON_TOKENS = 2_500  # the cap T17 will enforce on always-on text, as a per-call proxy

# Today's values with a little room (measured 2026-10-28, 0.2.0 structure).
PIPELINE_SMALL = Measured(11, 19, 12_102, 1_896, 3, ())
PIPELINE_MEDIUM = Measured(13, 23, 12_102, 1_896, 3, ())
SINGLE_SMALL = Measured(1, 4, 12_102, 402, 0, ())
FIX_SMALL = Measured(9, 12, 12_102, 1_691, 0, ())
HEADROOM = 1.15


def within(measured: Measured, ceiling: Measured) -> None:
    for name in ("sessions", "model_calls", "schema_tokens", "always_on_tokens", "ceremony_stages"):
        limit = getattr(ceiling, name) * HEADROOM
        assert getattr(measured, name) <= limit, (
            f"{name}: {getattr(measured, name)} is over {limit:.0f} "
            f"({measured.stage_names}); if this is an improvement, lower the ceiling"
        )


# -- today: no growth ----------------------------------------------------------------------------


def test_a_small_pipeline_run_does_not_grow(monkeypatch: pytest.MonkeyPatch) -> None:
    within(small_new_project_pipeline(monkeypatch), PIPELINE_SMALL)


def test_a_medium_pipeline_run_does_not_grow(monkeypatch: pytest.MonkeyPatch) -> None:
    within(medium_feature_pipeline(monkeypatch), PIPELINE_MEDIUM)


@pytest.mark.git
def test_a_small_bugfix_does_not_grow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    within(small_bugfix_fix_mode(monkeypatch, tmp_path), FIX_SMALL)


def test_the_single_agent_baseline_does_not_grow(monkeypatch: pytest.MonkeyPatch) -> None:
    within(small_new_project_single(monkeypatch), SINGLE_SMALL)


def test_more_packages_cost_sessions_and_calls_in_proportion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the medium plan has two more packages than the small one: one session and two calls each
    small = small_new_project_pipeline(monkeypatch)
    import shutil
    from pathlib import Path

    shutil.rmtree(Path.cwd() / "ws")
    medium = medium_feature_pipeline(monkeypatch)

    assert medium.sessions - small.sessions == 2
    assert medium.model_calls - small.model_calls == 4


# -- the targets (xfail until T6 and T12) --------------------------------------------------------

LEAN = pytest.mark.xfail(
    strict=True, reason="T6 (lean team) and T12 (lead agent) bring the small-task structure here"
)


@LEAN
def test_a_small_task_meets_the_session_and_call_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    measured = small_new_project_pipeline(monkeypatch)

    assert measured.sessions <= SMALL_SESSIONS
    assert measured.model_calls <= SMALL_MODEL_CALLS
    assert measured.ceremony_stages == 0


@LEAN
@pytest.mark.git
def test_a_small_bugfix_meets_the_session_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    measured = small_bugfix_fix_mode(monkeypatch, tmp_path)

    assert measured.sessions <= SMALL_SESSIONS
    assert measured.model_calls <= SMALL_MODEL_CALLS


@LEAN
def test_every_agent_meets_the_tool_schema_target(monkeypatch: pytest.MonkeyPatch) -> None:
    assert small_new_project_pipeline(monkeypatch).schema_tokens <= SCHEMA_TOKENS


def test_the_single_agent_baseline_is_within_the_small_task_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the bar the team has to reach: one session, few calls, no ceremony
    measured = small_new_project_single(monkeypatch)

    assert measured.sessions <= SMALL_SESSIONS
    assert measured.model_calls <= SMALL_MODEL_CALLS
    assert measured.ceremony_stages == 0


def test_the_always_on_prompt_stays_under_its_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # already true today, so a guard rather than a target
    assert small_new_project_pipeline(monkeypatch).always_on_tokens <= ALWAYS_ON_TOKENS
