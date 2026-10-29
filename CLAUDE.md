# CLAUDE.md

Project: `engineering-team` — a CrewAI-based AI engineering team (see `README.md`). Released
state is **0.2.0** (not tagged yet); all current work targets **0.3.0**.

## Implementation plan — "implement task N"

The single implementation plan is **`docs/IMPLEMENTATION_PLAN_0.3.0.md`** (34 numbered tasks,
index in §5, rules in §4). When the user says "implement task N" (or "do task N", "next task"):

1. Run `uv run python scripts/plan_task.py show N` (or `next` to find the next runnable task).
   It prints the rules for every task, the task block, and the status of its dependencies.
   `uv run python scripts/plan_task.py list` shows all tasks with statuses.
2. If a dependency is not `done`, stop and tell the user.
3. Implement only that task, following the rules (hermetic tests, docs updated per the
   Documentation map, `CHANGELOG.md` entry under `[Unreleased]`, no duplicated docs).
4. Run the gate, then `uv run python scripts/plan_task.py done N`. A task with a live run (plan §4
   *Live spend cap*): make the run yourself as part of the task. Before the first live command, ask
   the user to check the balance of each provider it bills (name them, the estimate and the
   allocation left) and wait for the reply; never leave a live run to the user.
   Large downloads (Docker images, datasets, toolchains): fetch them again when the task needs
   them, and remove what the task fetched before marking it done (plan §4 *Large downloads and
   disk space*).
5. **Never `git commit`, `git push`, tag or publish** — the user commits manually. Finish with
   files changed, gate results, decisions (also append deviations to the plan's Decision log)
   and a suggested commit message.

Before touching CrewAI code, follow the research steps in `AGENTS.md` (installed version, PyPI,
changelog, relevant docs page).

## Basics

- `uv sync --group dev` · `uv run pytest -q` · `uv run ruff check .` · `uv run ruff format --check .` · `uv run mypy`
- Source in `src/engineering_team/`; tests in `tests/` must stay offline and hermetic.
- Never read or print `.env` (it holds API keys).
