from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from engineering_team import contracts
from engineering_team.contracts import (
    AcceptanceCriterion,
    CheckResult,
    CheckSpec,
    Event,
    Finding,
    Plan,
    ProjectCommands,
    RunManifest,
    Spec,
    StageRecord,
    WorkPackage,
)

ALL_MODELS = [
    AcceptanceCriterion,
    Spec,
    ProjectCommands,
    WorkPackage,
    Plan,
    CheckSpec,
    CheckResult,
    Finding,
    StageRecord,
    RunManifest,
    Event,
]

SAMPLES = [
    AcceptanceCriterion(id="C1", text="A user can add a note", kind="functional"),
    Spec(
        title="Notes",
        summary="A tiny notes app",
        criteria=[AcceptanceCriterion(id="C1", text="Add a note")],
        non_goals=["sync"],
        assumptions=["single user"],
        open_questions=["auth?"],
    ),
    ProjectCommands(setup=["uv sync"], test=["uv run pytest -q"], run=["uv run app"]),
    WorkPackage(
        id="WP1",
        title="API",
        role="backend_engineer",
        owned_paths=["src/api/**"],
        depends_on=["WP0"],
        criteria_ids=["C1"],
    ),
    Plan(
        stack="FastAPI",
        commands=ProjectCommands(test=["pytest"]),
        work_packages=[WorkPackage(id="WP1", title="API", role="backend_engineer")],
        risks=["scope"],
    ),
    CheckSpec(
        id="tests", name="Unit tests", argv=["pytest", "-q"], timeout=60, criteria_ids=["C1"]
    ),
    CheckResult(
        id="tests",
        status="passed",
        exit_code=0,
        duration=1.5,
        log_path="commands/1.log",
        revision="abc123",
        started_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    ),
    Finding(id="F1", severity="high", summary="SQL injection", file="app.py", line=12),
    StageRecord(name="build", status="succeeded", attempts=1, artifacts=["docs/architecture.md"]),
    RunManifest(
        run_id="20260102-030405-abcdef",
        project_name="notes",
        versions={"python": "3.12"},
        stages=[StageRecord(name="build")],
    ),
    Event(
        seq=1,
        ts=datetime(2026, 1, 2, tzinfo=UTC),
        run_id="r",
        type="tool.call",
        agent="Backend",
        lane=2,
        data={"ok": True},
    ),
]


@pytest.mark.parametrize("model", ALL_MODELS)
def test_every_contract_carries_a_schema_version(model: type[contracts.Contract]) -> None:
    assert model.model_fields["schema_version"].default == 1


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: type(sample).__name__)
def test_contracts_round_trip_through_json(sample: contracts.Contract) -> None:
    text = sample.model_dump_json()

    assert type(sample).model_validate_json(text) == sample
    assert json.loads(text)["schema_version"] == 1


def test_every_model_has_a_round_trip_sample() -> None:
    assert {type(sample) for sample in SAMPLES} == set(ALL_MODELS)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: type(sample).__name__)
def test_unknown_fields_from_a_newer_version_are_ignored(sample: contracts.Contract) -> None:
    payload = json.loads(sample.model_dump_json())
    payload["added_in_a_future_release"] = {"anything": [1, 2, 3]}
    payload["schema_version"] = 2

    loaded = type(sample).model_validate(payload)

    assert not hasattr(loaded, "added_in_a_future_release")
    assert loaded.schema_version == 2
    assert loaded.model_dump(exclude={"schema_version"}) == sample.model_dump(
        exclude={"schema_version"}
    )


def test_defaults_keep_agent_output_cheap_to_validate() -> None:
    spec = Spec.model_validate({"title": "Only a title"})
    plan = Plan.model_validate({})
    manifest = RunManifest(run_id="r", project_name="p")

    assert spec.criteria == [] and plan.work_packages == [] and plan.commands.test == []
    assert manifest.status == "pending" and manifest.finished is None
    assert manifest.created.tzinfo is not None


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (CheckResult, {"id": "x", "status": "exploded"}),
        (Finding, {"id": "x", "severity": "catastrophic", "summary": "s"}),
        (RunManifest, {"run_id": "r", "project_name": "p", "status": "done"}),
        (StageRecord, {"name": "s", "status": "meh"}),
        (Spec, {"summary": "no title"}),
        (CheckSpec, {"id": "x", "name": "n"}),
    ],
)
def test_invalid_values_and_missing_required_fields_are_rejected(
    model: type[contracts.Contract], payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_the_check_statuses_are_the_documented_four() -> None:
    for status in ("passed", "failed", "skipped", "unavailable"):
        assert CheckResult(id="c", status=status).status == status  # type: ignore[arg-type]
