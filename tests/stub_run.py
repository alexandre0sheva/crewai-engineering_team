"""Stand-in for ``python -m engineering_team``: same arguments, no model."""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

from engineering_team.board.store import BoardStore
from engineering_team.contracts import RunManifest, StageRecord, utc_now
from engineering_team.runtime.cancel import cancel_flag_path, clear_cancel_flag
from engineering_team.runtime.events import JsonlSink
from engineering_team.runtime.inbox import inbox_watch
from engineering_team.runtime.interaction import HumanChannel
from engineering_team.runtime.locks import WorkspaceBusy, WorkspaceLock
from engineering_team.runtime.run_store import RunStore


def value(flag, default=None):
    """The option's value, written ``--flag value`` or ``--flag=value``."""
    for index, arg in enumerate(sys.argv):
        if arg == flag and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return default


root = Path(value("--workspace-root"))
command = next(
    a for a in sys.argv[1:] if a in ("new", "feature", "fix", "maintain", "review", "resume")
)
resuming = command == "resume"
run_id = sys.argv[sys.argv.index("resume") + 1] if resuming else value("--run-id")
project = value("--project-name", "demo")
request = ""
if value("--request-file"):
    request = Path(value("--request-file")).read_text()
workspace = root / project
workspace.mkdir(parents=True, exist_ok=True)
if "FAIL-AT-ONCE" in request:
    sys.exit("The stub was told to fail at once.")
try:
    lock = WorkspaceLock(workspace).acquire(run_id)
except WorkspaceBusy as exc:
    sys.exit(f"Error: {exc}")
store = RunStore(workspace)
clear_cancel_flag(workspace, run_id)  # a started or resumed run begins uncancelled
if resuming:
    store.update(run_id, lambda m: setattr(m, "resumes", m.resumes + 1))
    store.set_status(run_id, "running")  # a failed/cancelled run may run again
else:
    store.create(
        RunManifest(run_id=run_id, project_name=project, mode=command, strategy="pipeline")
    )
    store.set_status(run_id, "running")
run_dir = store.run_dir(run_id)
sink = JsonlSink(run_dir / "events.jsonl", run_id)
board = BoardStore(run_dir, sink, run_id=run_id, render_delay=0)
human = HumanChannel(sink, interactive="--answers-via-inbox" in sys.argv)
ctx = SimpleNamespace(run_dir=run_dir, run_id=run_id, board=board, human=human, events=sink)
sink.emit("run.started", mode=command)
steps = 400 if "SLOW" in request else 8
outcome = "succeeded"
with inbox_watch(ctx):
    if resuming:
        card = board.cards()[0]
    else:
        card = board.create_card(
            "Build the thing",
            kind="work_package",
            assignee="backend",
            actor="controller",
            status="ready",
        )
    if card.status != "in_progress":
        board.move(card.id, "in_progress", actor="backend")
    store.record_stage(
        run_id, StageRecord(name="implement", status="running", started=utc_now(), attempts=1)
    )
    sink.emit("stage.started", stage="implement")
    if "ASK" in request:
        answer = human.ask("Which database?", agent="backend", card_id=card.id, timeout=30)
        sink.emit("note.written", answer=answer)
    for step in range(steps):
        if cancel_flag_path(workspace, run_id).exists():
            outcome = "cancelled"
            break
        sink.emit(
            "tool.call",
            agent="backend",
            tool="Write File",
            args={"path": f"f{step}.py"},
            duration=0.01,
            ok=True,
        )
        time.sleep(0.05)
    if outcome == "succeeded":
        board.move(card.id, "verifying", actor="backend")
        board.move(card.id, "done", actor="controller", stage_success=True)
    sink.emit("stage.finished", stage="implement", status=outcome)
board.flush()
sink.emit("run.finished", status=outcome)
store.record_stage(
    run_id,
    StageRecord(
        name="implement", status=outcome, started=utc_now(), finished=utc_now(), attempts=1
    ),
)
store.set_status(run_id, outcome)
lock.release()
sys.exit(130 if outcome == "cancelled" else 0)
