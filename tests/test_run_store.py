from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from engineering_team.contracts import RunManifest, StageRecord
from engineering_team.runtime.run_store import (
    InvalidTransition,
    RunNotFound,
    RunStore,
    atomic_write_json,
    atomic_write_text,
    list_runs,
)


def _manifest(run_id: str = "20260101-000000-aaaaaa", **fields: object) -> RunManifest:
    return RunManifest(run_id=run_id, project_name="demo", **fields)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path)


def test_a_created_run_can_be_loaded_listed_and_found_as_latest(store: RunStore) -> None:
    store.create(_manifest("20260101-000000-aaaaaa"))
    store.create(_manifest("20260102-000000-bbbbbb"))

    assert store.load("20260101-000000-aaaaaa").project_name == "demo"
    assert [run.run_id for run in store.list_runs()] == [
        "20260101-000000-aaaaaa",
        "20260102-000000-bbbbbb",
    ]
    assert store.latest() is not None and store.latest().run_id == "20260102-000000-bbbbbb"  # type: ignore[union-attr]
    assert store.manifest_path("20260101-000000-aaaaaa").parent == (
        store.runs_dir / "20260101-000000-aaaaaa"
    )


def test_an_empty_store_has_no_runs(store: RunStore, tmp_path: Path) -> None:
    assert store.list_runs() == [] and store.latest() is None
    assert list_runs(tmp_path) == []
    with pytest.raises(RunNotFound):
        store.load("missing")


def test_listing_skips_directories_that_are_not_runs(store: RunStore) -> None:
    store.create(_manifest())
    (store.runs_dir / "junk").mkdir()
    (store.runs_dir / "corrupt").mkdir()
    (store.runs_dir / "corrupt" / "manifest.json").write_text("{not json", encoding="utf-8")

    assert [run.run_id for run in store.list_runs()] == ["20260101-000000-aaaaaa"]


def test_creating_the_same_run_twice_is_an_error(store: RunStore) -> None:
    store.create(_manifest())

    with pytest.raises(ValueError, match="already exists"):
        store.create(_manifest())


def test_status_follows_pending_running_then_an_end_state(store: RunStore) -> None:
    store.create(_manifest())

    running = store.set_status("20260101-000000-aaaaaa", "running")
    assert running.status == "running" and running.finished is None
    done = store.set_status("20260101-000000-aaaaaa", "succeeded")

    assert done.status == "succeeded" and done.finished is not None
    assert store.load("20260101-000000-aaaaaa").finished == done.finished


@pytest.mark.parametrize("end", ["succeeded", "failed", "cancelled", "interrupted"])
def test_every_documented_end_state_is_reachable_from_running(store: RunStore, end: str) -> None:
    store.create(_manifest())
    store.set_status("20260101-000000-aaaaaa", "running")

    assert store.set_status("20260101-000000-aaaaaa", end).status == end  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("start", "target"),
    [
        ("pending", "succeeded"),  # must run first
        ("pending", "interrupted"),
        ("running", "pending"),
        ("running", "running"),
        ("succeeded", "running"),  # terminal states are final
        ("failed", "succeeded"),
        ("cancelled", "failed"),
    ],
)
def test_invalid_status_changes_are_rejected_and_leave_the_manifest_alone(
    store: RunStore, start: str, target: str
) -> None:
    run_id = "20260101-000000-aaaaaa"
    store.create(_manifest(run_id, status="pending"))
    path = {"pending": [], "running": ["running"]}.get(start, ["running", start])
    for step in path:
        store.set_status(run_id, step)  # type: ignore[arg-type]
    before = store.manifest_path(run_id).read_text(encoding="utf-8")

    with pytest.raises(InvalidTransition):
        store.set_status(run_id, target)  # type: ignore[arg-type]

    assert store.manifest_path(run_id).read_text(encoding="utf-8") == before


def test_a_pending_run_can_be_cancelled_or_failed_before_it_starts(store: RunStore) -> None:
    store.create(_manifest("20260101-000000-aaaaaa"))
    store.create(_manifest("20260101-000000-bbbbbb"))

    assert store.set_status("20260101-000000-aaaaaa", "cancelled").finished is not None
    assert store.set_status("20260101-000000-bbbbbb", "failed").status == "failed"


def test_stage_records_are_upserted_in_first_seen_order(store: RunStore) -> None:
    run_id = "20260101-000000-aaaaaa"
    store.create(_manifest(run_id))

    store.record_stage(run_id, StageRecord(name="spec", status="running"))
    store.record_stage(run_id, StageRecord(name="build"))
    store.record_stage(run_id, StageRecord(name="spec", status="succeeded", attempts=1))

    stages = store.load(run_id).stages
    assert [(stage.name, stage.status) for stage in stages] == [
        ("spec", "succeeded"),
        ("build", "pending"),
    ]


def test_atomic_write_replaces_without_leaving_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "file.json"

    atomic_write_json(target, {"a": 1})
    atomic_write_text(target, "second\n")

    assert target.read_text(encoding="utf-8") == "second\n"
    assert [path.name for path in target.parent.iterdir()] == ["file.json"]


def test_a_failed_write_keeps_the_previous_file_intact(tmp_path: Path) -> None:
    directory = tmp_path / "run"
    target = directory / "manifest.json"
    atomic_write_json(target, {"ok": True})

    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert [path.name for path in directory.iterdir()] == ["manifest.json"]


def test_concurrent_writers_never_lose_updates_or_expose_partial_files(
    store: RunStore, tmp_path: Path
) -> None:
    run_id = "20260101-000000-aaaaaa"
    store.create(_manifest(run_id))
    store.set_status(run_id, "running")
    writers, per_writer = 8, 25
    stop = threading.Event()
    problems: list[str] = []

    def write(worker: int) -> None:
        own = RunStore(tmp_path)  # separate instances in one process share the lock
        for round_number in range(per_writer):
            own.record_stage(
                run_id, StageRecord(name=f"w{worker}-{round_number}", attempts=round_number)
            )

    def read() -> None:
        while not stop.is_set():
            try:
                RunManifest.model_validate_json(store.manifest_path(run_id).read_text("utf-8"))
            except Exception as exc:  # a torn read would fail validation
                problems.append(repr(exc))

    readers = [threading.Thread(target=read) for _ in range(2)]
    threads = [threading.Thread(target=write, args=(n,)) for n in range(writers)]
    for thread in [*readers, *threads]:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    stop.set()
    for thread in readers:
        thread.join(timeout=10)

    assert problems == []
    assert len(store.load(run_id).stages) == writers * per_writer
