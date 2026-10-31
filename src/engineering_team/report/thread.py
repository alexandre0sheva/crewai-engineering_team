"""The thread a run is in: the run it follows and the follow-ups it got.

Read from the manifests next to the run (the runs of one workspace), so the report links the
parent and the children that share its repository; a follow-up that went to another workspace
(a plan implemented in a new project) is not listed here, ``runs --thread`` finds it.
"""

from __future__ import annotations

from pathlib import Path

from engineering_team.contracts import RunManifest
from engineering_team.report.html_common import badge, esc, section, table
from engineering_team.report.model import RunLink, ThreadInfo
from engineering_team.runtime.run_store import RunStore

MAX_HEAD = 100


def _link(store: RunStore, manifest: RunManifest) -> RunLink:
    try:
        text = (store.run_dir(manifest.run_id) / "request.md").read_text(encoding="utf-8")
    except OSError:
        text = ""
    head = " ".join(text.split("\n---\n")[0].split())
    return RunLink(manifest.run_id, manifest.status, manifest.verdict, head[:MAX_HEAD])


def build_thread(run_dir: Path, manifest: RunManifest) -> ThreadInfo | None:
    """The run's parent and children, or ``None`` for a run that is not in a thread."""

    store = RunStore(run_dir.parents[2])
    runs = {m.run_id: m for m in store.list_runs()}
    children = [m for m in runs.values() if m.parent_run == manifest.run_id]
    if manifest.parent_run is None and not children:
        return None
    parent_id = manifest.parent_run
    parent = None
    if parent_id is not None:
        found = runs.get(parent_id)
        parent = _link(store, found) if found else RunLink(parent_id, "elsewhere", None, "")
    return ThreadInfo(
        thread_id=manifest.thread_id or manifest.run_id,
        parent=parent,
        children=[_link(store, m) for m in children],
    )


def shown(link: RunLink) -> str:
    verdict = f", {link.verdict}" if link.verdict else ""
    return f"{link.status}{verdict}"


def html(thread: ThreadInfo | None) -> str:
    if thread is None:
        return ""
    rows = [("Follows", thread.parent), *(("Followed up by", c) for c in thread.children)]
    body = table(
        ["", "Run", "Status", "Request"],
        [
            [esc(role), f"<code>{esc(link.run_id)}</code>", badge(shown(link)), esc(link.request)]
            for role, link in rows
            if link is not None
        ],
    )
    return section("thread", "Thread", f"<p>Thread <code>{esc(thread.thread_id)}</code></p>{body}")
