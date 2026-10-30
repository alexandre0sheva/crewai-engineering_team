"""Offline performance proxies: sessions, model calls, tool-schema and prompt size per scenario.

These are the deterministic stand-ins for the speed and cost targets in the 0.3.0 plan
("Performance targets"): a change that adds agent sessions, model calls or per-call prompt weight
fails the gate without a paid benchmark. Two kinds of test:

* ``test_*_does_not_grow`` pin today's structure: a ceiling a little above the measured value.
  When a task makes the team leaner, lower the ceiling to the new value in the same change.
* ``test_*_meets_the_target`` state the target for each scenario. The small-task targets are met
  since T6 (lean team); the medium target ("sessions only for teammates the lead called") since
  T12 (the team strategy).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from scenarios import (
    Measured,
    full_pipeline,
    medium_feature_pipeline,
    medium_new_project_team,
    small_bugfix_fix_mode,
    small_new_project_pipeline,
    small_new_project_single,
    small_new_project_team,
)
from test_baseline import interpreter_on_path  # noqa: F401  (the repro command needs pytest)

# The targets of the plan, in the units of ``Measured``.
SMALL_SESSIONS = 3
SMALL_MODEL_CALLS = 25
SCHEMA_TOKENS = 6_000  # per agent
ALWAYS_ON_TOKENS = 2_500  # the cap T17 will enforce on always-on text, as a per-call proxy

# Today's values with a little room (measured 2026-10-28 after T6; before it, with the 0.2.0
# structure, the small pipeline was 11 sessions, 19 calls and 12,102 schema tokens per call, the
# medium one 13 sessions and 23 calls, the small fix 9 sessions and 12 calls).
PIPELINE_SMALL = Measured(3, 6, 3_234, 2_250, 0, ())
PIPELINE_MEDIUM = Measured(9, 15, 3_234, 2_250, 0, ())
PIPELINE_FULL = Measured(10, 17, 3_234, 1_719, 2, ())  # lean.size = large, ceremony = always
SINGLE_SMALL = Measured(1, 4, 3_234, 404, 0, ())
FIX_SMALL = Measured(3, 6, 3_234, 1_849, 0, ())
# The team strategy (measured 2026-10-30, T12): the lead's schema carries the seven team tools.
TEAM_SMALL = Measured(1, 3, 4_651, 912, 0, ())
TEAM_MEDIUM = Measured(4, 9, 4_651, 1_346, 0, ())
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


def test_the_full_pipeline_does_not_grow(monkeypatch: pytest.MonkeyPatch) -> None:
    within(full_pipeline(monkeypatch), PIPELINE_FULL)


@pytest.mark.git
def test_a_small_bugfix_does_not_grow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    within(small_bugfix_fix_mode(monkeypatch, tmp_path), FIX_SMALL)


def test_the_single_agent_baseline_does_not_grow(monkeypatch: pytest.MonkeyPatch) -> None:
    within(small_new_project_single(monkeypatch), SINGLE_SMALL)


def test_a_medium_plan_costs_one_session_per_package_plus_foundation_and_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    small = small_new_project_pipeline(monkeypatch)
    shutil.rmtree(Path.cwd() / "ws")
    medium = medium_feature_pipeline(monkeypatch)

    # design, foundation, four packages, integration, two reviewers
    assert medium.sessions == 1 + 1 + 4 + 1 + 2
    assert small.sessions == 1 + 1 + 1  # design, one build session, one reviewer


# -- the targets ---------------------------------------------------------------------------------


def test_a_small_task_meets_the_session_and_call_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    measured = small_new_project_pipeline(monkeypatch)

    assert measured.sessions <= SMALL_SESSIONS
    assert measured.model_calls <= SMALL_MODEL_CALLS
    assert measured.ceremony_stages == 0


@pytest.mark.git
def test_a_small_bugfix_meets_the_session_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    measured = small_bugfix_fix_mode(monkeypatch, tmp_path)

    assert measured.sessions <= SMALL_SESSIONS
    assert measured.model_calls <= SMALL_MODEL_CALLS


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
    assert small_new_project_pipeline(monkeypatch).always_on_tokens <= ALWAYS_ON_TOKENS


# -- the team strategy (T12) ---------------------------------------------------------------------


def test_a_small_team_run_does_not_grow(monkeypatch: pytest.MonkeyPatch) -> None:
    within(small_new_project_team(monkeypatch), TEAM_SMALL)


def test_a_medium_team_run_does_not_grow(monkeypatch: pytest.MonkeyPatch) -> None:
    within(medium_new_project_team(monkeypatch)[0], TEAM_MEDIUM)


def test_a_small_task_the_lead_does_alone_is_one_session(monkeypatch: pytest.MonkeyPatch) -> None:
    measured = small_new_project_team(monkeypatch)

    assert measured.sessions == 1
    assert measured.model_calls <= SMALL_MODEL_CALLS
    assert measured.ceremony_stages == 0


def test_a_medium_task_has_sessions_only_for_the_teammates_the_lead_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    measured, called = medium_new_project_team(monkeypatch)

    assert called == ["backend_engineer", "frontend_engineer", "code_reviewer"]
    assert measured.sessions == 1 + len(called)  # the lead, and one session per job it gave out
    assert measured.schema_tokens <= SCHEMA_TOKENS  # the lead's team tools included
