# Implementation plan — version 0.2.0

**Goal of 0.2.0:** turn the 0.1.0 prototype (a greenfield-only, fixed-pipeline CrewAI crew)
into a genuinely useful open-source AI engineering team that can build a new project from a
requirements file or text box, or connect to an existing/legacy repository to add features,
fix bugs and maintain code — with parallel execution, independent verification, cost
control, a polished CLI and web UI, and published benchmarks.

Written 2026-10-02 against `main` @ `0ba55a3` (= release 0.1.0). Installed CrewAI 1.15.9,
latest on PyPI 1.15.23.

## Quick start (read this first — also for a fresh Claude Code chat)

This file is the single implementation plan for 0.2.0. When the user says *"implement task N"*
(in any phrasing: "do task N", "task N", "next task"):

1. Run `uv run python scripts/plan_task.py show N` — it prints the **rules for every task**
   (§4), the task block, and the status of its dependencies. (Without the script: read §4, then
   the section whose heading is exactly `#### Task N — …` in §6.) For "next task", run
   `uv run python scripts/plan_task.py next`.
2. Stop if a dependency is not `done` in the index (§5) and tell the user.
3. Implement exactly that task, then run `uv run python scripts/plan_task.py done N` to mark it
   done in the index, and report as described in §4.

`uv run python scripts/plan_task.py list` shows the whole index with statuses. Each task is
self-contained and sized for one Sonnet/Opus session. **Nothing is committed or pushed by the
tasks** — the user reviews and commits manually after each one.

Contents: [1. Current version](#1-current-version-010) · [2. Target version](#2-target-version-020) ·
[3. Target architecture](#3-target-architecture) · [4. Rules for every task](#4-rules-for-every-task) ·
[5. Task index](#5-task-index) · [6. Tasks](#6-tasks) · [7. Decision log](#7-decision-log)

---

## 1. Current version (0.1.0)

### What it is

`engineering-team` is a CrewAI project: a tool-free `engineering_lead` manager (hierarchical
process) delegates six fixed tasks — architecture → foundation → backend → frontend →
quality → release — to four generic specialists. Specialists get six filesystem/command tools
scoped to `workspace/<project>/`. About 1,050 lines of Python, 20 offline tests, all green;
Ruff clean; CrewAI 1.15.9.

### What works well (keep)

- Small readable code; agents/tasks in YAML; model tiers (flagship lead, cheap workers) and
  a cheap `smoke` profile.
- Persistent normal project directories; explicit `--reset`; `--prepare-only` dry path.
- Careful tool design: path containment, exact-replace edits, secret-stripped environment,
  uncached mutating tools, no shell, honest "not a sandbox" documentation.
- Tests that build the real hierarchical manager without LLM calls.

### Findings (verified by reading the code, 2026-10-02)

| # | Finding | Evidence | Consequence | Fixed in |
|---|---------|----------|-------------|----------|
| F1 | Guardrails only test `is_file()` | `crew.py:_require_workspace_files` | An empty `verification.md` "proves" QA; a stale file from a previous run passes. Success is not evidence. | T18 (T3 interim) |
| F2 | Symlink alias can reach `.git` | `workspace_tools.py:resolve` checks `.git` only on the *lexical* path | Documented `.git` protection is bypassable via `link -> .git` | T3 |
| F3 | Inline env request beats `--request-file` | `main.py:load_requirements` line 46 | CLI names one brief, run uses another | T3 |
| F4 | `PROJECT_ROOT = parents[2]` | `main.py:22-24` | Installed wheel looks for a missing request file; workspaces land next to site-packages | T3 |
| F5 | `--reset` deletes any `<root>/<slug>` with no ownership check | `main.py:prepare_workspace` | A wrong `ENGINEERING_WORKSPACE_ROOT` can delete an unrelated directory | T3 |
| F6 | Global mutable workspace + `os.environ` mutation for profile | `_active_workspace`, `os.environ["ENGINEERING_RUN_PROFILE"]` | No concurrent runs, no parallel agents, hidden coupling, flaky tests | T4, T5 |
| F7 | Every run restarts all 6 stages | `run()` always `kickoff`s | Re-running pays full cost again; no resume after failure/Ctrl-C | T8, T16 |
| F8 | Fixed linear plan; `frontend_task` always runs | `tasks.yaml` | CLI/API projects waste a stage; no parallelism; manager LLM adds delegation overhead of unknown value | T16, T17 |
| F9 | Command output captured fully, then truncated; timeout kills only the direct child | `workspace_tools.py:run_command` | Output floods use memory; grandchildren survive a timeout | T7 |
| F10 | No usage/cost/time reporting or budgets | — | Unpredictable spend | T9 |
| F11 | Greenfield only | — | Cannot adopt a legacy repo, add a feature, fix a bug | T25–T28 |
| F12 | CLI only, raw CrewAI console, generic exit handling (`RuntimeError` wrap) | `main.py:run` | Poor UX, no history, no reports | T21, T29–T31 |
| F13 | Hard-coded model IDs for one provider; settings scattered across `os.getenv` | `crew.py` | Lock-in, hard to extend | T4 |
| F14 | No LICENSE, CI, contributor docs, changelog | — | Not reusable/contributable as open source | T2 (LICENSE and CHANGELOG already added) |
| F15 | Tests need a writable CrewAI storage dir and depend on ambient env | `tests/` (two failures in a read-only home) | Non-hermetic | T1 |
| F16 | Agents have only six tools (list/read/write/replace/delete/run) | `tools/workspace_tools.py` | No search, structured test/lint results, background servers, HTTP checks, browser verification, docs/web lookup, code navigation, or shared notes — agents burn iterations on guesswork and cannot verify UIs/APIs | T7, T11–T15 |
| F17 | Progress is only raw CrewAI console text | — | No view of who is doing what, what is blocked, or how far along the run is; no human steering | T10, T21, T29–T32 |

Also noticed: `run_command` has an executable allowlist but generated programs run on the host
(documented; addressed by T20); `sandbox/` and `workspace/*` hold stale outputs from earlier
runs (git-ignored; delete at your convenience); `pyproject.toml` already says `0.2.0` — that is
intentional: it is the in-development version, and `v0.1.0` should be tagged on `0ba55a3`
(`git tag -a v0.1.0 0ba55a3 -m "0.1.0"`).

---

## 2. Target version (0.2.0)

### Capability matrix

| Area | 0.1.0 | 0.2.0 |
|------|-------|-------|
| Project sources | New project from file/inline/env | **New**, **existing/legacy repo** (feature, bug fix, maintenance, review, analysis) |
| Requirements input | File, `--request`, env | File(s), stdin, inline, text box in web UI, context docs, templates, guided clarification |
| Team | 4 generalists + manager | Registry of ~11 teammates (product analyst, architect, backend, frontend, QA, debugger, code reviewer, security, DevOps, tech writer, codebase analyst); user-defined teammates via YAML |
| Orchestration | One hierarchical crew, 6 fixed tasks | Typed **Flow pipeline** driven by *recipes* (data), planner-chosen work packages; hierarchical kept as a selectable strategy and a single-agent baseline for benchmarking |
| Parallelism | None | Parallel work packages with write-scope ownership, parallel reviewers, parallel codebase analysis, concurrent runs, parallel benchmarks |
| "Done" means | Files exist | **Independently executed checks** tied to spec criteria and the exact workspace revision; bounded verify→repair loop |
| Resilience | Restart from zero | Run IDs, manifests, event log, per-stage checkpoints, `resume`, `cancel`, git checkpoints |
| Agent tools | 6 (list, read, write, replace, delete, run) | **~70 tools in 13 groups**: search/read-range/batch-read/patch/outline/repo-map, structured test/lint/typecheck/build/format/coverage runners, background processes + port/HTTP/SQLite checks, code intelligence (symbols, references, import graph, hotspots), web search/fetch/package info (opt-in), headless browser (snapshot/screenshot/click), git info, shared notes, task board, ask-human — all registered in one catalogue (`docs/TOOLS.md`) |
| Progress visibility | Raw console text | **Task board (kanban)** the controller keeps truthful and agents update; live in the CLI and the web UI with agent presence cards, activity feed, parallel-lane timeline, progress %, blocked reasons, and human steering notes |
| Safety | Project-scoped tools on host | Ownership sentinel, post-resolution path protection, streaming-capped/killable commands, optional **Docker sandbox**, git branch/worktree isolation for legacy repos, never pushes |
| Cost control | None | Token/cost accounting, price table with provenance, budgets (cost/tokens/time/tool calls) |
| Models | One provider family hard-coded | Typed settings, provider presets (OpenAI/Anthropic/Google/Ollama), per-role overrides |
| UX | CLI + raw logs | Typer/Rich CLI with live parallel-lane progress, `doctor`, `init`, run history; self-contained HTML run report; local web UI (textarea/file upload, mode picker, repo picker, **live kanban dashboard**, diff viewer, history) |
| Extensibility | Edit YAML/code | Teammate YAML, recipes, MCP per role, plugin tools, repo conventions auto-loaded, hooks |
| Evidence | None | Benchmark suite (greenfield + legacy fixtures) with hidden acceptance checks, strategy comparison, published results incl. failures |
| OSS hygiene | — | MIT, CI matrix, CONTRIBUTING, SECURITY, templates, Dependabot, release workflow |

### Vector of improvement

1. **Trust** (F1, F2, F5, T18, T20): from "agents say it works" to "controller proved it works".
2. **Usefulness** (F11, T25–T28): the largest real-world demand is changing existing code.
3. **Efficiency** (F7, F8, F10, T9, T16, T17): resume instead of restart, parallelise independent
   work, spend visibly and within budget.
4. **Capability** (F16, T7, T11–T15): agents get the tools a real engineer uses — search, run tests and read results, run servers, call APIs, drive a browser, look things up.
5. **Experience** (F12, F17, T10, T21, T29–T32): you can see what the team is doing and why, live, and steer it.
6. **Credibility** (T33–T34): measured claims; honest comparison of strategies.

### Explicit non-goals for 0.2.0

Hosted service/accounts/multi-tenant, auto-deploy, pushing to remotes or opening PRs,
long-term vector memory, fine-tuning, mobile device runners, a general plugin marketplace.
(`feature`/`fix` produce a branch + patch; you push.)

---

## 3. Target architecture

```text
CLI (Typer/Rich) ─┐
Web UI (FastAPI) ─┼─►  Run controller ──► Recipe (stage list) ──► Flow pipeline (typed state)
Trigger/legacy ───┘         │                                         │  stages = small crews
                            │                                         ├─ parallel work packages (write-scoped)
   Settings (T4)            │                                         ├─ parallel reviewers / analysts
   RunContext (T5)          ▼                                         ▼
   Contracts (T8)      Run store + event log ◄── usage/budget (T9) ◄── CrewAI event bridge
                            │
                    Verifier (controller-run checks, T18) ◄─ ExecutionBackend: Local | Docker (T7/T20)
                            │
                    GitPort (T19): init / branch / worktree / checkpoint / patch
                            │
                    Report (T29) · UI API (T30) · Dashboard (T31, T32)

   Task board (T10): cards created/moved by the controller from stage events; agents update via the
                     Task Board tool; every move is an event → CLI kanban (T21) and UI dashboard (T32)

   Tool catalogue (docs/TOOLS.md), assigned to teammates by group, all execution via ExecutionBackend:
     fs_read · fs_write · search (T7) · board/notes/human (T10) · dev: tests/lint/build (T11)
     runtime: processes/HTTP/DB (T12) · code_intel (T13) · web (T14) · browser (T15) · git_read (T19)
```

Suggested layout (adapt if there is a good reason; record deviations in the Decision log and
`docs/ARCHITECTURE.md`):

```text
src/engineering_team/
  settings.py                 # typed settings, presets            (T4)
  contracts.py                # Pydantic: Spec, Plan, CheckResult…  (T8)
  runtime/                    # context, run_store, events, usage, budget, locks (T5,T8,T9)
  tools/                      # registry.py (groups), workspace.py, commands.py, search.py, patch.py, factory.py (T5,T7)
                              # board_tools.py, notes.py, human.py (T10) · runtime_tools.py (T12)
                              # code_intel.py (T13) · web_tools.py (T14) · browser_tools.py (T15)
  devtools/                   # test/lint/typecheck/build/format/coverage runners + result parsers (T11; reused by T18)
  board/                      # Card/Board models, BoardStore, transition rules, board.md export (T10)
  execution/                  # backend.py, local.py, docker.py     (T7,T20)
  git/                        # port.py                             (T19)
  verification/               # verifier.py, profiles.py            (T18)
  pipeline/                   # flow.py, stages.py, parallel.py, strategies.py, recipes.py (T16,T17)
  team/                       # registry.py + config/team/*.yaml    (T23,T24)
  modes/                      # adopt.py, repo_analyzer.py, recipes/*.yaml (T25–T28)
  reporting/                  # report.py + template               (T29)
  ui/                         # server.py, static/ (shell T31, live dashboard T32) (T30–T32)
  cli/                        # app.py, commands, console.py        (T21)
  testing/                    # fake_llm.py, fake team helpers      (T6)
  config/                     # existing agents.yaml/tasks.yaml (kept for the hierarchical strategy)
benchmarks/                   # tasks + harness (not in the wheel)  (T33)
examples/                     # curated examples                    (T3, T36)
```

Design principles: controller is deterministic code, LLMs do bounded work inside stages;
state lives on disk (resumable); every cross-stage handoff is a typed contract; one small
abstraction per seam (Strategy, ExecutionBackend, GitPort, Recipe) — no plugin framework
before a second implementation needs it.

---

## 4. Rules for every task

Read this section before starting any task.

### Execution protocol

1. Read this section, the task, and the docs it names. Check the **Depends on** tasks are `done`
   in the index; if not, stop and say so.
2. Before touching CrewAI code, follow `AGENTS.md` "Mandatory: research before writing CrewAI
   code" (installed version, PyPI, changelog, relevant docs page). Live docs beat this plan if
   they conflict; record the difference in the Decision log.
3. Work test-first where practical (`superpowers:test-driven-development`): failing test →
   implementation → green. Every bug fix gets a regression test.
4. Run the full gate (below) before declaring done.
5. Update documentation per the **Documentation map** (in `CONTRIBUTING.md`; `plan_task.py show` prints it) and add a `CHANGELOG.md` entry under
   `[Unreleased]` (Added/Changed/Fixed/Security; one line per user-visible change, no
   implementation chatter).
6. Mark the task done with `uv run python scripts/plan_task.py done N` (sets `done (YYYY-MM-DD)` in
   the index) and add any deviation to the Decision log.
7. **Do not `git commit`, `git push`, tag, or publish.** End with: files changed, gate output
   summary, decisions made, follow-ups, and a suggested commit message.

### Gate (all must pass)

```bash
uv sync --locked --group dev      # the lockfile is authoritative after T1
uv run pytest -q                  # offline, no network, no LLM, no ambient .env
uv run ruff check . && uv run ruff format --check .
uv run mypy                       # blocking since T2
uv build                          # CI also installs the wheel in a clean venv and runs `engineering-team --help` from another directory
```

### Global constraints

- Python `>=3.11,<3.14` (3.14 is excluded because CrewAI itself declares `<3.14`; widen the range and the CI matrix when a CrewAI release supports it); `uv`; Ruff (line length 100, rules `E,F,I,UP,B,SIM`); `from __future__ import annotations`; full type hints on new code.
- Tests are hermetic: no network, no LLM calls, no reading the developer's `.env`/`~/.config`, temp dirs only, isolated `CREWAI_STORAGE_DIR`. Tests that need Docker or live LLMs are marked (`docker`, `live`) and skipped by default.
- Never read, print, log, or commit secrets. Never print `.env` contents.
- Controller code never trusts agent-authored text as evidence (see T18).
- Keep modules focused (aim < 400 lines); split by responsibility.
- Backward compatibility: the 0.1.0 invocation `engineering-team --request-file …` keeps working (with a deprecation notice after T21) until at least 0.3.0. `crewai run/train/replay/test` entry points must keep working (the CrewAI CLI calls those script names).
- Subprocess use must avoid shells (`shell=False`); anything touching the filesystem outside the project must be explicit and tested.
- Parallel code must be deterministic in what it records (stable ordering in reports) even if execution order varies.
- Prefer the simplest thing; do not add dependencies without a line in the Decision log saying why. New runtime deps must be declared in `pyproject.toml` (do not rely on transitive ones).

### Tool design rules (apply to every agent tool, T7 and T10–T15)

- Register each tool in the catalogue (`tools/registry.py`) with: `name`, `group`, `read_only`, `needs_network`, `needs_command_gate`, one-line `summary`; add its row to `docs/TOOLS.md`.
- **All execution goes through `ExecutionBackend`** (so the Docker sandbox in T20 covers it); no tool calls `subprocess` directly.
- Descriptions are written for an LLM: *when to use it*, argument meaning, limits, one short example. Keep them under ~120 words.
- Results are compact and structured (counts first, then details), bounded in size, and point to a full log path when truncated. Errors start with `ERROR:` and say how to fix the call (valid ranges, similar paths, the exact flag).
- Every call emits a `tool.call` event (tool, redacted/truncated args, duration, ok) — it powers the activity feed and agent cards (T32). Respect `ctx.cancel_event` and the tool-call budget (T9).
- Read-only tools must be usable by read-only teammates; write-scope (T5) and path protection (T3) apply everywhere; network tools are off unless enabled in settings.
- Tool output from the web or from the repo is **untrusted data**: never let it change permissions or instructions (see T14).
- Every tool has unit tests with a temp `RunContext`, and at least one test through `ScriptedLLM` proving an agent can call it.

### Documentation map

The map of which file owns which topic lives in [CONTRIBUTING.md](../CONTRIBUTING.md#documentation-map); update the canonical file and link to it, never duplicate.

---

## 5. Task index

Model: **O** = Opus recommended (design-heavy/concurrency/security); **S** = Sonnet sufficient.
Status: `todo` → `done (date)` — maintained by `scripts/plan_task.py`; the table format is
machine-read, keep one row per task and the first/last columns intact.

| # | Task | Model | Depends on | Phase | Status |
|---|------|-------|------------|-------|--------|
| 1 | Upgrade all packages; hermetic tests | S | — | A Foundation | done (2026-10-02) |
| 2 | CI, packaging metadata, community files | S | 1 | A | done (2026-10-02) |
| 3 | Fix known defects (F2–F5) + hardening | O | 1 | A | done (2026-10-02) |
| 4 | Typed settings and provider-agnostic model routing | S | 1,3 | B Core | done (2026-10-02) |
| 5 | `RunContext`, per-run tools, workspace lock (remove globals) | O | 4 | B | done (2026-10-02) |
| 6 | Offline test infrastructure (FakeLLM) | S | 5 | B | done (2026-10-02) |
| 7 | Core toolbelt: tool catalogue, search/read/patch/outline/repo-map, `ExecutionBackend` (local) | S | 5 | B | done (2026-10-02) |
| 8 | Contracts, run store, event log | O | 5 | B | done (2026-10-02) |
| 9 | Usage accounting, price table, budgets | S | 8 | B | done (2026-10-02) |
| 10 | Task board (kanban model) + coordination tools: board, notes, ask-human, progress | O | 7,8 | B | done (2026-10-02) |
| 11 | Developer tools: structured test/lint/typecheck/build/format/coverage runners | S | 7,8 | B | done (2026-10-02) |
| 12 | Runtime tools: background processes, ports, HTTP client, SQLite inspect, environment info | S | 7 | B | done (2026-10-02) |
| 13 | Code intelligence tools: symbols, references, import graph, hotspots, dependency inspect | S | 7 | B | done (2026-10-02) |
| 14 | Web & knowledge tools: search, fetch, package info, local doc search (opt-in, SSRF-safe) | S | 4,7 | B | done (2026-10-02) |
| 15 | Browser tools: headless Chromium snapshot/screenshot/click for UI verification | S | 12 | B | done (2026-10-03) |
| 16 | Flow pipeline, recipes, strategies, resume | O | 6,7,8,9,10 | B | done (2026-10-03) |
| 17 | Parallel execution engine | O | 16 | B | done (2026-10-03) |
| 18 | Independent verification + bounded repair | O | 7,11,16 | B | done (2026-10-03) |
| 19 | Git integration (`GitPort`) + Git Info tool | S | 5 | B | done (2026-10-03) |
| 20 | Docker execution backend | O | 7,12,18 | B | done (2026-10-03) |
| 21 | CLI v2 (Typer/Rich), live kanban, `doctor`, `init`, runs | S | 10,16,18 | C Product | done (2026-10-03) |
| 22 | Requirements intake + Product Analyst | S | 21 | C | done (2026-10-03) |
| 23 | Team registry, custom teammates, tool groups per teammate | S | 10,16 | C | done (2026-10-03) |
| 24 | New teammates + reviewer fan-out | S | 17,18,23 | C | done (2026-10-03) |
| 25 | Adopt existing project + codebase analysis (`analyze`) | O | 13,17,19,22,24 | C | done (2026-10-03) |
| 26 | Feature mode | S | 25 | C | done (2026-10-03) |
| 27 | Fix mode | S | 25 | C | done (2026-10-03) |
| 28 | Maintain + review modes, user recipes | S | 25,26,27 | C | done (2026-10-04) |
| 29 | Run report (HTML/Markdown) | S | 10,18,21 | D UX | done (2026-10-04) |
| 30 | Web UI — API server (runs, SSE, board, steering) | S | 10,21,22,29 | D | done (2026-10-04) |
| 31 | Web UI — app shell: new run, history, results, settings | S | 30 | D | todo |
| 32 | Web UI — live dashboard: kanban, agent cards, activity feed, lanes | S | 30,31 | D | todo |
| 33 | Benchmark harness + task suite | O | 18,20,26,27 | E Evidence | todo |
| 34 | Live evaluation, default decision (needs your API spend) | S | 33 | E | todo |
| 35 | Extensibility: MCP, conventions, plugins, hooks | S | 23,28 | E | todo |
| 36 | Docs overhaul, examples, release 0.2.0 | S | all | E | todo |

Order notes: execute in table order. After 1, tasks 2 and 3 can swap. Tasks 11–15 are independent
of each other (any order after 7/8; 15 needs 12). 19 can move earlier (after 5). 29 can precede 30.
35 is independent of 33/34.

---

## 6. Tasks

### Phase A — Foundation

#### Task 1 — Upgrade all packages; hermetic tests

**Depends on:** none · **Model:** S

**Why:** you asked for packages first; 0.1.0 pins CrewAI 1.15.9 (latest 1.15.23) and tests depend on a writable user-data dir (F15).

**Do:**
1. Follow the AGENTS.md CrewAI research steps. Read the 1.15.10–1.15.23 changelog entries (notably: tool failure surfacing, throttled provider retries, SQLite connection fixes, checkpoint features, `llm_overlay`, usage accounting).
2. In `pyproject.toml` change the pin to a compatible range (`crewai[tools]>=1.15.23,<1.16`); bump dev-group floors. Run `uv lock --upgrade` then `uv sync --group dev`. Run `uv pip list --outdated` and list what is still behind and why (CrewAI-constrained transitives) in your summary; do not force incompatible versions.
3. Fix breakage. In `crew.py` re-test the guardrail return-annotation workaround comment (`tuple[bool, Any]`): if 1.15.23 accepts it, annotate and delete the workaround comment; otherwise update the comment's version number. Investigate the `function_calling_llm` DeprecationWarning seen when building agents — if library-internal, silence only in test config (`filterwarnings` in `pyproject.toml`), with a comment.
4. Add `tests/conftest.py` with autouse fixtures: set `CREWAI_STORAGE_DIR` to `tmp_path`, `CREWAI_DISABLE_TELEMETRY=true`, `OTEL_SDK_DISABLED=true`; delete every `ENGINEERING_*` variable; run each test in a temp cwd; reset the module-level active workspace after each test.
5. Verify `uv run engineering-team --project-name x --request "Build a tiny CLI" --prepare-only` and `crewai run --help`-style entry points still import.
6. Update version facts: README line "The project uses CrewAI 1.15.9" → point to `pyproject.toml` instead of repeating a number; `docs/ARCHITECTURE.md` and `AGENTS.md` mentions of 1.15.9 updated to the installed version or generalised.

**Tests:** existing 20 pass with `env -u OPENAI_API_KEY` and a read-only `$HOME` (simulate with `HOME=$(mktemp -d)`); add a test asserting `ENGINEERING_*` env leakage cannot affect tests.
**Docs/Changelog:** Changed — "Upgraded CrewAI to 1.15.x (…) and all dependencies".
**Done when:** lockfile regenerated, tests/ruff green, no deprecation warnings originating in our code, `.env` not required to run tests.

#### Task 2 — CI, packaging metadata, community files

**Depends on:** 1 · **Model:** S

**Do:**
1. `.github/workflows/ci.yml`: matrix Python 3.11–3.13 on ubuntu, 3.12 on macOS; `astral-sh/setup-uv`; `uv sync --locked --group dev`; `ruff check`, `ruff format --check`; `pytest -q` (hermetic env); `uv build`; install the wheel into a fresh venv in a temp dir and run `engineering-team --help` from an unrelated cwd.
2. Add `[tool.ruff.format]`; format the repo in this task (noisy but isolated). Add a type checker (`mypy` or `pyright`, your choice — record it) scoped to our code with narrow third-party ignores; make it blocking only if it is clean after a reasonable pass, otherwise run it non-blocking and note the count in the Decision log.
3. `pyproject.toml` metadata: `authors`, `license = "MIT"` + `license-files`, `classifiers`, `keywords`, `[project.urls]` (Homepage/Repository/Issues/Changelog → `github.com/alexandre0sheva/crewai-engineering_team`), `readme`.
4. `CONTRIBUTING.md` (setup, gate commands, test markers, **how to work through the implementation plan with `scripts/plan_task.py`**, how tasks/commits are scoped, **the Documentation map moved from this plan** — replace section 4's table here with a link), `SECURITY.md` (private reporting via GitHub Security Advisories, scope, "generated code is untrusted" note), `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `.github/ISSUE_TEMPLATE/{bug,feature}.yml`, `.github/pull_request_template.md`, `.github/dependabot.yml` (uv + github-actions, weekly).
5. README: add CI/license/python badges only (full rewrite is T36).

**Tests:** a pytest test that reads `pyproject.toml` and asserts required metadata keys exist and `LICENSE` file is present.
**Docs/Changelog:** Added — CI, community files, license metadata.
**Done when:** workflow YAML validated (`actionlint` if available, else carefully reviewed), gate passes locally including the wheel smoke test.

#### Task 3 — Fix known defects and harden file/reset/request handling

**Depends on:** 1 · **Model:** O (security-sensitive)

**Why:** F2–F5 and interim F1 mitigation. Each fix lands with a regression test that fails before the fix.

**Do (files: `main.py`, `tools/workspace_tools.py`, `crew.py`, `examples/`, `pyproject.toml`):**
1. **Request precedence (F3):** CLI `--request` > CLI `--request-file` > `ENGINEERING_PROJECT_REQUEST` > `ENGINEERING_REQUEST_FILE` > `./PROJECT_REQUEST.md` in the *current working directory* if present > clear error naming `--example`. Test the full matrix.
2. **Packaging (F4):** remove `PROJECT_ROOT = parents[2]`. Default workspace root = `./workspace` relative to the *caller's cwd*; relative `ENGINEERING_WORKSPACE_ROOT` resolves against cwd. Move the bundled Tiny Notes request to `examples/tiny-notes/request.md` (canonical), ship it in the wheel via Hatch `force-include` to `engineering_team/examples/`, and add `--example tiny-notes` (loaded with `importlib.resources`). Delete root `PROJECT_REQUEST.md`; update README quickstart accordingly. Test from an installed wheel in an unrelated cwd (a pytest test that builds/installs is slow — instead test the resource loader plus cover the wheel check in CI from T2).
3. **Protected paths (F2):** apply protection after resolution: reject any path whose resolved location (and every intermediate component, via `os.path.realpath` / checking each `Path.is_symlink()` parent) lies in `.git`, or in `.engineering-team/` (controller-owned; agents may not write there — except a dedicated scratch dir `.engineering-team/tmp` used by commands). Apply to read/write/replace/delete/list and to command `cwd`. Tests: `link -> .git` and `docs -> .engineering-team` aliases for each operation.
4. **Reset ownership (F5):** `prepare_workspace` writes `.engineering-team/owner.json` (`{"tool": "engineering-team", "version", "created"}`) when it creates a project. `--reset` requires the sentinel unless `--force-reset`; refuses (even with force) when the target is the current repo checkout, an ancestor of cwd, `$HOME`, the filesystem root, or a symlink. Directories without a sentinel that already exist are *not adopted* silently: tell the user to pass `--adopt` (flag reserved; implemented in T25). Tests with tmp dirs.
5. **Exit handling:** `run()` returns proper exit codes — usage/config errors → 2 with a one-line message (no traceback), runtime failure → 1, Ctrl-C → 130. Keep the function names used by entry points.
6. **Interim guardrail (F1):** `_require_workspace_files` also rejects files under 40 bytes of non-whitespace text. (Replaced by real verification in T18.)

**Tests:** one regression test per item above. **Docs:** README/ARCHITECTURE for new defaults and `--example`; SAFETY notes deferred to T7. **Changelog:** Fixed ×3, Security ×2, Changed (default workspace root and request file location).
**Done when:** every listed repro from §1 is covered by a test that fails on `main` and passes now.

---

### Phase B — Core engine and agent toolbelt

#### Task 4 — Typed settings and provider-agnostic model routing

**Depends on:** 1,3 · **Model:** S

**Do:**
1. `settings.py`: Pydantic models `Settings`, `ModelTier(model, reasoning_effort|None, temperature|None, max_iter)`, `Profile` (`standard`, `smoke`, `max-quality`), `BudgetSettings` (fields only; enforcement in T9), `ExecutionSettings` (`backend`, `max_parallel_commands`), `ParallelSettings(max_parallel_agents, max_rpm)`. Precedence: explicit CLI/API overrides > env (`ENGINEERING_*`, all 0.1.0 names keep working) > project `engineering-team.toml` > user `~/.config/engineering-team/config.toml` > defaults. Use the standard-library `tomllib` (Python ≥ 3.11).
2. Provider presets (`openai`, `anthropic`, `google`, `ollama`, `azure`): a mapping tier → model for `lead`, `worker`, `cheap`, `reviewer`. **Verify every default model ID against the provider's current docs and CrewAI's LLM documentation** (the repo's `openai/gpt-5.6-*` IDs could not be verified while planning — keep them only if verified; never ship a model ID you did not confirm exists). Per-role overrides: `[models.roles] quality_engineer = "…"`.
3. Delete `_profile_setting`, `_make_llm`, `_max_iter`, `_env_bool`, `_optional_docs_mcps` from `crew.py` in favour of `Settings`. `EngineeringTeam(settings)`; stop mutating `os.environ` anywhere (profile travels in settings).
4. `Settings.describe()` returns each value with its *source* and masks secrets; expose `engineering-team config show` as a minimal subcommand (full CLI is T21).
5. Create `docs/CONFIGURATION.md` (every setting once, precedence, presets, examples). Remove the env-var table from README and trim `.env.example` to secrets + 3 commonly changed values, with a pointer.

**Tests:** precedence matrix; smoke marker; per-role override; invalid provider/effort values produce one-line errors; secret masking; 0.1.0 env names still honoured.
**Changelog:** Added (config file, provider presets), Changed (settings centralised).
**Done when:** `grep -rn "os.getenv\|os.environ" src/` shows only `settings.py` and the subprocess-env code.

#### Task 5 — `RunContext`, per-run tools, workspace lock (remove globals)

**Depends on:** 4 · **Model:** O

**Why:** the global workspace (F6) blocks concurrency and parallel agents; this is the keystone refactor.

**Do:**
1. `runtime/context.py`: frozen `RunContext(run_id, settings, workspace: ProjectWorkspace, run_dir: Path, command_gate: threading.BoundedSemaphore, cancel_event: threading.Event)`. `run_id` = sortable unique id (timestamp + short random). `run_dir = <workspace>/.engineering-team/runs/<run_id>/`.
2. Split `tools/workspace_tools.py` (≈500 lines): `tools/workspace.py` (`ProjectWorkspace`), `tools/commands.py` (command running; T7 extends), `tools/factory.py`: `build_tools(ctx, *, write_scope: WriteScope | None = None, read_only: bool = False) -> list[BaseTool]` returning tool instances **closed over `ctx`** (no module-level tool objects, no `get_workspace()`, no `configure_workspace()`/`_active_workspace`). Keep `cache_function = never` behaviour for mutating tools.
3. `WriteScope(allow: tuple[str, ...], deny: tuple[str, ...] = ())` (gitignore-style globs); write/replace/delete/patch enforce it and return an `ERROR:` string naming the owned paths; reads are unrestricted. Used by T17.
4. Workspace lock: `runtime/locks.py` — one writer per workspace via an OS-level file lock (`.engineering-team/lock`, record pid + run_id, detect stale locks by pid liveness); `acquire()` raises a clear `WorkspaceBusy` error. `command_gate` limits concurrent `run_command` calls (`Settings.execution.max_parallel_commands`, default 2).
5. Update `EngineeringTeam`, `main.py`, and tests to construct everything from a `RunContext`. `output_log_file` becomes `run_dir/crew-log.json`.

**Tests:** two contexts in two threads never see each other's files/tools; write-scope denies out-of-scope writes and allows owned ones; lock excludes a second holder and recovers from a dead pid; old tests ported.
**Docs:** ARCHITECTURE (RunContext, tools, locking). **Changelog:** Changed (internal: per-run tools), Added (workspace lock: one run per workspace).
**Done when:** `grep -rn "get_workspace\|configure_workspace\|_active_workspace" src/ tests/` is empty.

#### Task 6 — Offline test infrastructure (FakeLLM)

**Depends on:** 5 · **Model:** S

**Why:** every later task needs deterministic tests of agent flows without paid calls.

**Do:**
1. Check the installed CrewAI custom-LLM API (`crewai.BaseLLM`; docs "Custom LLM") and implement `engineering_team/testing/fake_llm.py`: `ScriptedLLM(responses: list[str | ToolCall | Callable])` that returns scripted ReAct/native tool-call turns, then a final answer; records prompts; raises if the script runs out; supports usage numbers (so T9 can test accounting).
2. `engineering_team/testing/fakes.py`: helpers to build one real `Agent`+`Task` with `ScriptedLLM` and the real tools bound to a temp `RunContext`, so a test can prove "agent calls Write Project File → file exists".
3. `tests/test_fake_llm_integration.py`: one end-to-end agent run (write file, run `python script.py` fixture, final answer) proving tool wiring, cancellation, and iteration limits without network.
4. Pytest markers in `pyproject.toml`: `live` (needs `ENGINEERING_LIVE_TESTS=1` + API key), `docker` (skip if `docker info` fails); a `conftest.py` hook skips them by default.
5. The `testing/` package is part of the wheel (T33's offline benchmark mode uses it).

**Docs:** CONTRIBUTING (testing section). **Changelog:** none user-visible (internal) — add under Added: "Offline FakeLLM test utilities".
**Done when:** a CrewAI agent loop is tested end-to-end with zero network.

#### Task 7 — Core toolbelt: tool catalogue, search/read/patch/outline/repo-map, `ExecutionBackend` (local)

**Depends on:** 5 · **Model:** S

**Why:** agents are only as good as their tools (F16); also fixes F9. This task builds the **tool catalogue** every later tool task (T10–T15, T19) plugs into, plus the core file/search/exec tools.

**Do:**
1. **Catalogue** `tools/registry.py`: `ToolSpec(name, group, read_only, needs_network, needs_command_gate, summary, factory)`; `build_tools(ctx, *, groups, write_scope, read_only)` (extends T5's factory) assembles tools by **group** (`fs_read`, `fs_write`, `search`, `command`; later `dev`, `runtime`, `code_intel`, `web`, `browser`, `git_read`, `board`, `notes`, `human`). Implements the "Tool design rules" in §4: uniform compact results, `ERROR:` convention with remediation hints, `tool.call` telemetry hook (emitted to the event sink once T8 lands — until then a no-op sink protocol), cancellation and tool-call-budget hooks. Create `docs/TOOLS.md` with the group table and one row per tool.
2. **Search & navigation tools** (`search` / `fs_read` groups): **Search Project Files** (regex or literal, include/exclude globs, context lines, result cap, case options; `rg` when on PATH else Python fallback; respects `.gitignore` and the ignored-dirs list), **Find Files** (glob, sorted by recency or name), **Read File Range** (1-based, line-numbered, limit), **Read Many Files** (batch of paths/ranges in one call with a total byte cap — saves agent iterations), **File Info** (size, lines, type, mtime, binary?), **Project Tree** (depth/size-annotated, ignores heavy dirs), **Project Outline** (top-level symbols per file: Python via `ast`, JS/TS/Go/Java/Rust via light regexes), **Repo Map** (token-budgeted overview of the most important files and their symbols, ranked by how often identifiers are referenced elsewhere — a lightweight aider-style map; size parameter).
3. **Write tools** (`fs_write` group): **Apply Patch** (unified diff *or* a list of `{path, old, new, expected_replacements}` edits; validates every hunk first, applies atomically, returns per-file summary + new line ranges), **Move Path**, **Copy Path**, **Make Directory**; existing write/replace/delete stay. **Workspace Changes** (read-only): files added/modified/deleted since the run started with a compact unified diff — works before git exists (snapshot hashes taken at run start by `RunContext`).
4. **Execution backend** `execution/backend.py`: `ExecutionBackend` protocol — `run(CommandSpec) -> CommandRecord` where `CommandSpec(argv, cwd, env, timeout, label, network: bool=False)` and `CommandRecord(id, argv, cwd, exit_code, timed_out, duration, log_path, output_tail, truncated)`; plus `start(CommandSpec) -> ProcessHandle` (used by T12 background processes; implement the protocol now, the tools come in T12). `execution/local.py`: `LocalBackend` — spawn in its own session/process group; stream stdout+stderr to a capped log under `run_dir/commands/<n>.log` while keeping only a head+tail window in memory; on timeout/cancel kill the whole process group (SIGTERM, SIGKILL after 3 s; Windows `taskkill /T`); set `CI=1`, `NO_COLOR=1`. Existing validation (allowlist, no shell, blocked inline flags, absolute-path args) stays in `tools/commands.py` and runs before `backend.run`.
5. **Run Project Command** returns exit code, duration, output tail, and `full log: <path>` (readable with Read File Range). Add **Run Script** (`command` group): run a named script (`package.json` scripts, `Makefile`/`justfile` targets, `pyproject` `[project.scripts]`/tool scripts) with a **List Scripts** companion that shows what exists — fewer guessed commands.
6. Create `docs/SAFETY.md` (boundary description moved out of README/ARCHITECTURE; local backend now, Docker section added in T20).

**Tests:** output flood bounded in memory; timeout kills a grandchild (spawned via a script file); each tool's happy path and `ERROR:` hints; patch atomicity (bad hunk leaves tree untouched); repo-map ordering on a fixture repo; `Workspace Changes` diff; registry group assembly and read-only mode excludes write tools; a ScriptedLLM agent uses search → range read → patch end to end; **a consistency test that fails when a registered tool has no row in `docs/TOOLS.md` (and vice versa)**.
**Docs:** `docs/TOOLS.md`, `docs/SAFETY.md`, ARCHITECTURE (tools + backend). **Changelog:** Added (tool catalogue, search/read/patch/outline/repo-map tools, script runner, streaming command logs), Fixed (descendant processes survive timeout; unbounded output buffering).
**Done when:** an agent can navigate a 5,000-file tree using search + repo map + range reads without reading whole files, and `docs/TOOLS.md` lists every tool with its group.

#### Task 8 — Contracts, run store, event log

**Depends on:** 5 · **Model:** O

**Do:**
1. `contracts.py` (Pydantic v2, every model has `schema_version: int = 1`): `AcceptanceCriterion(id, text, kind)`, `Spec(title, summary, criteria, non_goals, assumptions, open_questions)`, `ProjectCommands(setup, test, lint, build, run)`, `WorkPackage(id, title, role, description, owned_paths, depends_on, criteria_ids)`, `Plan(stack, commands, work_packages, risks)`, `CheckSpec(id, name, argv, required, timeout, criteria_ids)`, `CheckResult(id, status: passed|failed|skipped|unavailable, exit_code, duration, log_path, revision, started_at)`, `Finding(id, severity, file, line, summary, suggested_fix, source_role)`, `StageRecord(name, status, started, finished, attempts, artifacts)`, `RunManifest(run_id, mode, project_name, request_hash, settings_hash, versions{engineering_team, crewai, python}, strategy, recipe, status, stages, created, finished)`, `Event(ts, run_id, type, stage|None, agent|None, lane|None, data)`.
2. `runtime/run_store.py`: atomic JSON writes (temp file + `os.replace`), `events.jsonl` append (line-atomic, flushed, thread-safe), `list_runs(workspace)`, `load(run_id)`, `latest()`, status transitions validated (`pending→running→{succeeded,failed,cancelled,interrupted}`). Replace the old `request.md`/`run.json` writing in `build_inputs`.
3. `runtime/events.py`: `EventSink` protocol, `JsonlSink`, `FanoutSink`, `emit(type, **data)`; **bridge CrewAI's event bus** (check `crewai.events` in the installed version: task started/completed/failed, agent execution, tool usage, LLM call completed with token usage) into our events, tagged with the current run via `contextvars` (register listeners once per process; filter by run id so concurrent runs do not cross).
4. Secrets never enter events: scrub values of env vars matching `*KEY*|*TOKEN*|*SECRET*` before writing.

**Tests:** atomicity under concurrent writers; schema round-trip + forward-compat unknown-field tolerance; bridge with a ScriptedLLM agent produces expected event types; two concurrent runs' events do not mix; scrubbing.
**Docs:** ARCHITECTURE (contracts, run directory layout — this is *the* place describing `.engineering-team/runs/<id>/`). **Changelog:** Added (run IDs, manifests, event log; `crew-log.json` moved into the run directory).
**Done when:** a finished fake run leaves `manifest.json` + `events.jsonl` that fully describe it.

#### Task 9 — Usage accounting, price table, budgets

**Depends on:** 8 · **Model:** S

**Do:**
1. Collect per-LLM-call usage from the event bridge → `usage.json` (totals by stage/agent/model: prompt, completion, cached, reasoning tokens, call count) in the run dir.
2. `pricing.py` + `data/pricing.yaml`: USD per 1M tokens per model with `source_url` and `as_of`; **fetch current prices from the providers' pricing pages during implementation and record the date**; user overrides via config; **unknown model ⇒ cost `None`** shown as "unknown", never `$0`.
3. `runtime/budget.py`: `Budget(max_cost_usd, max_tokens, max_wall_seconds, max_tool_calls, max_repair_rounds)` from `Settings.budget`; `BudgetGuard.check(ctx)` called at stage boundaries *and* from the LLM-call event hook (cooperative stop: set `ctx.cancel_event`, raise `BudgetExceeded` at the next safe point). Document in-flight overrun honestly. Warn at 80 %.
4. Run summary includes `usage`, `estimated_cost_usd | None`, `budget_status`.

**Tests:** ScriptedLLM usage → totals; unknown model; budget stop at stage boundary and mid-stage; 80 % warning; override prices.
**Docs:** CONFIGURATION (budgets, pricing overrides). **Changelog:** Added (usage/cost reporting, budgets).
**Done when:** a fake run prints exact token counts and a cost or "unknown".

#### Task 10 — Task board (kanban model) + coordination tools

**Depends on:** 7,8 · **Model:** O

**Why:** F17 — you asked for great visibility: a kanban board agents move cards on, truthfully. This task builds the model, rules, tools and exports; the pipeline (T16) populates it, the CLI (T21) and web UI (T32) display it.

**Design (record in ARCHITECTURE "Task board"):** the **controller is the source of truth**. It auto-creates and moves cards from stage/package/check events, so the board is correct even if an agent forgets to update it. Agents *also* use a board tool to add subtasks, comment, flag blockers and request completion; their moves are validated.

**Do:**
1. `board/models.py` (Pydantic, add to contracts): `Card(id "K-001", title, description, kind: stage|work_package|subtask|repair|finding|check|user_note, status, assignee (teammate key|None), lane, stage, parent_id, depends_on, criteria_ids, owned_paths, artifacts, attempts, blocked_reason, comments[Comment(ts, author, text)], evidence (check ids), tokens, cost_usd|None, created/started/finished)`; columns (`status`): `backlog · ready · in_progress · verifying · blocked · done · failed` (+ `cancelled`).
2. `board/store.py`: `BoardStore` (thread-safe; atomic `board.json` in the run dir; every mutation also emits a `board.*` event: `card_created`, `card_moved`, `card_commented`, `card_updated`). Transition rules in one table: agents may do `ready→in_progress`, `in_progress→verifying` (= "I'm done, please verify"), `*→blocked` (reason required), `blocked→in_progress`; **only the controller** may move to `done`/`failed`/`cancelled` (requires evidence: check ids or stage success), or revert `verifying→in_progress` on failed checks (attempt counter +1). WIP limit per column (`max_parallel_agents` for `in_progress`). Invalid moves return an `ERROR:` with the allowed next states.
3. `progress()` → overall percent (cards done / total, weighted: stages 1, work packages 2, checks 1, subtasks 0.5), per-stage percent, count by column, blocked list with reasons, oldest in-progress card age. No ETA claims.
4. **Human steering:** a card comment authored by `user` (and a run-level `user_note`) is delivered to the assignee's **next prompt** at the stage/agent boundary ("User note on K-004: …"); `pause`/`unpause` flag checked at safe points (between agent steps). API surface is a library function now; CLI/UI use it in T21/T30.
5. **Coordination tools** (groups `board`, `notes`, `human`; register in T7 catalogue): **Task Board** (`list`, `get`, `create_subtask`, `move`, `comment`, `block`, `unblock` — compact table output), **Shared Notes** (`write_note(key, text)`, `read_note(key)`, `list_notes()`; persisted under `run_dir/notes/`; plus an optional project-level `decisions.md` append-only log agents can cite — size-capped), **Report Progress** (one-line status per card, shown in activity feed), **Ask Human** (blocks with a timeout until answered via the interaction hook; in non-interactive mode returns "no human available — proceed with your best assumption and record it with Shared Notes"; emits `question` events; T22 wires the CLI/UI answer path).
6. `board.md` renderer (Markdown columns/table) written to the run dir on every change (debounced) and at run end; `board.json` is the machine format consumed by T21/T29/T30/T32.

**Tests:** transition-rule matrix (agent vs controller, evidence required for `done`, WIP limit); concurrent moves from multiple threads; event emission; progress math; steering note delivered once; pause flag; every coordination tool via ScriptedLLM; `board.md` golden.
**Docs:** ARCHITECTURE (board section: lifecycle, rules, who may move what), TOOLS (rows), USAGE later. **Changelog:** Added (task board with controller-enforced card lifecycle, notes, ask-human, progress reporting).
**Done when:** a scripted run leaves a `board.json` whose card history alone explains the whole run, and an agent cannot move a card to `done` by itself.

#### Task 11 — Developer tools: structured test/lint/typecheck/build/format/coverage runners

**Depends on:** 7,8 · **Model:** S

**Why:** agents currently run a test command and read raw text. Structured results save iterations, make repair loops precise, and are reused by the Verifier (T18).

**Do:**
1. `devtools/` package: framework **detection + runners + parsers** producing one shape — `TestReport(framework, passed, failed, skipped, errors, duration, failures[TestFailure(test_id, file, line, message, trace_excerpt)], log_path)`. Support: pytest (`--junitxml`), unittest, jest/vitest (JSON or JUnit), go test (`-json`), cargo test (text parser), Maven/Gradle surefire XML, dotnet test (trx/junit), RSpec/minitest, PHPUnit (junit). Unknown framework → raw log with exit code. Parsers are pure functions with fixture-based tests.
2. Tools (group `dev`, all through `ExecutionBackend`, honouring the command allowlist):
   - **Run Tests** (`path|filter|markers`, `framework` auto-detected; returns the compact `TestReport`; `fail_fast`, `timeout`), **Rerun Failed Tests**, **Run Single Test**.
   - **Run Linter** (ruff, eslint, golangci-lint, clippy, rubocop, phpcs …; normalised `Diagnostic(file,line,col,rule,severity,message)` list, capped), **Type Check** (mypy, pyright, tsc, go vet/build, `cargo check`), **Format Code** (`mode=check|write`: ruff format, black, prettier, gofmt, rustfmt), **Build Project** (detected build command; errors parsed into diagnostics).
   - **Coverage Report** (coverage.py, jest/vitest/nyc, go cover, cargo-llvm-cov when installed; summary + uncovered line ranges for a given file), **Install Dependencies** (detects uv/pip/poetry/npm/pnpm/yarn/bun/go/cargo/maven…; the only dev tool marked `network: setup`; honours the setup-phase policy of T20), **Dependency Audit** (pip-audit/npm audit/cargo audit when available; `unavailable` rather than empty-success when the tool is missing).
3. `devtools/detect.py`: stack and command detection (shared with T18's profiles and T25's `RepoAnalyzer` — one implementation; whichever lands first owns it, the others import it).
4. Missing tool/runtime ⇒ explicit `unavailable` result with an install hint (never a silent pass).
5. TOOLS.md rows; CONFIGURATION: `[tools.dev]` timeouts and extra allowlisted executables.

**Tests:** parser fixtures for each framework (real captured outputs committed as tiny fixtures); runner behaviour with a tiny Python project in tmp (pytest present in the dev env); `unavailable` paths; diagnostics capping; agent ScriptedLLM run: fail → read structured failure → fix → pass.
**Docs:** TOOLS, SAFETY (install-phase network), CONFIGURATION. **Changelog:** Added (structured test/lint/typecheck/build/format/coverage tools).
**Done when:** a failing pytest run is returned to the agent as a compact list of failing tests with file/line/message, not a 40 KB log.

#### Task 12 — Runtime tools: background processes, ports, HTTP client, SQLite inspect, environment info

**Depends on:** 7 · **Model:** S

**Why:** to verify an API or UI the agent must *start* the app, wait for it, call it, read its logs, and stop it — without blocking forever (a classic agent failure).

**Do (all via `ExecutionBackend.start`; group `runtime`):**
1. **Start Background Process** (`name`, `command`, `cwd`, `ready_when`: `port|url|log_regex|delay`, `ready_timeout`) → returns pid-less handle id + readiness result + first log lines; **List Processes**, **Read Process Logs** (`tail|since_offset|grep`), **Stop Process**, **Wait For Port/URL**. Processes are registered in the `RunContext`, capped (`max_background_processes`), have hard lifetime limits, and are **killed (whole process group) on stage end, cancel, crash, or run end** — leak test required.
2. **HTTP Request** (method, URL, headers, JSON/text body, timeout; response status, selected headers, body capped and pretty-printed for JSON). **Default allowed targets: `localhost`, `127.0.0.1`, `[::1]` and ports of processes started in this run**; anything else requires `network.http_allowlist` (opt-in, logged). Redirects re-validated per hop. Follow the data-not-instructions rule from T14 for responses.
3. **Check Port / Find Free Port** (avoids port collisions between parallel agents — the helper hands out distinct ports per lane), **Environment Info** (OS, CPU/mem, versions of python/node/go/java/rust/dotnet/docker/git found on PATH, which allowlisted executables are missing) so agents pick commands that exist.
4. **Query SQLite** (read-only: `file:…?mode=ro`, parameterised, row/size caps) and **Inspect Database Schema** (tables, columns, indexes, row counts) for projects using SQLite; other databases are explicitly out of scope for 0.2.0 (note in TOOLS).
5. No interactive shell / PTY tool: long-lived processes + log reading cover the real needs; record this decision (security and determinism) in the Decision log.
6. Docker (T20) must publish only `127.0.0.1:<port>` for container processes — leave a documented extension point (`CommandSpec.ports`).

**Tests:** start a tiny Python `http.server` fixture, wait for readiness, HTTP-request it, read logs, stop it, assert no leftover processes (even after a simulated crash/cancel); allowlist enforcement incl. redirect to an external host; port allocation under threads; SQLite read-only enforcement (write attempts fail); environment info with a stubbed PATH.
**Docs:** TOOLS, SAFETY (network rules for HTTP tool), CONFIGURATION. **Changelog:** Added (background process, HTTP request, port, SQLite and environment tools).
**Done when:** an agent can start a dev server, assert an endpoint's response, and leave nothing running.

#### Task 13 — Code intelligence tools: symbols, references, import graph, hotspots, dependency inspect

**Depends on:** 7 · **Model:** S

**Why:** navigating and changing legacy code (T25–T28) depends on knowing *where* things are and *what depends on them* — more than grep.

**Do (group `code_intel`, read-only):**
1. **Find Symbol** (definitions by name/kind across the repo: Python via `ast`; JS/TS/Go/Java/Rust/C#/Ruby/PHP via tree-sitter when the optional extra `[code-intel]` (e.g. `tree-sitter-language-pack`) is installed, else regex fallback — record the choice and why in the Decision log), **Find References** (word-boundary search classified as definition/import/call/other, grouped by file), **Show Symbol** (definition body with N lines of context).
2. **Import Graph** — **Who Imports** (reverse dependencies of a file/module) and **Imports Of** (forward); languages as above; used for impact analysis ("what could break if I change X").
3. **Find Related Tests** (tests that import or mention a module/symbol; naming heuristics `test_*.py`, `*.spec.ts`, `*_test.go`), **Find TODOs** (TODO/FIXME/HACK with owner/age when git info available).
4. **Hotspots** (git churn × file size/complexity proxy; top-N risky files) — requires T19's GitPort; degrade gracefully ("no git history") if absent.
5. **Inspect Dependencies**: parse manifests/lockfiles (pyproject/requirements/poetry.lock/package.json/lock files/go.mod/Cargo.toml/pom) → direct dependencies with declared versions; *outdated/deprecated* info only via T14's Package Info when web tools are enabled.
6. Optional **LSP diagnostics bridge** is **deferred** (Decision log): Type Check (T11) covers diagnostics for 0.2.0.

**Tests:** fixture polyglot mini-repo (Python + TS + Go): definitions, references classification, reverse imports, related tests; fallback when tree-sitter missing; hotspot ranking with a synthetic git history; dependency parsers.
**Docs:** TOOLS. **Changelog:** Added (symbol/reference/import-graph/related-tests/hotspot/dependency tools).
**Done when:** "who breaks if I change `parse_config()`?" is answerable with two tool calls on the fixture.

#### Task 14 — Web & knowledge tools: search, fetch, package info, local doc search

**Depends on:** 4,7 · **Model:** S

**Why:** engineers look things up — library docs, error messages, latest versions. Off by default; safe when on.

**Do (group `web`; `Settings.web.enabled=false`, enabled by `--allow-web` or config, per-role allow):**
1. Check CrewAI's built-in tools first (AGENTS.md rule — e.g. `SerperDevTool`, `ScrapeWebsiteTool`); wrap or reuse them where they fit and meet the safety rules below, otherwise implement. Providers for **Web Search**: Serper, Brave, Tavily (API key from env, never printed); pluggable `SearchProvider` interface; results = title/url/snippet only.
2. **Fetch URL** (HTML→Markdown with readability-style extraction, size/time caps, content-type filter, PDF text optional): **SSRF-safe** — http/https only, resolve DNS and block private/loopback/link-local/metadata addresses *after resolution and on every redirect hop*, optional domain allow/deny lists, per-run request cap.
3. **Package Info** (official JSON APIs: PyPI, npm registry, crates.io, pkg.go.dev/proxy): latest version, release dates, deprecation/yanked flags, license, repo URL — lets agents choose current versions instead of hallucinating.
4. **Search Docs (local)** — lightweight BM25/TF-IDF retrieval over `--context-dir` documents, the repo's `docs/`/README/markdown, and previously fetched pages cached under `run_dir/web-cache/` (no embeddings, no network); the shared home for the "knowledge" idea used by T22/T35.
5. **Prompt-injection posture:** every web/doc tool result is wrapped in a clearly delimited block labelled *untrusted external content*; agents' instructions state that such content never changes tools, permissions, scope or user requirements; tool results can never trigger controller actions. Log every outbound request as an event.

**Tests:** SSRF matrix (127.0.0.1, 10.x, 169.254.169.254, DNS-rebinding style hostname resolving to private IP, redirect to private IP); caps; offline provider mocks; package-info parsers on recorded JSON; local search ranking; injection-wrapper test; disabled-by-default test.
**Docs:** TOOLS, CONFIGURATION (`[web]`, keys by env var name only), SAFETY (network model and injection posture). **Changelog:** Added (opt-in web search/fetch/package-info tools and local doc search).
**Done when:** with web disabled no network tool is even registered; with it enabled, private-address fetches are provably blocked.

#### Task 15 — Browser tools: headless Chromium for UI verification

**Depends on:** 12 · **Model:** S

**Why:** frontend work is unverifiable without looking at it. This lets the frontend/QA teammates actually load their page, read it, interact with it and catch console errors.

**Do (group `browser`, optional extra `[browser]` = Playwright; `doctor` explains `playwright install chromium`; tools not registered when unavailable):**
1. Tools: **Browser Open** (URL), **Browser Snapshot** (accessibility-tree text with stable element refs — the primary way agents "see" a page, cheap and deterministic), **Browser Screenshot** (PNG to `run_dir/screenshots/`, full-page or element; returned as an image to multimodal models — verify CrewAI's multimodal/image-tool support for the installed version; otherwise return the path and let a vision-capable reviewer call it), **Browser Click / Type / Select / Press Key / Wait For**, **Browser Console & Errors** (console messages, page errors, failed network requests), **Set Viewport** (desktop/tablet/mobile), **Accessibility Check** (snapshot-based heuristics: missing labels, empty buttons/links, heading order, contrast hints — clearly labelled heuristic, not a full audit), **Browser Close**.
2. **Navigation guard:** only `localhost`/loopback and ports of this run's background processes (T12) by default; external URLs only through the T14 allowlist rules. No file:// URLs, no downloads, no credential storage, fresh incognito context per agent, size/time caps, max concurrent browsers (`browser.max_contexts`, default 2), all closed at stage/run end.
3. The browser runs on the host (orchestrator side) and talks only to loopback — document that Docker mode (T20) publishes container ports to `127.0.0.1` for it.
4. Screenshots are registered as run artifacts (shown in the report T29 and dashboard T32); `frontend_engineer` and `quality_engineer` get the group by default (T23).
5. Optional **e2e check type** for the Verifier (T18): a `checks.yaml` entry `type: browser_script` running a Playwright test file supplied by the user.

**Tests:** serve a tiny static page via the T12 tools; open/snapshot/click/type/console-error capture; navigation guard rejects external and `file://`; resource cleanup; all browser tests marked `browser` (skipped when Playwright/Chromium is missing) with the guard/cleanup logic unit-tested without a browser.
**Docs:** TOOLS, SAFETY (browser sandbox scope), CONFIGURATION (`[browser]`), USAGE (UI verification). **Changelog:** Added (optional headless-browser tools).
**Done when:** a scripted agent loads a local page, finds a button by snapshot ref, clicks it, and detects a seeded console error.

#### Task 16 — Flow pipeline, recipes, strategies, resume

**Depends on:** 6,7,8,9,10 · **Model:** O (biggest design task)

**Why:** replaces the fixed 6-task hierarchical run (F7, F8) with a controller that is resumable, skip-aware, and the base for parallelism and new modes.

**Do:**
1. **Strategy interface** `pipeline/strategies.py`: `Strategy.run(ctx, recipe, bundle) -> RunResult`. Implementations: `HierarchicalStrategy` (existing crew refactored onto `RunContext`/`Settings`, `config/tasks.yaml` unchanged), `PipelineStrategy` (new), `SingleAgentStrategy` (one agent, all tools, one task — baseline for T33). CLI/Settings `strategy = pipeline|hierarchical|single`; **default stays `hierarchical` until T34 decides**, new modes require `pipeline`.
2. **Recipes** `pipeline/recipes.py` + `modes/recipes/new.yaml`: a `Recipe` is data — ordered stages with `name`, `kind` (`agent`|`controller`|`parallel`), `teammates`, `inputs`, `outputs` (contracts), `skip_if` conditions, `retry`, and a `verification_policy`. The `new` recipe: `spec` → `plan` (architect emits `Plan`) → `foundation` → `implement` (work packages; sequential now, parallel in T17) → `verify` (placeholder using the quality agent; T18 swaps in the real Verifier) → `release`.
3. **Flow** `pipeline/flow.py`: `PipelineFlow(Flow[PipelineState])` with a Pydantic state (spec, plan, stage records, check results). Stages are methods wired with `@start/@listen/@router`; each stage builds a *small crew* (single agent + task, `output_pydantic` where a contract is expected) via `pipeline/stages.py`. Task prompts live in `config/stages.yaml`, agents reuse `agents.yaml` — do not duplicate role text. The architect's `Plan` decides which work packages exist, so CLI/API-only projects skip frontend work naturally.
4. **Resume** (`engineering-team resume <run_id>`, API function `resume(run_id)`): reload manifest + state; a stage is *complete* only if its record says so **and** the workspace tree-hash recorded at its end still matches (fast hash of tracked, non-ignored files); an `interrupted` stage re-runs with an added instruction ("a previous attempt may have left partial changes; inspect the workspace first, do not redo finished work"). Re-running with a *changed* request creates a new run, never silently reuses old evidence. Evaluate CrewAI's native `checkpoint=True`/`CheckpointConfig` per-stage and record the decision (use it if it works with our per-stage crews; otherwise keep our own stage state).
5. **Cancellation:** SIGINT/SIGTERM handler sets `ctx.cancel_event`; stages check it between agent steps (tools check it too); manifest ends `cancelled`; `cancel <run_id>` for background runs writes a flag file the controller polls.
6. **Board wiring (T10):** at run start create one `stage` card per recipe stage (`backlog`/`ready`); the controller moves cards on stage events (`in_progress` → `verifying` → `done`/`failed`) and creates `work_package`, `repair` and `finding` cards from the `Plan`, repair loop and reviews; pass each agent its card id and the board/notes/human tools; deliver pending steering notes at stage start; honour `pause`. Stage agents receive tool groups by a default table now (`fs_read, fs_write, search, command, dev, runtime, code_intel, board, notes, human`; `web`/`browser` only if enabled) — T23 replaces the table with per-teammate groups.
7. Keep tests with `ScriptedLLM`/fake stage runners: stage order, skipping, failure at each stage then resume (no stage re-run when hash matches), repeated resume safe, changed request ⇒ new run, cancellation, strategy selection.

**Docs:** ARCHITECTURE (pipeline, recipes, resume semantics, why Flow). **Changelog:** Added (pipeline strategy, resume, cancel, single-agent strategy), Changed (strategy option).
**Done when:** a scripted 6-stage run can be killed after any stage and resumed to the same final state without redoing completed stages.

#### Task 17 — Parallel execution engine

**Depends on:** 16 · **Model:** O (concurrency)

**Why:** you asked for parallelism wherever possible. The engine must be safe, not merely concurrent.

**Design (record in ARCHITECTURE):** parallelism by **ownership**, not by merging. The architect's plan gives each work package `owned_paths` globs; each package's agent receives tools with a `WriteScope` for those paths; reads are unrestricted; shared files (manifests/lockfiles/README/CI config) are owned by `foundation`/`integrator` only. Git worktrees per package were considered and rejected for 0.2.0 (merge conflicts need an LLM resolver; revisit if ownership proves too restrictive) — add that to the Decision log.

**Do:**
1. `pipeline/parallel.py`: `run_work_packages(ctx, plan, runner, max_parallel)` — topological layering from `depends_on`; run each layer in a thread pool (copy `contextvars` into workers so event tags and the RunContext propagate); per-package crew with role-appropriate agent and scoped tools; global `max_parallel_agents` (default 3) and a shared requests-per-minute limiter (`max_rpm`) across agents; provider 429s rely on CrewAI retry/throttle.
2. **Plan validation** before execution: disjoint `owned_paths` within a layer (glob overlap check; overlapping packages are auto-serialised with a warning), every package maps to ≥1 spec criterion, dependency graph acyclic, shared-file ownership rule. Invalid plan ⇒ architect gets one structured repair attempt, else the run fails clearly.
3. **Failure isolation:** a failed package does not cancel siblings; retry once with the error context; on second failure mark `failed`, continue independent packages, and let `integrate` decide (fail the run if a required package is missing).
4. **Integrator stage** (sequential, unrestricted scope): runs build/tests, fixes cross-package contract mismatches, records what it changed.
5. **Parallel read-only fan-out helper** `run_parallel_readonly(ctx, jobs)` for reviewers and codebase analysts (T24, T25): each job gets read-only tools and writes only to its own report path.
6. Event `lane` ids per concurrent unit so the CLI/UI can draw parallel lanes (T21, T32); **each work package is a board card (T10) assigned to its teammate and lane**, `in_progress` WIP limit = `max_parallel_agents`; each lane gets its own free ports from T12's port allocator and its own browser context (T15) so parallel agents never collide. Reports list lanes in stable order.
7. Settings: `parallel.max_parallel_agents`, `parallel.max_rpm`, `ENGINEERING_MAX_PARALLEL`; `1` reproduces sequential behaviour exactly.

**Tests (ScriptedLLM + barriers):** two packages observably run concurrently (a `threading.Barrier` both must reach); dependency order respected; out-of-scope write blocked; overlap detected and serialised; one failure isolated; `max_parallel=1` sequential; deterministic report ordering; cancellation stops all lanes; no cross-talk of events.
**Docs:** ARCHITECTURE (parallelism section + rationale), CONFIGURATION. **Changelog:** Added (parallel work packages, write-scoped agents, parallel read-only fan-out).
**Done when:** wall-clock of a 3-package fake plan with 0.2 s sleeps per package is ≈ one package's time at `max_parallel=3`.

#### Task 18 — Independent verification and bounded repair loop

**Depends on:** 7,11,16 · **Model:** O

**Why:** F1. This is the trust core of the project.

**Do:**
1. `verification/profiles.py`: detect stack and default setup/test/lint/build/startup-smoke commands **by reusing T11's `devtools/detect.py` and result parsers (do not reimplement)**; precedence: user-supplied checks > spec/plan-declared commands > detected defaults. User checks may live in a `checks.yaml` **outside the worker-writable tree** (`--checks FILE`), each `{id, name, command, required, timeout, criteria}`.
2. `verification/verifier.py`: `Verifier.run(ctx, checks) -> list[CheckResult]` executes checks as the **controller** through `ExecutionBackend`, never via an agent; stores exit code, duration, log, and the **workspace revision hash** (T16's tree hash) in the run store. Each check is a `check` board card (T10) the controller moves (`verifying` → `done`/`failed`), and its parsed `TestReport`/diagnostics (T11) are attached to the `CheckResult`. Statuses `passed|failed|skipped|unavailable` are distinct (missing runtime ⇒ `unavailable`, not pass).
3. **Replace artifact-existence guardrails**: the controller itself renders an "Independent checks" section from `CheckResult`s into `docs/verification.md`; agent narrative goes to `docs/qa-notes.md`. Stage success requires: required checks passed, results' `revision` equals the current revision, every spec criterion either has a passing mapped check or is listed as `manual/unverified` in the report.
4. **Repair loop:** verify → if required checks failed, hand structured failures (check id, command, log tail, suspect files) to the `debugger`/quality agent, re-verify; `max_repair_rounds` (default 3, budget-aware). Any workspace edit after the last verification (e.g. release stage) triggers a final re-verify. Terminal states: `verified` (exit 0), `failed` (exit 3), `partial` (exit 4: some required checks unavailable/skipped), with a useful report in every case.
5. Remove the T3 interim size heuristic where superseded.
6. Optional check type `browser_script` (user-supplied Playwright test; needs T15) alongside `command` checks.

**Tests (false-success matrix):** empty report, stale report from an older run, agent claiming a pass the controller never ran, failing behaviour, altered check file, edit-after-verify, missing runtime ⇒ unavailable, repair converges, repair exhausts, criteria without checks flagged.
**Docs:** ARCHITECTURE (verification model), USAGE later; CONFIGURATION (`verify.*`). **Changelog:** Added (independent verification, repair loop, exit codes), Changed (guardrails replaced by controller-run checks).
**Done when:** none of the false-success scenarios can yield `verified`.

#### Task 19 — Git integration (`GitPort`)

**Depends on:** 5 · **Model:** S

**Do:**
1. `git/port.py`: thin `subprocess` wrapper (no new dependency; `shell=False`, fixed env: `GIT_TERMINAL_PROMPT=0`, no pager, no credential helpers, `-c core.hooksPath=/dev/null` for controller operations). Methods: `is_repo`, `is_dirty`, `status`, `current_branch`, `head`, `create_branch`, `worktree_add/remove`, `checkpoint(message) -> sha` (controller commits with a configurable author, default `Engineering Team <engineering-team@users.noreply.github.com>`), `diff(base)`, `diff_stat`, `export_patch(path, base)`, `tree_hash()` (shared with T16's revision hash).
2. Greenfield behaviour: `git init` in new projects by default (`--no-git` to skip), an initial commit, and a checkpoint commit after every completed stage (`stage(architecture): …`) → free rollback and nice history.
3. Read-only agent tools (group `git_read`, register in the T7 catalogue): **Git Info** (`status|diff|log|show|blame` on project paths and refs, bounded output), **Git History Search** (`log -S`/`-G`, file history, who-last-touched), **Git Diff Between Refs**. Agents still cannot touch `.git` directly (T3).
4. Hard rules: never push, fetch, add/modify remotes, force anything, or run hooks; refuse to operate on a repo whose toplevel is outside the workspace.

**Tests:** real `git` in tmp dirs (skip if git missing): init, checkpoints, branch, worktree, dirty detection, patch export/apply round-trip, hooks not executed, no network.
**Docs:** ARCHITECTURE, SAFETY. **Changelog:** Added (git-backed stage checkpoints, patch export).
**Done when:** a fake greenfield run yields a git history with one commit per stage.

#### Task 20 — Docker execution backend

**Depends on:** 7,12,18 · **Model:** O

**Do:**
1. `execution/docker.py`: `DockerBackend` implementing `ExecutionBackend`. Image selection by detected stack (`python:3.12-slim`, `node:22-slim`, `golang:1.23`, `rust`, `eclipse-temurin`, `mcr.microsoft.com/dotnet/sdk`…; verify tags exist; configurable `[execution.docker] image`, `setup_image`).
2. Hardening flags: run as the host uid:gid (not root), `--cap-drop ALL`, `--security-opt no-new-privileges`, `--read-only` root with tmpfs for `/tmp`, `--pids-limit`, `--memory`, `--cpus`, no Docker socket, no host env (secrets never passed; only explicit allowlist), workspace mounted rw at `/workspace` **without** `.engineering-team/` (use a tmpfs overlay or mount subpaths), per-project cache volumes for uv/pip/npm.
3. Network policy: `none` for verification/test runs; a distinct **setup phase** (`network = "setup"`) allows network only for install commands; arbitrary domain filtering is *not* claimed.
4. Lifecycle: container labelled with run id; `docker rm -f` on timeout/cancel/exit; `engineering-team doctor` checks Docker. **Never silently fall back to the host** — `backend = "docker"` with Docker missing is an error; local mode is an explicit choice (`--sandbox local`, default `local` in 0.2.0 for zero-setup, recommended `docker` in docs; revisit default after T34).
5. The Verifier, agent commands, **and the T11 dev tools and T12 background processes** all honour the configured backend. Container processes publish ports only as `127.0.0.1:<port>` (T12's `CommandSpec.ports`) so the host-side HTTP and browser tools (T12, T15) can reach them; background containers are removed on run end. `doctor` recommends Docker when it is available.

**Tests:** golden tests for the generated `docker run` argv (no Docker needed); `@pytest.mark.docker` integration: external canary file and an API-key canary env var are invisible to the workload, network blocked in verify mode, output floods bounded, container removed after timeout.
**Docs:** SAFETY (threat model: what Docker protects, what it does not — kernel/escape risks, mounts), CONFIGURATION. **Changelog:** Added (Docker sandbox), Security.
**Done when:** the canary tests pass on a machine with Docker; skipped cleanly elsewhere.

---

### Phase C — Product: CLI, intake, team, modes

#### Task 21 — CLI v2 (Typer/Rich), `doctor`, `init`, runs

**Depends on:** 10,16,18 · **Model:** S

**Do:**
1. `cli/app.py` with Typer + Rich (declare both as dependencies). Commands now: `new`, `resume`, `status`, `runs`, `cancel`, `config show`, `doctor`, `init`, `examples` (list/run bundled examples), `team` (placeholder until T23). Later tasks add `analyze`, `feature`, `fix`, `maintain`, `review`, `report`, `ui`, `bench`. Global options: `--json`, `--quiet`, `-v/--verbose`, `--no-color`, `--workspace-root`.
2. **Back-compat:** bare `engineering-team --request-file …` (0.1.0 style) maps to `new` with a deprecation notice; keep script names `run_crew`, `train`, `replay`, `test`, `run_with_trigger` (required by the CrewAI CLI); move implementations behind the Typer app where sensible and make `replay` resolve project/run explicitly (take a run id or project name; fixes the env-based workspace guess).
3. **Live progress + kanban** (`cli/console.py`): Rich `Live` view fed by run events — header with overall progress bar (T10 `progress()`), cost/tokens vs budget and elapsed; a **kanban** of the T10 board (columns Backlog · Ready · In progress · Verifying · Blocked · Done, cards show id, title, assignee, age; blocked cards show the reason); one row per active **lane** (parallel agents) with its latest tool call; a rolling activity feed. Layouts adapt to terminal width (narrow ⇒ a compact list view). New commands: `board <run> [--watch]` (static/live kanban of any run), `note <run> "text" [--card K-004]` (human steering note, T10), `pause <run>` / `unpause <run>`.  Non-TTY or `--quiet`: plain timestamped log lines. Turn CrewAI's own verbose console off by default (it fights Live); `-v` re-enables it.
4. **Summary panel** at the end: status, duration, cost (or "unknown"), files changed, checks table, workspace path, report path, next-step hints. Exit codes: 0 verified/success, 1 error, 2 usage/config, 3 verification failed, 4 partial/needs-info, 130 interrupted.
5. `doctor`: Python/uv/git/Docker presence and versions, provider API key *presence* for the selected provider (never print values), language runtimes (node/go/java/rust/dotnet if the allowlist expects them), optional `--online` single-token model ping per tier.
6. `init`: interactive (Typer prompts) or flag-driven creation of `engineering-team.toml` + a request template in cwd.
7. Create `docs/USAGE.md` (CLI reference, workflows, how to write a strong request, exit codes); shrink README quickstart to point to it.

**Tests:** Typer `CliRunner` for each command; board rendering golden tests at two widths; JSON output schema; exit codes; deprecated-form mapping; non-TTY output; `doctor` with mocked environment.
**Changelog:** Added (new CLI), Changed (legacy invocation deprecated).
**Done when:** `engineering-team new --example tiny-notes --provider openai` shows live progress with a fake LLM in a test harness, and `--json` output is machine-parseable.

#### Task 22 — Requirements intake + Product Analyst

**Depends on:** 21 · **Model:** S

**Do:**
1. **Sources:** `--request TEXT`, `--request-file FILE` (repeatable → concatenated with headers), stdin (`-`), `--context-dir DIR` (reference docs copied read-only into `.engineering-team/context/` with an index; readable by agents), and the library function `RequestBundle.from_sources(...)` (the web UI uses it for textarea + uploaded files). Normalise line endings, enforce a size cap, reject template placeholders, hash for resume (T16).
2. **Templates:** `engineering-team init --mode new|feature|fix|maintain` writes a mode-specific request template (package resources) with guidance on problem, users, scope, non-goals, acceptance criteria, constraints.
3. **Product Analyst teammate** (`product_analyst`, defined in `agents.yaml` now; registry in T23) and `spec` stage: raw request → `Spec` with stable criterion IDs (`AC-1…`), assumptions, non-goals, open questions; writes `docs/spec.md`. Criteria IDs flow into the plan, work packages, and checks (T17/T18).
4. **Clarification:** if the analyst reports low confidence or open blocking questions: `--interactive` asks the user (≤5 questions, via the T10 **Ask Human** tool/hook that the CLI answers from the terminal and the UI answers from a form; emits `question`/`answer` events); otherwise record assumptions and continue. `--non-interactive` forced when stdin is not a TTY.
5. Hand the spec to the architect as the contract (replace "requirements" free text in later prompts with spec + original request).

**Tests:** multi-source merge order; stdin; size cap; template rejection; analyst stage with ScriptedLLM producing a valid `Spec`; invalid spec triggers one repair; clarification flow both modes.
**Docs:** USAGE (request writing, sources), CONFIGURATION (`intake.*`). **Changelog:** Added (multi-source requirements, context docs, Product Analyst spec stage, clarifying questions).
**Done when:** the same request text produces the same normalised bundle hash from CLI file, stdin, and API call.

#### Task 23 — Team registry and custom teammates

**Depends on:** 10,16 · **Model:** S

**Do:**
1. `team/registry.py` + `config/team/*.yaml` (built-ins moved out of `agents.yaml`; keep `agents.yaml` as the single source by having the registry read it — **do not keep two copies**). Teammate schema: `key, role, goal, backstory, tier (lead|worker|cheap|reviewer), tool_groups [fs_read, fs_write, search, command, git_read, mcp:<name>], allow_delegation, max_iter, enabled, modes, stages`.
2. User/project overrides: `engineering-team.toml [team.<key>]` or `.engineering-team/team.yaml` — enable/disable, change model/tier/prompt, or **add new teammates** (validated; clear errors; unknown tool groups rejected).
3. Build agents programmatically from the registry (the pipeline needs dynamic rosters, so `@CrewBase` stays only for the hierarchical strategy — note why in ARCHITECTURE). Planner assigns work packages to *available* teammate keys; unknown/disabled roles fall back to the nearest enabled generalist.
4. `engineering-team team list|show <key>` commands.
5. **Default tool groups per teammate** (replaces T16's default table; documented in TEAM.md, group contents in TOOLS.md): architect/analyst — `fs_read, search, code_intel, web*, board, notes, human`; backend/frontend engineers — `fs_read, fs_write, search, command, dev, runtime, code_intel, board, notes` (+ `browser` for frontend; `web*` optional); quality engineer — `fs_read, search, command, dev, runtime, browser, board, notes`; reviewers/security/analysts — **read-only**: `fs_read, search, code_intel, git_read, board, notes` (+ `dev` for security audits); devops — `fs_read, fs_write, search, command, dev, runtime`; writer — `fs_read, fs_write, search, code_intel, notes`; debugger — all but `web`. (`*` = only when `web.enabled`.) Tool groups not available (browser extra missing, web disabled) are dropped with a `doctor` hint, never an error mid-run.
6. Create `docs/TEAM.md` (roster table, tool groups, add-your-own tutorial).

**Tests:** merge/override precedence, validation errors, disabled teammate fallback, custom teammate used by a ScriptedLLM pipeline run, `hierarchical` strategy still builds.
**Changelog:** Added (teammate registry, custom teammates via YAML).
**Done when:** adding a 12th teammate in a project YAML requires no Python.

#### Task 24 — New teammates + reviewer fan-out

**Depends on:** 17,18,23 · **Model:** S

**Do:**
1. Add teammates (prompt quality matters — each with a concrete checklist, output contract, and "never claim a check you did not run" rule): `code_reviewer` (read-only; correctness/maintainability/diff-noise review), `security_engineer` (secrets, injection, authz, unsafe defaults, dependency audit via `pip-audit`/`npm audit`/`cargo audit` *when allowlisted/available*), `devops_engineer` (Dockerfile, CI workflow, `.env.example`, run scripts), `technical_writer` (README/usage/changelog for the generated project), `debugger` (reproduce → root cause → minimal fix; used by repair loop and T27). `product_analyst` from T22 and `codebase_analyst` (T25) round out the roster.
2. Add to the `new` recipe: `review` stage = **parallel read-only fan-out** (T17 helper) of `code_reviewer` + `security_engineer` (+ `ux_reviewer` role is *not* added) → structured `Finding`s (JSON) → consolidated `docs/review.md`; high/critical findings are fed to the repair loop (bounded by `max_repair_rounds`), then re-verified. After verification: `devops` and `writer` stages (can be disabled; `--profile smoke` and `team.profile = minimal` skip them), followed by the final re-verify (T18).
3. Findings contract validation, severity thresholds (`review.fail_on = high`), dedupe across reviewers.

**Tests:** ScriptedLLM reviewers produce findings → repair loop triggered → re-verified; minimal profile skips optional stages; findings dedupe.
**Docs:** TEAM.md (roster rows), ARCHITECTURE (stages). **Changelog:** Added (code reviewer, security, DevOps, technical writer, debugger teammates; parallel review).
**Done when:** `new` recipe full-profile run shows ≥2 reviewers executing in parallel lanes in the event log.

#### Task 25 — Adopt an existing project + codebase analysis (`analyze`)

**Depends on:** 13,17,19,22,24 · **Model:** O

**Why:** unlocks everything in your brief about legacy code.

**Do:**
1. `modes/repo_analyzer.py` (deterministic, no LLM): language statistics, manifests/package managers, **detected commands** (reuse T11's `devtools/detect.py`; test/lint/build from `package.json` scripts, `pyproject`/`tox`/`Makefile`, `go.mod`, `pom.xml`, `build.gradle`, `Cargo.toml`, `*.csproj`), entrypoints, test directories, CI config, size, git state, convention files (`README`, `CONTRIBUTING`, `AGENTS.md`, `CLAUDE.md`, `.editorconfig`, lint configs) → `RepoProfile` JSON.
2. **Isolation policies** (never edit the user's checkout blindly): (a) **branch mode** — git repo with a clean tree: create `engineering-team/<run-id>-<slug>`; (b) **worktree mode** (default when the tree is dirty or `--worktree`): `git worktree add .engineering-team/worktrees/<run-id>` so the user's working copy is untouched; (c) **copy mode** — non-git directory: copy to `workspace/<name>` (or init git with `--init-git`). Dirty tree without worktree ⇒ refuse unless `--allow-dirty`. `--adopt` (reserved in T3) is how an existing non-owned directory becomes a workspace. State dir `.engineering-team/` is excluded via `.git/info/exclude`, never by editing the user's `.gitignore`. Never push.
3. **Baseline:** run the detected checks through the `ExecutionBackend` before any change → `BaselineReport` (per check pass/fail, known pre-existing failures). T18's verifier compares against it ("no new failures") for brownfield recipes.
4. **Codebase Analyst** (`codebase_analyst`): split the repo into ≤N chunks (top-level packages/dirs by size), analyse **in parallel** with read-only tools (T17 helper), then a synthesis step writes `.engineering-team/codebase-map.md` (architecture, modules, key flows, conventions, hotspots, risks, how to run/test) cached by tree hash; later modes inject a size-capped version into agent context.
5. `engineering-team analyze --repo PATH [--deep]`: read-only (no branch/worktree needed); prints the profile + map location. Without `--deep` only the deterministic profile (zero LLM cost).
6. Recipe `adopt.yaml` composes these stages for reuse by T26–T28.

**Tests:** fixture mini-repos built in tmp (Python app with tests, Node app, non-git dir, dirty repo); command detection tables; each isolation mode leaves the original working tree byte-identical; baseline records a seeded failing test; parallel analysis with ScriptedLLM; `.git/info/exclude` entry; refuses dirty without flag.
**Docs:** USAGE (adopting a project, isolation modes), SAFETY (what is touched/never touched), ARCHITECTURE. **Changelog:** Added (`analyze`, existing-repo adoption, baseline, isolation modes).
**Done when:** `analyze` on a real small repo yields correct commands and a map; originals untouched.

#### Task 26 — Feature mode

**Depends on:** 25 · **Model:** S

**Do:** recipe `feature.yaml` + `engineering-team feature --repo PATH (--request|--request-file|…)`:
`adopt` → `spec` (analyst, criteria) → `impact` (architect, using the codebase map: files to change/add, risks, migrations, test plan; emits `Plan` with **write-scoped** work packages) → `implement` (parallel packages, T17) → `tests` (QA adds/updates tests *following the repo's existing test conventions*) → `verify` (baseline-aware: required = detected checks + spec checks; **no new failures vs baseline**) → `review` (parallel reviewers; additional diff-noise metric: lines changed outside touched scope) → `summary`.
Outputs: branch/worktree with stage checkpoint commits (T19); `CHANGE_SUMMARY.md` and `changes.patch` in the run dir; `engineering-team diff <run>` and `export-patch <run> --out FILE`; optional `--squash` for a single commit. Style rules: minimal diff, no unrelated reformatting, follow lint config.

**Tests:** fixture legacy repo + scripted agents applying a deterministic change; baseline comparison catches a regression; unrelated-file touch flagged; patch applies cleanly to the original commit.
**Docs:** USAGE (feature workflow), TEAM. **Changelog:** Added (`feature` mode).
**Done when:** the fixture run ends on a new branch with a green baseline-compared verification and an applicable patch.

#### Task 27 — Fix mode

**Depends on:** 25 · **Model:** S

**Do:** `engineering-team fix --repo PATH` with inputs: free-text bug report, `--trace-file` (stack trace/log), `--repro "<command>"`, or an issue text. Recipe `fix.yaml`: `adopt` → `triage` (debugger: parse trace, search suspects, ranked hypotheses) → `reproduce` (must create a failing test or repro script; **the controller runs it and requires it to fail before the fix = "red"**) → `fix` (minimal change, write-scoped) → `verify` (repro now "green", baseline no new failures, the new regression test stays in the repo) → `review` → `summary` with root cause and risk.
If it cannot reproduce within the attempt budget: stop with `needs-info` (exit 4) and a list of concrete questions — never "fix" blind unless `--allow-unreproduced`.

**Tests:** fixture repo with a seeded bug and ScriptedLLM that writes a failing test then the fix; red-before-green gate; non-reproducible path exits 4; trace parser for Python/Node/Java traces.
**Docs:** USAGE (fix workflow). **Changelog:** Added (`fix` mode, red→green gate).
**Done when:** the seeded bug is fixed with a regression test and the run record proves red then green.

#### Task 28 — Maintain + review modes, user recipes

**Depends on:** 25,26,27 · **Model:** S

**Do:**
1. `maintain --repo PATH --task <preset> [--goal "free text"]`: recipes in `modes/recipes/`: `add-tests` (characterization tests for untested modules; coverage delta when a coverage tool is detected), `refactor` (behaviour-preserving: tests green before and after, diff constraints), `upgrade-deps` (update manifests, run checks, try one-by-one on failure, report what could not be upgraded and why), `docs` (technical writer documents the legacy code), `security-audit` (security engineer + dependency audit → findings report; fixes optional), `custom` (goal text).
2. `review --repo PATH [--base BRANCH]`: read-only review of the working diff/branch by parallel reviewers → `findings.json` + Markdown (usable in CI, exits 3 when findings ≥ `review.fail_on`).
3. **User recipes:** `.engineering-team/recipes/*.yaml` and `~/.config/engineering-team/recipes/` can define/override recipes (stages from a documented catalogue; validated; clear errors). `engineering-team recipes list|show`.

**Tests:** each preset's stage list and policy; recipe validation errors; review on a fixture diff; upgrade bisect logic unit-tested.
**Docs:** USAGE (maintain/review/recipes), TEAM. **Changelog:** Added (`maintain`, `review`, user recipes).
**Done when:** a user can add a custom recipe file and run it without touching Python.

---

### Phase D — UX

#### Task 29 — Run report (HTML/Markdown)

**Depends on:** 10,18,21 · **Model:** S

**Do:** `engineering-team report <run> [--format html|md] [--open]`, and automatic generation at the end of every run to `run_dir/report.html`. Self-contained HTML (inline CSS/JS, **no CDN**, light/dark): summary + status banner; stage timeline with parallel lanes; **final board snapshot + per-card history and tool-call counts per teammate (T10)**; screenshots taken by the browser tools (T15); usage/cost vs budget; check results with log excerpts; **criteria coverage matrix** (criterion → checks → status; unverified flagged); findings; diff stats + unified diff viewer; warnings (budget, unavailable checks, unreproduced); environment/version block. **Escape all agent-/repo-derived text** (XSS test with `<script>` in a log). Use plain Python templating or Jinja2 — if Jinja2 is not already a declared dependency, declare it.

**Tests:** golden-structure tests; escaping; report for failed/cancelled/partial runs; large log truncation; deterministic ordering.
**Docs:** USAGE (reports). **Changelog:** Added (`report` command and automatic run report).
**Done when:** a fake failed run produces a readable report explaining why it failed.

#### Task 30 — Web UI — API server

**Depends on:** 10,21,22,29 · **Model:** S

**Do:** `engineering-team ui [--host 127.0.0.1 --port 8765]` (FastAPI + uvicorn as an optional extra `[ui]`, declared; `doctor` reports if missing). Endpoints (all JSON, versioned `/api/v1`, schemas = our Pydantic contracts):
`POST /runs` (mode, request text, uploaded files, repo path or project name, options: strategy, profile/provider, budget, roster toggles, sandbox, isolation, parallelism), `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/events` (**SSE** tailing `events.jsonl`, `Last-Event-ID` resume), `POST /runs/{id}/cancel|resume`, `POST /runs/{id}/answer` (clarification replies), **`GET /runs/{id}/board` (+ `?at=<seq>` replay snapshot), `GET /runs/{id}/cards/{card}` (card detail incl. tool-call trail), `POST /runs/{id}/cards/{card}/comments` and `POST /runs/{id}/notes` (steering, T10), `POST /runs/{id}/pause|unpause`, `GET /runs/{id}/agents` (teammate presence: state, current card, last tool call, counters), `GET /runs/{id}/artifacts` (screenshots/logs)**, SSE carries `board.*` events with full card payloads, `GET /runs/{id}/files` + `/files/{path}` (read-only, protected paths blocked), `/diff`, `/report`, `GET /config` (masked), `GET /doctor`, `GET /team`, `GET /recipes`, `GET /repo/inspect?path=` (validate folder, detected stack, git state).
**Runs execute as CLI subprocesses** (`engineering-team … --run-id`), not in-process threads: isolation, signal-based cancel, survives UI restarts; `max_concurrent_runs` setting; workspace lock enforces one writer.
**Security:** localhost bind by default; non-local host requires `--allow-remote` **and** a random bearer token printed at start; reject cross-origin requests (no CORS; require a custom header for mutating calls); path traversal protection; never return secrets or `.env`; request size caps.

**Tests:** `TestClient` for every endpoint (incl. board replay determinism and steering); SSE resume; two concurrent runs; auth modes; traversal/secret-leak attempts; upload limits.
**Docs:** USAGE (UI section: how to run, security notes), CONFIGURATION. **Changelog:** Added (local web UI backend).
**Done when:** a run started over HTTP streams events and can be cancelled and resumed.

#### Task 31 — Web UI — app shell: new run, history, results, settings

**Depends on:** 30 · **Model:** S (use `frontend-design` skill)

**Do:** static SPA served by the API, **no build step, no CDN** (vanilla ES modules + small CSS, or a vendored lightweight lib; record the choice and why). Design tokens for light/dark, one component vocabulary reused by T32. Screens:
1. **New run:** mode tabs (Build new · Add feature · Fix bug · Maintain · Review); large **textarea** (markdown hint, character count) **plus drag-drop/pick of one or more `.md/.txt` files**; repo path field with live validation (stack, git state, isolation recommendation); project name; team preset (minimal/full) with per-teammate toggles and the tool groups each gets; provider/model preset; budget fields with a pre-run cost estimate hint; advanced (parallelism, sandbox, strategy, isolation, web/browser tools on/off); Start.
2. **Run page (basic):** header with status/progress/cost, stage list, raw log stream, cancel/resume — a working fallback that T32 upgrades to the live dashboard (same route, richer content).
3. **Results:** summary, criteria coverage matrix, file tree + viewer, diff viewer, screenshots gallery (T15 artifacts), report download, patch export, "how to merge the branch" instructions.
4. **History:** list/filter/search runs with a mini progress bar per run. 5. **Settings/Doctor:** masked config, environment checks, tool availability (browser/web/docker).
Quality bar: keyboard navigable, ARIA live region for status, visible focus, AA contrast, dark/light, responsive to phone width, designed empty/loading/error states. Verify manually in the preview browser using `engineering-team ui --demo` (a ScriptedLLM-backed fake run so it works without API keys) and save 3–4 screenshots to `docs/assets/`.

**Tests:** static asset serving; API-shape contract tests shared with T30; a smoke script (Playwright optional/non-blocking) for the new-run → run-page flow in demo mode.
**Docs:** USAGE (UI: screens, screenshots), README (one screenshot). **Changelog:** Added (web UI: new run, history, results, settings).
**Done when:** you can paste a request, pick a repo, start a demo run, and open the diff/results — without a terminal.

#### Task 32 — Web UI — live dashboard: kanban, agent cards, activity feed, lanes

**Depends on:** 30,31 · **Model:** S (use `frontend-design` skill)

**Why:** F17 — "visibility of the process should be great". This is the centrepiece screen: you watch the team work like a real project board.

**Do (extends the T31 run page; data from T10 board + T8 events via T30 SSE; no new business logic in JS — the server sends full card payloads and snapshots):**
1. **Header:** run title, mode, status chip, elapsed, overall **progress bar** (T10 `progress()`), cost/tokens vs budget meter (turns amber at 80 %), **Pause · Resume · Cancel**, a **"Needs attention" strip** aggregating blocked cards (with reasons), budget warnings, `unavailable` checks, failed checks and pending **human questions** (modal answer form → `POST /answer`).
2. **Kanban board:** columns Backlog · Ready · In progress · Verifying · Blocked · Done (+ collapsed Failed/Cancelled). Cards show id, kind icon, title, assignee (consistent colour + initials), lane, live elapsed timer while in progress, criteria chips, attempt badge, blocked reason. Cards **animate between columns** when `board.card_moved` events arrive (respect `prefers-reduced-motion`); a short "K-004 moved to Verifying" message goes to an ARIA live region. Cards are **not human-draggable** (the controller owns state; say so in a tooltip) — a click opens a **card drawer**: description, status history, comments + **"Send steering note"** (→ T10 note delivery), artifacts (files, screenshots), the card's **tool-call trail** (tool, args summary, duration, ok), check evidence/log excerpts, tokens/cost.
3. **View switcher:** **Board** · **Swimlanes** (rows per teammate) · **Timeline** (Gantt of stages and parallel lanes with hover details; failed/repair attempts visible) · **Activity**.
4. **Agent roster panel:** one card per teammate with state (idle / working / waiting on tool / blocked / waiting for human), current card, last tool call + age, tool-call and error counts, tokens, model. Parallel agents visibly work at the same time.
5. **Activity feed:** live, auto-scrolling (pause on scroll-up), filter by agent/card/type/severity, tool calls collapsed to one line (expand for args/result excerpt, redacted), errors and repair attempts highlighted; virtualised list and `requestAnimationFrame`-batched updates so thousands of events stay smooth.
6. **Checks & findings side panel:** live list of independent checks (T18) with status, and review findings (T24) by severity.
7. **Replay:** for finished runs a time slider scrubs the board and roster through the run (`GET /runs/{id}/board?at=<seq>` computed server-side from `events.jsonl`), plus play/pause at 1×–16×.
8. **Demo mode** (`engineering-team ui --demo`): scripted run with 3 parallel lanes, a blocked card, a failed check + repair, a budget warning and a human question, so every state can be seen and tested without API keys. Capture screenshots (board, swimlanes, timeline, card drawer) into `docs/assets/` for README/USAGE.
Quality bar as T31, plus: card focus order is column-then-row with arrow-key navigation, colour is never the only status signal, and the page remains usable at 375 px width (columns become a horizontally snapping carousel).

**Tests:** API tests for board/cards/replay snapshots (determinism, `at` bounds); SSE delivers `board.*` events in order; demo-mode run produces every card state; steering-note round trip (UI → API → delivered once in the next agent prompt, using ScriptedLLM); accessibility smoke (aria-live updates, no duplicate ids) via the optional Playwright script; manual visual verification recorded in the task summary.
**Docs:** USAGE ("Watching a run": board, roster, feed, replay, steering), ARCHITECTURE (SSE/board data flow, 3 lines), README (screenshot). **Changelog:** Added (live kanban dashboard, agent roster, activity feed, timeline, replay, steering notes).
**Done when:** in demo mode you can see three agents working in parallel, watch cards move between columns, open a card to see its tool-call trail, send a steering note, answer a question, and replay the finished run.

---

### Phase E — Evidence, extensibility, release

#### Task 33 — Benchmark harness + task suite

**Depends on:** 18,20,26,27 · **Model:** O

**Do:**
1. `benchmarks/tasks/<id>/{task.yaml, request.md, acceptance/, fixture/?, reference/}`: **greenfield** (CLI notes, CSV validator, URL-shortener HTTP API, Markdown→HTML converter, minimal todo web app, cron-like scheduler) and **brownfield** fixtures (add a feature to a small Flask or Express legacy app, fix a seeded bug from a stack trace, add tests to an untested module, behaviour-preserving refactor). Each ≤ ~300 LOC fixtures. `acceptance/` holds **hidden behavioural checks run by the harness outside the workspace** (agents never see them).
2. `engineering-team bench run --tasks … --strategy pipeline|hierarchical|single --repeat N --parallel P --budget-usd X --provider …` — one subprocess per run, isolated workspaces under `benchmarks/.runs/`; per-run `result.json` (criteria pass/fail, cost, tokens, duration, repair rounds, tool failures, setup failures). `bench report` → Markdown + CSV; pass rate with **Wilson 95 % interval**; cost per *successful* task; all failures listed; `--dry-run` estimates cost from the price table before spending.
3. **Offline mode** (`--fake`): reference solutions per task replayed through `ScriptedLLM`/fake team so CI can validate the harness itself: reference passes, a deliberately broken solution fails, proving the checks discriminate.
4. Exclude `benchmarks/` from the wheel; document method and threat model (hidden checks, held-out subset).

**Tests:** harness unit tests; `--fake` end-to-end in CI (< 2 min); statistics helper tests.
**Docs:** create `docs/BENCHMARKS.md` (method only; results in T34). **Changelog:** Added (benchmark suite and `bench` command).
**Done when:** `bench run --fake` passes in CI and fails when a reference solution is broken.

#### Task 34 — Live evaluation and default decision *(needs your API spend)*

**Depends on:** 33 · **Model:** S

**Process:** Claude first runs `bench run --dry-run` and tells you the estimated cost; **you** run the live command it prints (screen 5 tasks × 3 strategies × 2 repeats first, then the full suite only if the numbers justify it), then ask Claude to continue with the results directory.
**Do:** analyse results honestly; write results into `docs/BENCHMARKS.md` (table, intervals, cost per success, **every failure and what caused it**, sample-size caveats, models/versions/dates); choose the **default strategy** (flip the setting if `pipeline` wins on quality-per-cost, otherwise keep `hierarchical`/`single`), tune defaults (`max_parallel_agents`, tier models, `max_repair_rounds`), decide the default sandbox, and align README claims *strictly* to measured numbers. Produce CV-ready bullets in the chat reply (not in the repo) using only measured values.
**Tests:** default-selection tests updated. **Changelog:** Changed (default strategy/models, with the evidence link).
**Done when:** every number in README/BENCHMARKS is reproducible from a committed result file.

#### Task 35 — Extensibility: MCP, conventions, plugins, hooks

**Depends on:** 23,28 · **Model:** S

**Do:**
1. **MCP per teammate:** `[mcp.<name>] url|command, roles=[…], allow_tools=[…]` in config; trust warning; replaces the single `ENGINEERING_DOCS_MCP_URLS` (keep it as an alias).
2. **Repo conventions:** auto-load `AGENTS.md`/`CLAUDE.md`/`CONTRIBUTING.md`/`.editorconfig` from the target repo (size-capped) into agent context for adopted repos; explicit `conventions_file` setting.
3. **Knowledge:** local retrieval over `--context-dir`/docs already ships in T14 (**Search Docs**); here add *optional* CrewAI knowledge sources/embeddings, strictly opt-in (document the embedding cost/provider requirement).
4. **Plugin tools:** Python entry-point group `engineering_team.tools` and project-local `.engineering-team/tools/*.py` (**off unless `allow_project_plugins = true`**; loud warning because it is code execution); plugin tools register in the T7 catalogue, declare their group, and follow the §4 tool design rules (telemetry, `ERROR:` convention, TOOLS.md row generated by `plugins list`).
5. **Hooks:** `before_stage`/`after_stage`/`on_finish` commands or webhooks in config (for notifications/CI); bounded timeout; secrets scrubbed.

**Tests:** MCP config validation (no network), conventions loading/caps, plugin discovery on/off, hook execution and failure isolation.
**Docs:** CONFIGURATION, TEAM, SAFETY (plugins = code execution). **Changelog:** Added.
**Done when:** a custom tool and a Slack-style webhook hook can be added with config only.

#### Task 36 — Docs overhaul, examples, release 0.2.0

**Depends on:** all · **Model:** S

**Do:**
1. **README rewrite** for a portfolio/open-source audience: one-sentence pitch, 30-second demo (GIF/asciinema path `docs/assets/demo.*` — generate with `vhs`/`asciinema` if installed, otherwise leave a documented TODO in this task's summary for you to record), mode table (new/feature/fix/maintain/review/analyze), install via `uv tool install`/`pipx` from Git (and PyPI once published), quickstart (CLI + UI), team roster, architecture diagram (Mermaid), a **kanban dashboard screenshot/GIF** (from T32 demo mode), a short "what tools do the agents have" line linking `docs/TOOLS.md`, measured results link, honest limitations, contributing/license. No env tables, no CLI reference (those live in CONFIGURATION/USAGE).
2. `examples/`: three curated, runnable examples — greenfield request, feature-on-legacy fixture, bug-fix scenario — each with request, expected outcome, and one **sanitised committed HTML run report**.
3. **Consistency audit:** if the then-current CrewAI supports Python 3.14, widen `requires-python`, the classifiers, ruff/mypy targets, CI matrix and README badge; remove plan task numbers (`(created T..)`) from CONTRIBUTING's documentation map; every setting documented exactly once (script `scripts/check_docs.py`: env vars in code ⊆ CONFIGURATION.md, relative links resolve; run it in CI), duplicates removed, ARCHITECTURE matches reality (module map, parallelism, run state, decisions + rejected alternatives), `AGENTS.md` version facts refreshed, `.env.example` current.
4. **Release:** `pyproject` version `0.2.0` (already set — verify), CHANGELOG `[Unreleased]` → `[0.2.0] — <date>` with a **Migration from 0.1.0** subsection (CLI, workspace default location, removed root `PROJECT_REQUEST.md`, env var compatibility, new run directory layout), compare links; `.github/workflows/release.yml` (tag `v*` → build → GitHub Release with changelog notes; PyPI publish via trusted publishing, disabled until you enable it); a "Releasing" section in CONTRIBUTING; final gate including wheel smoke on all CI platforms.
5. Print the exact commands for you to run (not run by Claude): `git tag -a v0.2.0 -m "0.2.0"`, `git push --tags`.

**Done when:** a stranger can go from README to a successful demo run in five minutes and every claim is backed by a test, a benchmark result, or a link.

---

## 7. Decision log

*(Executors append here: date · task · decision · reason. Deviations from this plan belong here.)*

| Date | Task | Decision | Reason |
|------|------|----------|--------|
| 2026-10-02 | plan | License MIT; copyright holder set to your legal name in `LICENSE` (you edited it) | Permissive, CV-friendly |
| 2026-10-02 | plan | `pyproject` stays `0.2.0` (in development); `v0.1.0` tag goes on `0ba55a3` | 0.1.0 is already committed history |
| 2026-10-02 | plan | Parallelism by write-scope ownership, not git-worktree merging | Deterministic, no merge-conflict resolution step; revisit if too restrictive |
| 2026-10-02 | plan | Web UI = FastAPI + static SPA, runs as CLI subprocesses | No JS build toolchain; process isolation and easy cancel; survives UI restarts |
| 2026-10-02 | plan | Default strategy stays `hierarchical` until T34 measures `pipeline` | Avoid shipping an unmeasured default |
| 2026-10-02 | plan | Keep `train`/`replay`/`test`/`run_with_trigger` script names | The `crewai` CLI invokes them by name |
| 2026-10-02 | plan | Default sandbox = `local`, Docker recommended | Zero-setup first run; revisit after T34 |
| 2026-10-02 | T5 | `run_command` moved from a `ProjectWorkspace` method to `tools/commands.run_command(workspace, …, gate=)`; `ProjectWorkspace._reject_protected` became public `reject_protected` | The plan splits command running out of `workspace.py`; `commands.py` needs the protected-path check without importing a private name |
| 2026-10-02 | T5 | `WriteScope` lives in `tools/scope.py` with its own gitignore-style matcher (no `pathspec` dependency); no `!` negation, `deny` wins | Plan names no file for it; avoids a new dependency; deny list covers exceptions |
| 2026-10-02 | T5 | Lock uses `fcntl.flock` only (macOS/Linux, the supported platforms); stale detection is the kernel dropping the lock on holder exit, with pid liveness used only to word the `WorkspaceBusy` message | `flock` makes a crashed holder's lock free automatically, so a pid check cannot be needed to recover |
| 2026-10-02 | T5 | `WorkspaceBusy` subclasses `ValueError`; `--reset` probes the lock before deleting; the CLI holds the lock from workspace preparation until the run ends (including `--prepare-only`, `replay`, `train`, `test`) | Reports as exit 2 through the existing usage-error path; never delete a workspace another run is writing to |
| 2026-10-02 | T5 | Tools honour `ctx.cancel_event` now (return an `ERROR:` and do nothing); `read_only=True` returns only list/read tools (the command tool is excluded) | Cheap to do while the factory is rewritten; T7's groups will refine what read-only means |
| 2026-10-02 | T5 | `request.md`/`run.json` stay at `.engineering-team/` for now; only `crew-log.json` moved to `runs/<run-id>/` | The plan scopes only the log to `run_dir`; T8 replaces the other two with the run store |
| 2026-10-02 | T6 | `ScriptedLLM` is a `crewai.BaseLLM` subclass supporting both native tool calls (default) and text ReAct (`native_tools=False`); items are strings, `ToolCall`s, callables, or `Turn` (usage); a bare string in ReAct mode is wrapped as `Final Answer:` | Covers both executor paths in CrewAI 1.15.23 without a second fake |
| 2026-10-02 | T6 | Test agents are built with `max_retry_limit=0` | CrewAI otherwise retries a failed agent execution three times, hiding `ScriptExhausted` behind a later call number |
| 2026-10-02 | T6 | Added an autouse guard failing non-loopback socket connects in tests (not in the plan) | Makes "zero network" enforced rather than assumed; `live` tests are exempt |
| 2026-10-02 | T6 | `live` tests still get the dummy `OPENAI_API_KEY` from the hermetic fixture | No live test exists yet; the first one must opt out of that scrub (decide when T33 adds it) |
| 2026-10-02 | T7 | Search Project Files has one pure-Python implementation; no `rg` fast path | `rg` output would pass through the backend's bounded log window, so results would differ by machine and be truncated; Python searches 5,000 files in well under a second. Revisit if a real tree is slow |
| 2026-10-02 | T7 | Only the root `.gitignore` is read (with `!` negation, last rule wins); nested `.gitignore` files are not | Keeps the walker simple; heavy directories are always skipped anyway |
| 2026-10-02 | T7 | Windows `taskkill /T` not implemented; `LocalBackend` is POSIX (macOS/Linux) only | pyproject classifiers list only macOS and Linux; matches the T5 `flock` decision |
| 2026-10-02 | T7 | `ToolSpec.factory` builds all tools of its module (`Mapping[name, tool]`); `build_tools` calls each distinct factory once per run | Tools of a module share closures; avoids rebuilding a bundle per tool |
| 2026-10-02 | T7 | `read_only=True` now means "only tools whose spec is read-only" across all groups (was: list/read only in T5) | Read-only teammates need search/outline/repo-map/changes; the spec flag is the single source of truth |
| 2026-10-02 | T7 | Command stdout and stderr are merged into one stream and one log | One ordered log is what an agent needs; the backend cannot interleave two pipes reliably |
| 2026-10-02 | T7 | Agents may read (never write) `.engineering-team/runs/<id>/commands/*.log` through `resolve(readonly=True)`; all other controller state stays hidden | Plan requires `full log:` to be readable with Read File Range |
| 2026-10-02 | T7 | `RunContext` gained `backend`, `baseline` (workspace snapshot, text kept for files <= 200 KB, 20 MB total), `events` (no-op sink) and `tool_gate`; `execution.backend` setting still unused (T20 selects Docker) | Plan: telemetry hook is a no-op sink until T8; budget hook until T9; snapshot taken by `RunContext` |
| 2026-10-02 | T7 | `List Project Files` and `Read Project File` kept next to the new `Project Tree` / `Read File Range` | Plan says existing tools stay; the new ones add annotation and ranges |
| 2026-10-02 | T7 | `just` is not in the default command allowlist, so `Run Script` on justfile recipes needs `ENGINEERING_COMMAND_ALLOWLIST=just` (the error says so) | Allowlist stays a deliberate decision; `make`, `npm`, `pnpm`, `yarn`, `bun`, `uv` are already allowed |
| 2026-10-02 | T7 | `docs/SAFETY.md` now holds the boundary text; README and ARCHITECTURE link to it | Single home per fact (Documentation map) |
| 2026-10-02 | T8 | `Event` has a `seq` field (per-run counter in file order) beyond the plan's list | The dashboard needs `Last-Event-ID` resume and `?at=<seq>` replay (T21/T32); CrewAI delivers events from a thread pool, so timestamps alone do not give a stable order |
| 2026-10-02 | T8 | `ProjectCommands` fields are `list[str]` (several command lines per category); `Plan.stack` is a free-text string; `AcceptanceCriterion.kind` is a free string | Real stacks need more than one setup/test command; plan gave no enums and LLM output should not fail validation on a new label |
| 2026-10-02 | T8 | Contracts ignore unknown fields (`extra="ignore"`) rather than preserving them | Forward compatibility for readers; rewriting a newer manifest with an older version would drop its extra fields, which resume (T18) must not do across versions |
| 2026-10-02 | T8 | `RunContext.create` defaults `events` to a `JsonlSink` (scrubbing ambient secrets), so every context writes `events.jsonl`; `settings.secret_values()` in `settings.py` reads the env | settings.py stays the only configuration reader; tests and CLI get the log without extra wiring |
| 2026-10-02 | T8 | Added `request.md` and `settings.json` to the run directory, and every CLI invocation (including `--prepare-only`, `train`, `test`, `replay`) creates a manifest with its `mode` | The manifest holds only hashes, so the request and effective settings must live beside it to "fully describe" a run |
| 2026-10-02 | T8 | CrewAI's `tool_usage_*` events are bridged as `tool.finished`/`tool.error` next to our own `tool.call` | `tool.call` covers project tools with duration; CrewAI's events also cover delegation tools. Consumers should count `tool.call` for project-tool activity |
| 2026-10-02 | T8 | Bridge tagging relies on `contextvars` being copied into CrewAI's handler threads, but not into threads CrewAI creates elsewhere (e.g. `max_execution_time`) | Verified in 1.15.23 for sequential crews (hierarchical not exercised offline); T19's workers must use `copy_context()` |
| 2026-10-02 | T9 | Price data is `data/pricing.toml`, not `pricing.yaml` | `tomllib` is stdlib; YAML would need a declared runtime dependency (`pyyaml` is only a dev dependency), and user overrides already live in TOML config |
| 2026-10-02 | T9 | Prices re-checked against the providers' pages on 2026-10-02 (OpenAI developers.openai.com/api/docs/pricing, Anthropic platform.claude.com/docs/en/about-claude/pricing, Google ai.google.dev/gemini-api/docs/pricing); they matched the T4 numbers. `ModelFacts.price`/`price_note` were removed so prices have one home | Single source of truth; Gemini's 2027 price change is a second dated row |
| 2026-10-02 | T9 | `BudgetGuard.check()` takes no `ctx` argument: the guard is bound to the run's usage tracker, cancel event, and event sink when `RunContext` is created | The guard needs those objects before the frozen context exists; a ctx parameter would only be read back from them |
| 2026-10-02 | T9 | A limit is "exceeded" when usage is strictly greater than the limit; the tool-call gate refuses the call that would pass it | Reaching a limit exactly is allowed to finish; refusing the over-limit call keeps `max_tool_calls` exact for sequential work |
| 2026-10-02 | T9 | A budget stop ends the run `failed` with `BudgetExceeded`, not `cancelled`; the CLI prints `Engineering team run stopped: ...` without a traceback (exit 1) | `cancelled` is for user cancellation (T18); a budget stop is an error the caller must act on |
| 2026-10-02 | T9 | Added `RunRecorder.stage(name)` (events, stage tag via `contextvar`, `StageRecord` upsert, budget check at both boundaries) | The plan needs stage boundaries for budget checks; T18 builds the stage runner on top of it |
| 2026-10-02 | T9 | An unpriced model makes `estimated_cost_usd` `null` for the whole run (the known part is in `known_cost_usd`) and a cost budget unenforceable (reported via `budget.warning`) | A partial sum shown as the total would understate cost |
| 2026-10-02 | T9 | The bridge emits `llm.call` models as `provider/model` (taken from the agent's LLM) | CrewAI's event carries the bare name; prices are keyed by provider-qualified IDs. Lookup also accepts a unique bare name |
| 2026-10-02 | plan | Root `PROJECT_REQUEST.md` replaced by `examples/tiny-notes/request.md` + `--example` | Wheels cannot see repo-root files; avoids two copies |
| 2026-10-02 | plan | Task board is controller-owned truth; agents may request transitions but only the controller can mark `done`/`failed` | A kanban that agents can fake would be worse than none (same principle as T18) |
| 2026-10-02 | plan | No interactive shell/PTY tool | Background processes + log reading cover the real needs; keeps execution deterministic and auditable |
| 2026-10-02 | plan | Web and browser tools are off/optional by default; browser only reaches loopback | Network/prompt-injection and sandbox surface; opt-in keeps the default run offline-safe |
| 2026-10-02 | plan | Code intelligence = `ast` + tree-sitter (optional extra) + regex fallback; LSP bridge deferred | Zero-setup default, accurate when the extra is installed; Type Check (T11) covers diagnostics |
| 2026-10-02 | plan | Dashboard replay computed server-side from `events.jsonl` | One implementation of board logic (Python), thin JS client |
| 2026-10-02 | 2 | Type checker = mypy (blocking); initial run had 23 errors, almost all CrewAI's untyped YAML `config=` pattern in `crew.py` → fixed real ones, narrow per-module override (`call-arg`, `arg-type`) for `engineering_team.crew` | pure-Python, no Node needed; clean after a reasonable pass |
| 2026-10-02 | 2 | Dev deps added: `mypy`, `pyyaml` (tests parse workflow/YAML; also a CrewAI dependency), `tomli` was added for Python 3.10 and removed again when 3.10 was dropped (stdlib `tomllib` suffices) | metadata/CI tests need TOML and YAML parsing |
| 2026-10-02 | 2 | `astral-sh/setup-uv` pinned to `v10.2.0` (no floating major tag exists), `actions/checkout@v7`; Dependabot keeps both current | verified against the GitHub API |
| 2026-10-02 | 2 | Documentation map moved to CONTRIBUTING.md; `plan_task.py show` prints it | single home for the map |
| 2026-10-02 | 2 | Supported Python = 3.11–3.13 (3.10 dropped by request; 3.14 not possible yet because CrewAI 1.15.23 declares `<3.14`) | Revisit when CrewAI supports 3.14 (Task 36 audit item) |
| 2026-10-02 | 3 | Bundled example lives in the package (`src/engineering_team/examples/tiny-notes/request.md`), not repo-root `examples/` + Hatch `force-include` | Works identically when installed, editable, and from a source checkout with no build magic; repo-root `examples/` stays free for T36's curated showcases |
| 2026-10-02 | 3 | `--force-reset` implies `--reset`; `--adopt` exists as a hidden flag that errors "not implemented yet" (T25 implements it); 0.1.0 projects (`.engineering-team/run.json`) are treated as owned and upgraded with `owner.json` | Fewer flags to combine; keeps existing user workspaces working |
| 2026-10-02 | 3 | Entry points return int exit codes (was: crew result → `sys.exit(obj)` ⇒ status 1 on success). Only the *prepare* phase maps `ValueError` to exit 2; any failure inside the crew run is exit 1 with traceback | Pydantic `ValidationError` is a `ValueError`; framework errors must not be reported as usage errors |
| 2026-10-02 | 3 | Workspace preparation moved from `main.py` to `engineering_team/workspaces.py` | `main.py` was growing; T5 builds on this module |
| 2026-10-02 | 3 | Protected-path check is case-insensitive and also rejects absolute command arguments into `.git`/`.engineering-team/`; the crew log path is built from `workspace.root` (controller-owned), not via the agent-facing `resolve()` | macOS/Windows are case-insensitive; the controller must not use the agent boundary for its own files |
| 2026-10-02 | 4 | **Model defaults changed to the GPT-6 family**; after review the presets use *only* `gpt-6.1-sol` and `gpt-6-luna` (no Astra, no `gpt-5.6-*`) | OpenAI's model page (developers.openai.com/api/docs/models) lists only GPT-6; `gpt-5.6-*` could not be verified. **Not live-tested** (no paid calls were made) — Task 34's evaluation must confirm these IDs work end to end |
| 2026-10-02 | 4 | OpenAI GPT-6 models use `api="responses"` + explicit `context_window_size` (75 % of 1.05M) | docs: `gpt-6.1-sol` has no tool calling on Chat Completions (luna only with effort `none`); CrewAI 1.15.23 does not know GPT-6 IDs and would assume ~7K context |
| 2026-10-02 | 4 | `reasoning_effort` is sent only to OpenAI/Azure; Anthropic/Gemini ignore it (adaptive thinking) | CrewAI 1.15.23 provider code: only OpenAI/Azure read it; its type also rejects `max` (accepts none…xhigh) |
| 2026-10-02 | 4 | `max_iter` lives on the resolved role (profile slot / override), not on the tier; tiers extended with `max` and `reviewer` | iteration caps differ per profile, not per model; `max-quality` and T24's reviewers need the extra tiers |
| 2026-10-02 | 4 | Optional extras `anthropic`, `google`, `azure` in `pyproject.toml` | CrewAI ships these native SDKs as extras; declared rather than relying on transitive installs |
| 2026-10-02 | 4 | `resolve_run_profile` removed; smoke marker handled by `Settings.for_request` (only when no layer chose a profile); CLI gains `--provider`, `--config`, and `config show` (dispatched before argument parsing until T21) | profile must live in `Settings`, not `os.environ` |
| 2026-10-02 | 4 | Credential pre-flight runs for real runs but not for `--prepare-only` | preparation must work without credentials |
| 2026-10-02 | 4 | **Final model set (by request):** OpenAI `gpt-6.1-sol` + `gpt-6-luna`; Anthropic `claude-sonnet-5-5` + `claude-opus-5-5`; Google `gemini-3.8-flash` only; Ollama `qwen3.8:27b` (best open model ≤30B in the library: 18 GB, 256K ctx, tools + thinking). Removed Astra, Fable, Haiku, other Gemini models, qwen3-coder | Price ladder: luna $0.1/$0.5 < gemini-3.8-flash $0.75/$3.75 (promo to 2026-12-31) < sol = sonnet-5.5 $2/$10 < opus-5.5 $4/$20; a test enforces that tiers never get cheaper as they get more capable |
| 2026-10-02 | 4 | Tier mapping: `max` = most expensive available (Opus; Sol at xhigh for OpenAI), `lead`/`reviewer` = mid price (Sol/Sonnet), `worker`/`cheap` = cheapest (Luna; Sonnet for Anthropic, which has nothing cheaper) | cost-aware routing: expensive models only where judgement matters |
| 2026-10-02 | 4 | **Azure disabled by default** (`enable_azure = false`): `provider = "azure"` and `azure/...` models are rejected until enabled | requested; keeps accidental Azure configuration from reaching a run |
| 2026-10-02 | 10 | Card models live in `board/models.py` as `Contract` subclasses, not in `contracts.py` (nested `Comment`/`Move` are unversioned parts) | `contracts.py` stays free of board concepts; the board package imports `contracts`, never the reverse |
| 2026-10-02 | 10 | Atomic file writers moved from `runtime/run_store.py` to a leaf module `atomic_io.py` | `board` needs them, and `run_store → tools.workspace → tools → board → run_store` was an import cycle |
| 2026-10-02 | 10 | The board's coordination tools are separate tools (List Board Cards, Get Board Card, Add Subtask, Move Card, Block Card, Unblock Card, Comment On Card, Report Progress), not one `Task Board` tool with an `action` argument; Report Progress is in group `board` | Typed per-tool signatures are easier for LLMs to call correctly; refusals stay specific |
| 2026-10-02 | 10 | `read_only` for `board`/`notes`/`human` tools is `yes`: it now means "never changes project files or runs commands"; these tools change only run state | Read-only teammates (reviewers) must still comment, flag blockers, and ask questions |
| 2026-10-02 | 10 | `build_tools(..., agent=)` names the teammate; board and notes tools act as it (the actor is never a tool argument), and `tool.call` events carry it. `crew.py` passes `PROJECT_GROUPS` so the 0.1 crew does not get coordination tools until the pipeline (T16) populates the board | An agent must not be able to claim to be the controller or another teammate; an empty board would only cost tokens |
| 2026-10-02 | 10 | WIP limit (`parallel.max_parallel_agents`) counts in-progress `work_package` and `repair` cards only; the controller sending `verifying → in_progress` is exempt | Stage cards are containers and subtasks belong to their parent's agent; counting them would make a full-parallel run impossible |
| 2026-10-02 | 10 | A paused run holds every tool call (in `ToolEnv.run`, before anything else) until unpaused or cancelled | Between agent steps is the only safe point we control; the "PAUSED" marker is only in `board.md`, since a held tool call never returns to show it |
| 2026-10-02 | 10 | Ask Human is a `HumanChannel` on `RunContext` (non-interactive by default; a front end calls `enable()`, reads `pending()`, calls `answer()`) rather than a callback argument | One object T22 can wire to the CLI prompt and T30 to the web API; the default needs no wiring |
| 2026-10-02 | 10 | Decision log at `.engineering-team/decisions.md` (workspace, outlives runs), written only via `Log Decision`, read via `Read Note key='decisions'`; capped at 50,000 characters | "Project-level" means across runs; keeping it under the controller directory keeps agents' file tools away from it |
| 2026-10-02 | 10 | Progress weights: repair/finding/user_note cards weigh 0; cancelled cards leave the total | Late discoveries must not make progress go backwards; cancelled work is no longer planned |
| 2026-10-02 | 11 | `devtools/` has pure parsers (`parsers/`), command plans (`plans*.py`), and `DevRunner` (`runner*.py`) as a base class plus mixins; the 10 agent tools are thin wrappers in `tools/dev_tools.py` | Keeps every module under ~400 lines and lets the verifier (T18) call `DevRunner` without the tool layer |
| 2026-10-02 | 11 | Parser fixtures for toolchains not installed on the author's machine (jest, vitest, go, cargo, dotnet, RSpec, Maven, PHPUnit, eslint, tsc, pyright, rubocop, phpcs, golangci-lint, clippy, coverage and audit tools) are hand-written to the documented formats; pytest, unittest, minitest, ruff, and mypy are captured from real runs (`tests/fixtures/devtools/README.md`) | The plan asked for captured outputs; this machine's go, cargo, and php binaries are x86-only and cannot run. Replace a fixture with a real capture if a parser disagrees with a real run |
| 2026-10-02 | 11 | Dev tools may run a fixed set of well-known dev executables (`DEV_EXECUTABLES`: eslint, prettier, golangci-lint, rubocop, phpunit, ...) without a project-wide allowlist entry; `Run Project Command` still refuses them; `tools.dev.extra_executables` adds more | The tools choose the command and flags and the agent only supplies paths and filters (flag-like values are rejected), so the risk of the allowlist (arbitrary commands) does not apply |
| 2026-10-02 | 11 | Install Dependencies and Dependency Audit are `needs_network = yes` and refused when `tools.dev.allow_network = false`; Install is documented as the setup phase | The local backend cannot enforce `network`, so a setting is the only switch until T20; audits need a registry too |
| 2026-10-02 | 11 | The `dev` group is not part of `PROJECT_GROUPS`, so the 0.1 crew does not get the tools until T23 assigns groups to teammates | Same reasoning as the coordination tools in T10: no behaviour change to the 0.1 flow |
| 2026-10-02 | 11 | `PYTHONDONTWRITEBYTECODE=1` is set in the environment of every project command (`tools/commands.py`) | An agent edits and re-runs within a second; same-size edits were served from stale `.pyc` files (regression test in `test_tools_commands.py`) |
| 2026-10-02 | 11 | `prepare_command` gained `max_timeout` (default unchanged, 300 s); dev tool timeouts are `tools.dev.*_timeout` settings, a call may only shorten them | Installs and coverage runs need longer than `Run Project Command` allows |
| 2026-10-02 | 11 | A missing tool counts only when the output names the tool the command ran (`No module named 'X'` where X is the `-m` module, `command not found` for the program, ...); a project's own failing import is a test failure | The first version marked a test run `UNAVAILABLE` because the project imported a missing module |
| 2026-10-02 | 11 | A format check that exits 1 without a recognisable file list is `failed` with the raw output, never a pass; ruff 0.12 and 0.16 print different `--check` formats and both are parsed | Output formats change between tool versions; the fallback keeps a format change from turning into a false pass |
| 2026-10-02 | 11 | Frameworks without a recognised runner fall back to the project's own `test` script (raw output, exit code) instead of failing | Matches the plan's "unknown framework -> raw log with exit code"; `Run Project Command` stays for anything else |
| 2026-10-02 | 12 | No interactive shell or PTY tool | Unbounded (no timeout, no allowlist), state the controller cannot see, and non-deterministic output; long-lived processes plus `Read Process Logs` and one-shot commands cover the real needs (also in TOOLS and SAFETY) |
| 2026-10-02 | 12 | `HTTP Request` allows loopback only on ports the run started, reserved, declared, or waited for (not any loopback port); `needs_network` stays `no` in the catalogue because other hosts need an explicit `network.http_allowlist` entry | The plan's "ports of processes started in this run"; a model server or the web UI on loopback must not be reachable by an agent by default |
| 2026-10-02 | 12 | Process lifetime is enforced by a registry (`runtime/processes.py`) hooked into `RunRecorder.stage` and `running`, a reaper thread for cancel and lifetime, and `atexit`; a `SIGKILL` of the controller still orphans children (documented, Docker removes it) | Covers stage end, cancel, crash, and run end as the plan requires; nothing in-process can survive its own kill |
| 2026-10-02 | 12 | `LocalProcess` flushes its log file after every chunk | The file was buffered, so a background process's log was empty until 8 KB or exit; found by the first runtime test (regression test in `test_execution_backend.py`) |
| 2026-10-02 | 12 | New settings sections `[runtime]` (`max_background_processes`, `process_lifetime_seconds`, `max_http_response_chars`) and `[network]` (`http_allowlist`); T14's `[web]` will add its own | The plan names `max_background_processes` and `network.http_allowlist` without a home for them |
| 2026-10-02 | 12 | Environment Info probes run through the backend with fixed argv (no allowlist), but `which` is host-side; with the Docker backend the "missing" list can disagree with the container until T20 adjusts it | Probes are not agent input; tool discovery inside a container is T20's concern |
| 2026-10-02 | 12 | SQLite inspection reads the file directly with Python's `sqlite3` rather than through the backend | It is a read of a workspace file like the read tools, not process execution; opened `mode=ro` with an authorizer |
| 2026-10-02 | 13 | **No tree-sitter in 0.2.0**: Python via `ast`; JS/TS, Go, Java/Kotlin, C#, Rust, Ruby, PHP via regexes (declarations, members, imports) with brace counting for extents; no `[code-intel]` extra and no new dependency | The plan says to record the choice. An optional grammar package cannot be exercised in the hermetic offline suite and makes results differ by installation; the regex path covers the nine tools for all eight languages, and `codeintel/definitions.py` and `imports*.py` are the seam where a parser can slot in later |
| 2026-10-02 | 13 | `Hotspots` and `Find TODOs` read Git through `codeintel/git.py` (fixed `git log --numstat` / `git blame --line-porcelain` run through `ctx.backend`, no allowlist) instead of T19's `GitPort`, which does not exist yet; without a repository they answer `No Git history: …` (not an `ERROR:`) | T13 depends only on T7; the plan says to degrade gracefully. T19 replaces `GitHistory._run` and keeps the parsers |
| 2026-10-02 | 13 | All nine `code_intel` tools are `read_only = yes`, including the two that run fixed `git` commands; the "read-only" definition in TOOLS.md is now "never changes project files or runs agent-chosen commands" (`Environment Info` stays `no` because it probes the machine) | Analysis and review teammates need hotspots and TODO ages; the argv is not agent input and cannot write |
| 2026-10-02 | 13 | `Who Imports` has a `depth` argument (1-4) and `Find Related Tests` accepts a file or a symbol; every tool re-reads the project on each call (no cache) | "What could break" needs transitive dependents; an agent edits files between calls, so a cache would serve stale answers, and indexing this repository takes about 0.2 s |
| 2026-10-02 | 13 | Hotspot score = commits x complexity, complexity = decision-point count + lines/20 (min 1); lock, minified, snapshot, map, and svg files are excluded | A deliberately simple, explainable proxy; the plan asks for "churn x size/complexity proxy" |
| 2026-10-02 | 13 | `codeintel/__init__.py` imports `engineering_team.tools` first | `tools.registry` imports `codeintel`, which imports `tools.ignore`/`workspace`; importing a `codeintel` module first would otherwise find a half-initialised package |
| 2026-10-02 | 14 | CrewAI's `SerperDevTool`/`ScrapeWebsiteTool` (crewai-tools 1.15.23) are not used; Serper, Brave, and Tavily are small own providers behind `SearchProvider`, and `Fetch URL` is own code | The built-ins call `requests` directly, so they cannot do DNS resolution + address pinning or per-hop redirect checks, which the plan makes the core safety property |
| 2026-10-02 | 14 | HTML to Markdown is a stdlib `html.parser` extractor; BM25 is ~60 lines; no new dependencies | `beautifulsoup4`/`lxml` are only transitive; the rules forbid relying on them, and the extraction needed (main/article, boilerplate and hidden-element removal, links, lists, code, tables) is small. PDF text is not supported (the plan says optional) |
| 2026-10-02 | 14 | `Search Docs` is in its own always-registered group `knowledge`, not `web`; the group list is now `..., code_intel, knowledge, web, runtime, ...` | The plan's "with web disabled no network tool is even registered" is about network tools; local retrieval is offline and is the shared knowledge home for T22/T35 |
| 2026-10-02 | 14 | Settings: `web.*` (enabled, roles, search_provider, allow/deny_domains, max_requests_per_run, timeout_seconds, max_download_bytes, max_page_chars) and `knowledge.context_dirs`; `ENGINEERING_ALLOW_WEB` and `--allow-web`; API keys are read only from `SERPER_API_KEY`/`BRAVE_API_KEY`/`TAVILY_API_KEY` into a private `Settings` attribute (never in `describe()`, `repr`, or `model_dump`) | Keys by environment variable name only; `config show` reports set/MISSING |
| 2026-10-02 | 14 | Per-role allow is `web.roles` checked in `build_tools(agent=)`; the teammate registry (T23) will feed real role names; the web tools are `read_only = yes` (they change no project file and run no command) | Roles do not exist yet; read-only teammates may be given research ability by configuration, the network switch being the separate permission |
| 2026-10-02 | 14 | `RunContext.web_requests` (a `RequestLimiter`) is the run-wide request cap; `safenet.NETWORK` is the single test seam for resolver/connector/TLS | A per-tool counter would multiply by teammate; tests must prove blocking without any real network |
| 2026-10-02 | 14 | npm `Package Info` uses the registry's `/latest` document, which has no release date | The full packument is megabytes for popular packages; the plan's "release dates" is met for PyPI, crates.io, and Go and reported as unavailable for npm |
| 2026-10-02 | 14 | Fixtures `tests/fixtures/web/*` are real responses recorded 2026-10-02 and trimmed (README there) | The plan asks for parsers on recorded JSON |
| 2026-10-03 | 15 | Playwright (`playwright>=1.63,<2`, installed 1.63.0) is the optional extra `browser`, declared in `pyproject.toml` and `uv.lock`; the tools are registered only when it is importable, a missing browser binary is an actionable error at first use (not at registration) | The plan says "not registered when unavailable"; probing for the binary needs the Playwright driver or the environment, and `os.environ` may only be read in `settings.py` |
| 2026-10-03 | 15 | `browser.channel` (`chromium`, `chrome`, `msedge`) selects the browser; `chrome` uses an installed Google Chrome with no download | Verified with Playwright 1.63 and Chrome 154 here; saves the ~150 MB `playwright install chromium` for people who have Chrome, and is how the browser tests ran without a download |
| 2026-10-03 | 15 | **Navigation is enforced by a per-run filtering proxy (`browsertools/proxy.py`), not by Playwright routes.** Chromium follows a fulfilled or continued redirect without routing the next hop, and WebSockets bypass `context.route`; a page on an allowed port could bounce the browser to a refused service (reproduced, then fixed). The proxy checks every request, hop, tunnel, and upgrade, needs a per-context token, never follows redirects, and pins the address of allowlisted external hosts | "Only localhost and this run's ports" had to hold for every hop; the redirect and WebSocket tests in `test_browser_tools.py` and the proxy tests in `test_browser_proxy.py` pin this |
| 2026-10-03 | 15 | External pages need `web.enabled` **and** a `web.allow_domains` entry (an empty allow list allows none), plus the public-address check | "Only through the T14 allowlist rules": the browser cannot do `Fetch URL`'s open-web fetching safely, so only explicitly trusted hosts are reachable |
| 2026-10-03 | 15 | Snapshots use Playwright's `locator.aria_snapshot(mode="ai")` (refs such as `[ref=e12]`) and refs are used through the `aria-ref=` selector; all browser calls run on one worker thread per run (`BrowserRegistry`) | The sync API is bound to its thread and agents call tools from any thread; refs are stable for an unchanged page and a stale ref times out with an explanatory message |
| 2026-10-03 | 15 | `Browser Screenshot` returns the file path (and emits `artifact.created`); it does not attach the image to the tool result | CrewAI 1.15.23's `AddImageTool` accepts an image path or URL for `multimodal=True` agents; tool results are text, so a vision-capable reviewer opens it with that tool |
| 2026-10-03 | 15 | Deferred: the Verifier's `browser_script` check type (T18, which does not exist yet) and the default `browser` group for `frontend_engineer`/`quality_engineer` (T23) | Both consumers are later tasks; `build_tools(groups=["browser"])` and `BrowserRegistry` are the interfaces they will use |
| 2026-10-03 | 15 | The CI test job installs the extra; a separate `browser` job installs Chromium and runs `pytest -m browser`; browser tests skip when neither Chromium nor Chrome can launch | The catalogue tests list the browser tools, and real-browser tests need a binary |
| 2026-10-03 | 15 | Chrome's own background requests (autofill, updates, a search-engine preconnect) are refused by the proxy like everything else but not shown in the page's console report | They are not the page's traffic and would read as the page trying to reach Google |
| 2026-10-03 | 16 | **CrewAI native checkpointing (`checkpoint=True` / `CheckpointConfig`, 1.15.23) is not used; stage state is our own.** It snapshots a crew's runtime when events such as `task_completed` fire, but a pipeline stage is a one-agent, one-task crew, so a task-boundary checkpoint inside it holds nothing between "not started" and "done"; what matters (files the tools changed) lives in the workspace, which the tree hash covers | Plan asked to evaluate and record the decision; revisit when stages become multi-task |
| 2026-10-03 | 16 | The Flow is one `@start` (`begin`) plus one `@router` (`advance`) that walks the recipe and returns `next_stage`/`finished`/`failed`/`cancelled`, not one hard-coded method per stage | Recipes are data (user recipes in T28); the router label must differ from the handler's name or CrewAI rejects the flow as an infinite loop (verified on 1.15.23) |
| 2026-10-03 | 16 | `pyyaml>=6.0,<7` moved from the dev group to runtime dependencies | Recipes and the stage/agent prompts are read at run time; it was only a dev or transitive dependency, and the rules forbid relying on transitive ones |
| 2026-10-03 | 16 | `resume` reopens the **same run id**: manifest transitions `interrupted`/`failed`/`cancelled → running` were added (`succeeded` stays final; `manifest.resumes` counts them); usage and budgets are rebuilt from `events.jsonl`, but the wall-clock budget and the `Workspace Changes` baseline restart at the resume | A run that is continued is one run to its report and board; the baseline snapshot holds file text and is not persisted |
| 2026-10-03 | 16 | Resume trust rule: a stage is finished only if its record succeeded/skipped, its contracts are in the state, **and** the workspace fits. The workspace is compared to the stage's end hash only when no later stage started; otherwise the next stage's recorded *start* hash must equal it (a stage that started afterwards legitimately changed files) | The literal "end hash still matches" would redo every completed stage after any interrupted one |
| 2026-10-03 | 16 | `StageRecord` gained `revision_start`, `revision`, `detail`; `RunManifest` gained `resumes` (all optional, schema version unchanged) | Resume needs both hashes per stage and the reason a stage failed or was skipped |
| 2026-10-03 | 16 | Board rules: the controller may reopen `failed`/`cancelled` cards (`→ ready`); `done` stays final. The existing matrix and "terminal cards are final" tests were updated | One card per stage for the whole run, including after a resume; otherwise a failed card could never reach 100% progress |
| 2026-10-03 | 16 | A first SIGINT/SIGTERM is a graceful cancel (run ends `cancelled`, exit 130, resumable); a second Ctrl-C raises `KeyboardInterrupt` and ends the run `interrupted`. Handlers are installed for every strategy (not for `train`/`replay`/`test`) | The plan asks for `cancelled` on a signal; `interrupted` stays for a hard stop and a killed process (a stale `running` manifest is treated as interrupted when the workspace lock is free) |
| 2026-10-03 | 16 | In-stage cancellation relies on tools refusing work once `cancel_event` is set plus controller checks before a stage, while paused, and after an agent returns; no per-step abort | CrewAI swallows exceptions raised from an agent's `step_callback` (verified), so an abort there is not reliable |
| 2026-10-03 | 16 | `single` runs through the pipeline machinery as a built-in one-stage recipe (`build`, teammate `generalist_engineer`, a new entry in `agents.yaml`); `hierarchical` is wrapped unchanged; `spec` and `plan` use `solution_architect` until T22 adds the product analyst | One code path gives the baseline a board card, stage record, resume, and cancel; the agent count of the hierarchical crew (four `@agent`s) is unchanged |
| 2026-10-03 | 16 | Default stage tool table is applied in `pipeline/stages.py`; `browser` is added for frontend/quality/generalist when the extra is installed, `web` is requested always and filtered by settings | Plan item 6; T23 replaces the table with per-teammate groups |
| 2026-10-03 | 16 | The interim artifact guardrail moved to `artifacts.missing_artifacts` (used by `crew.py` and by the pipeline's controller-side check); the pipeline checks files after the agent returns rather than as a CrewAI guardrail | Controller code does not trust the agent, and a guardrail retry inside CrewAI would hide the failure from stage retry and resume |
| 2026-10-03 | 16 | Plan validation (unique ids, known dependencies, no cycles) is minimal and runs when the plan stage ends (a bad plan fails that stage, whose retry carries the problems to the architect); overlap/ownership checks and write scopes stay in T17 | Plan splits plan validation between T16 and T17 only implicitly; work packages run sequentially and unscoped for now |
| 2026-10-03 | 17 | Scheduling is the plan's layering, not a dynamic ready-queue: a layer's packages run together (disjoint ownership) and the next layer starts when the batch finishes | Predictable lanes and reports, as the plan describes; a ready package can wait for the slowest sibling of the previous layer. Revisit if profiling shows it matters |
| 2026-10-03 | 17 | With `max_parallel_agents = 1` packages run sequentially **without** a write scope (as T16 did); with more than one lane every package gets `WriteScope(owned_paths, deny=shared root files)` | "1 reproduces sequential behaviour exactly"; scoping protects against concurrent writers and costs a sequential run flexibility it does not need |
| 2026-10-03 | 17 | Shared files are the **root-level** names in `pipeline/packages.SHARED_FILES` (anchored), so `frontend/package.json` belongs to the package owning `frontend/**`; owning a root shared file (or `**`) makes the plan invalid | A monorepo's sub-project manifests are legitimately owned by one package; only root files are contended |
| 2026-10-03 | 17 | Overlap detection is conservative (a glob is reduced to its anchored directory; unanchored wildcards overlap everything); overlapping packages are serialised, never rejected | A false overlap only costs parallelism; a missed one would let two agents write one file |
| 2026-10-03 | 17 | A parallel stage's `retry` is per work package inside the engine (one stage attempt); the stage fails only if a **required** package (`WorkPackage.required`, default true; new optional contract field) did not succeed, and a missing optional package is reported to `integrate` | Plan 3: "continue independent packages, and let integrate decide (fail the run if a required package is missing)"; failing the stage keeps `resume` redoing only what is missing |
| 2026-10-03 | 17 | New `integrate` stage in the `new` recipe (teammate `backend_engineer` until T23/T24 add an integrator; output `docs/integration.md`; skipped with `implement`) | Plan item 4; no integrator teammate exists yet and T23 owns the registry |
| 2026-10-03 | 17 | `parallel.max_rpm` is one sliding-window `RateLimiter` installed as every stage agent's CrewAI rate controller (`agent.set_rpm_controller`); the hierarchical crew does not use it | CrewAI's own `max_rpm` is per crew and sleeps a whole minute uninterruptibly; the shared limiter is cancel-aware and spans lanes. Provider 429s still rely on CrewAI's retry |
| 2026-10-03 | 17 | A lane's browser session and processes are keyed `teammate#lane` (`ToolEnv.owner`); the board actor stays the teammate. The default `browser.max_contexts` (2) still caps contexts, so a third lane is told to wait | Two same-role agents must not share a session or a port; changing the context default is a settings decision for T21+ |
| 2026-10-03 | 17 | Plan validation (including criteria and ownership) runs in the plan stage's artifact step, so the architect's one repair attempt is the stage retry carrying the problem list | Reuses the retry/resume machinery instead of a second repair loop |
| 2026-10-03 | 18 | `verify` is a new recipe **stage kind** (not a registered controller action); a recipe may have one; it takes no outputs, no `verification_policy` and no `retry` | The loop needs the stage's agent, the board, and the run's budget, which the `(ctx, state)` action signature does not carry; the stage repairs itself, so a stage retry would only rerun the whole loop |
| 2026-10-03 | 18 | Run `status` is unchanged (`verified` → `succeeded`; `failed` and `partial` → `failed`, so both stay resumable); the new `verdict` (`verified|failed|partial`) is in the manifest and `RunResult`, and `main` maps it to exit 3 / 4 | `succeeded` is final in the store, and a partial run must be resumable once the runtime is installed; a second field keeps the lifecycle and the trust verdict apart |
| 2026-10-03 | 18 | The final re-verify is a **gate in the flow** after the last stage (not a second recipe stage): it runs only if the recipe has a `verify` stage that ran, reuses recorded results when the revision is unchanged, and always re-renders `docs/verification.md` | A recipe author cannot forget it, and an agent that overwrote the report in `release` is undone |
| 2026-10-03 | 18 | The verification revision excludes `docs/verification.md`, `docs/qa-notes.md` and `docs/release-report.md` (`runtime/snapshot.workspace_revision(exclude=)`); the report carries no timestamps, durations, run ids or log paths | Writing the report must not stale its own evidence, and `docs/release-report.md` is always rewritten by the last stage; the resume tests require an interrupted run to end with the same tree as an uninterrupted one |
| 2026-10-03 | 18 | Per kind, user checks > plan-declared commands > detected defaults; only detected tests/lint/type-check/build run through `DevRunner` and carry a parsed report, plan and user commands run as plain commands (exit code, log tail, suspect files from `path:line`); a plan command the controller may not run falls back to the detected default with a note; detected **setup** is not run (only a declared one is) | Plan item 1 says reuse T11 and do not reimplement; parsing arbitrary declared commands would mean guessing their format, and reinstalling dependencies is the foundation stage's job and needs the network |
| 2026-10-03 | 18 | A required `tests` check always exists (a project with nothing to run is `failed`, which also asks the repair agent for tests); lint and type check are advisory and the smoke check (the plan's `run` command; still alive after `verify.smoke_seconds` counts as started) is advisory too (`verify.static_required`, `verify.smoke_required` make them required) | An empty project must not look verified; lint noise and a server that needs configuration should not fail a run unless the user says so |
| 2026-10-03 | 18 | User checks may use any program (no shell, no inline code); the file must be outside the project, is copied to `run_dir/checks.yaml` with its sha256 in the state, and every verification refuses to run if the copy changed; a `browser_script` is pinned by hash and runs as `python <script>` or `npx playwright test <script>` (a missing Playwright is `unavailable`) | The file is the user's, so it is trusted more than agent-written plan commands; the pinned copy is only as safe as the run directory, which a command tool can still reach until the Docker sandbox (T20) lands (stated in SAFETY.md) |
| 2026-10-03 | 18 | A criterion is `verified` only by a passing check mapped to it in the checks file; a passing suite whose test files mention the id is `referenced` (a hint); unverified criteria are listed but do not block `verified` | The plan says unmapped criteria are listed as manual/unverified; an agent wrote both the test and the mention, so the mention is not proof |
| 2026-10-03 | 18 | Repair rounds are counted over the whole run (`state.verification.rounds`, checked with `ctx.budget.may_repair`); a repair round that changes nothing reuses the recorded results; a crashed repair attempt uses a round; a resumed `verify` stage starts with fresh rounds; `unavailable` checks are not repaired | Bounded cost; re-running identical checks on an identical tree cannot change the answer; editing code cannot install a runtime |
| 2026-10-03 | 18 | Only the `pipeline` strategy verifies. `hierarchical` and `single` keep the interim size-based artifact guardrail (`artifacts.missing_artifacts`), which the pipeline still uses for its other promised files; `--checks` with another strategy is a usage error | The heuristic is only superseded where the controller now writes the record (the pipeline's verify stage); `single` is the benchmark baseline and T33 decides how to verify it fairly |
| 2026-10-03 | 18 | `CheckSpec.argv` is now optional (a detected check has none) and `CheckResult` gained summary, hint, suspect files, log tail, the parsed report and criteria; a check that passed (`done` card is final) and later regresses gets a new `(re-run)` card; a repair is a `repair` card closed by the controller | Backward compatible (extra fields are ignored by older readers); `done` cannot be reopened by the board rules |
| 2026-10-03 | 18 | CrewAI research step: installed 1.15.23 (as the plan records); only the stage runner's prompt key (`repair`) and one prompt input (`failures`) changed, no new CrewAI API; PyPI and the changelog were not re-fetched | No CrewAI behaviour this task depends on changed |
| 2026-10-03 | 19 | `GitPort` runs every `git` command through `ctx.backend` (fixed argv, `-c` safety config, locked-down environment), not through `subprocess` as the plan's wording suggests | Rule 4 and the existing `test_no_tool_calls_subprocess_directly` forbid spawning outside the backend, and it keeps the Docker sandbox (T20), cancellation, and timeouts covering Git |
| 2026-10-03 | 19 | A repository counts only when the project directory is its top level, and `GIT_CEILING_DIRECTORIES` is set to the project's parent so Git never discovers one above it; a project inside another repository is simply "not a repository" | The plan's "refuse a repo whose toplevel is outside the workspace"; `workspace/<project>` normally sits inside this tool's own checkout, so without it every Git read and commit would hit the wrong repository (it also fixes `Hotspots`/`Find TODOs`, which now use the port) |
| 2026-10-03 | 19 | Diffs, `export_patch`, and `tree_hash` use a temporary index that is a **`copy2`** of the real one (timestamp kept) plus `git add --all`; the real index and refs are never touched | A plain copy gets a new mtime, which defeats Git's "racily clean" rehash rule and made a same-size edit within a second of a commit invisible (found by a flaky tool test; `test_an_edit_right_after_a_checkpoint_is_always_seen` repeats the window) |
| 2026-10-03 | 19 | `workspace_revision` (T16) stays a content hash of non-ignored files; `GitPort.tree_hash()` is the Git tree object of the same project, not a replacement | Resume must work without Git (`--no-git`, git missing, a project that is not ours), so the plan's "shared with T16's revision hash" is met by offering both, not by making resume depend on Git |
| 2026-10-03 | 19 | Controller state and heavy directories are written to `.git/info/exclude` (any repository the port touches, never the project's `.gitignore`); a pathspec exclude was rejected | `git add` errors on an excluded pathspec that is also ignored; `info/exclude` is local, so it never changes what the user's repository tracks |
| 2026-10-03 | 19 | Greenfield history: `Checkpoints` runs `git init` only for a *new* project (nothing but controller state), commits `stage(<name>): <summary>` after each finished stage (empty commits allowed so every stage has one), adds a `final:` commit if the final verification changed anything, and treats any Git failure as a `git.warning` event; only `pipeline` and `single` runs do it (`--no-git`, `ENGINEERING_GIT`, `git.enabled`) | Plan item 2 and "one commit per stage"; the history is a convenience and must not fail a run; the hierarchical crew has no stages, and a project that already has files is T25's business |
| 2026-10-03 | 19 | `git_read` tools are in the default stage groups; `Git Info` `diff` is work tree against HEAD with new files, `Git History Search` doubles as file history and last author | Plan item 3 lists "file history, who-last-touched" under History Search; reading history helps every teammate |
| 2026-10-03 | 19 | Tests make no stage commits unless marked `git` (an autouse fixture patches `Checkpoints.enabled`); an `ENGINEERING_*` variable was rejected because the hermetic-environment test forbids them | Real commits cost several `git` processes per stage and slowed the pipeline tests from ~15 s to ~60 s per file |
| 2026-10-03 | 20 | The workspace is mounted at its **own host path** (`-v <root>:<root>`, `--workdir <cwd>`), not at `/workspace` as the plan says | Paths in arguments, environment variables (`HOME`, `TMPDIR`, cache dirs), Git's `--show-toplevel`, and tool output then mean the same inside and outside, so no path translation exists to get wrong, and an agent can reuse a path from a stack trace in the file tools |
| 2026-10-03 | 20 | `.engineering-team/` is hidden by an empty tmpfs; only `tmp/`, `cache/`, and `tool-home/` (host directories the backend creates) are bind-mounted back. The per-project caches are those bind directories, not named volumes | A named volume is created root-owned and the workload runs as the host uid; a bind directory is owned by the user and already is the cache location `command_environment` points at. Run records, pinned checks (closing T18's caveat), the board, and logs are invisible to commands, while the scratch dir T3 already lets agents use stays shared |
| 2026-10-03 | 20 | The default Python image is `astral/uv:python3.12-bookworm-slim` (Docker Hub) rather than `python:3.12-slim`, and Go is `golang:1.25` rather than 1.23; the other defaults are as the plan lists (`node:22-slim`, `rust:1-slim`, `eclipse-temurin:21-jdk`, `mcr.microsoft.com/dotnet/sdk:9.0`, plus ruby and php). Tags verified against Docker Hub and MCR on 2026-10-03 | The dev tools run Python projects through `uv`, which `python:3.12-slim` lacks (every uv project would be `unavailable`); Go 1.23 is end-of-life and a newer `go.mod` would need a toolchain download with the network off. The image is chosen from the program (`npm` -> Node), then the project's detected stack; `image` and `setup_image` override |
| 2026-10-03 | 20 | The workload's environment travels in a `0600` `--env-file` (removed when the command ends), never as `-e NAME=value` on the command line; the `docker` client itself gets only `PATH`, `HOME`, locale, XDG, and `DOCKER_*` from the host | Values on an argv are visible in the process list, and `-e NAME` would read them from the client's environment, where `HOME`/`PATH` collide with the workload's. The host `PATH` and other host-only names are dropped so the image's own apply |
| 2026-10-03 | 20 | The controller's Git (`GitPort`, T19) runs in the sandbox too, in `execution.docker.git_image` (default `alpine/git`), with `safe.directory=*` set through `GIT_CONFIG_*`; the language images have no Git | Leaving Git on the host would let a sandboxed command plant `.git/config` filter or driver settings that the host's `git add` then executes, which is an escape. `GitPort.available()` now asks the backend (`has_executable`) instead of the host's `PATH` |
| 2026-10-03 | 20 | A command that publishes a port gets the bridge network (so it also has outbound access); network `none` cannot publish ports. Documented in SAFETY; no domain filtering is claimed | Docker ignores `--publish` with `--network none`, and a user-defined internal network cannot publish to the host either; a host-side relay would be a second, larger mechanism than the plan asks for |
| 2026-10-03 | 20 | `engineering-team doctor` does not exist until T21, so T20 ships `docker_status()` (installed? daemon reachable? Linux containers? local daemon?) which already produces the run's failure message; **T21's `doctor` must call it and recommend Docker when it is available** (plan item 5) | Building a CLI command here would pre-empt T21's design |
| 2026-10-03 | 20 | `--sandbox local|docker` is added to the existing argparse CLI (`run` and `resume`); `ExecutionUnavailable` is a `ValueError` (a usage error, exit 2) raised when the run is created, while a command the backend cannot start (image pull failure, bad cwd) raises `ExecutionError(OSError)`, which tools already report as `ERROR:` | Matches how `_execute` and the tool wrapper classify errors; T21 carries the flag into Typer |
| 2026-10-03 | 20 | `Environment Info` under Docker returns the sandbox's description (images, limits, network) instead of probing; `docker.py` joins `local.py` as a module allowed to import `subprocess`, and `docker.py` may read `os.environ` for the docker client's own variables (both guard tests list it) | Probing would start, and first pull, a container per language; the guards exist to keep spawning and environment reads out of tools, which this module is not |
| 2026-10-03 | 20 | The `@pytest.mark.docker` canary tests (`tests/test_docker_integration.py`: outside file, API-key variable, network, hidden controller state, non-root and read-only root, output flood, timeout and cancel cleanup, a published port) pass against a real Docker Desktop 29.6.2; the argv, lifecycle, and wiring also have golden and fake-`docker` tests that need no daemon | Run `uv run pytest -m docker` on other platforms (Linux uid mapping in particular) before relying on the sandbox there; alpine's busybox has no `httpd`, so the port test uses `python:3.12-alpine` |
| 2026-10-03 | audit 1-20 | `pydantic>=2.7,<3` is now a declared runtime dependency | The code imports it directly (settings, contracts, board, devtools) but it only arrived transitively through CrewAI, which rule 'do not rely on transitive ones' forbids; the lockfile gained one line |
| 2026-10-03 | audit 1-20 | `execution.docker.git_image` defaults to the pinned `alpine/git:2.54.0` (tag verified on Docker Hub), not `latest` | The controller's Git runs in it, so an unpinned third-party tag would change under every user; the other default images are language tags that follow a release line |
| 2026-10-03 | audit 1-20 | `RunContext.create` builds the backend before it creates the run directory | A `docker` run with Docker missing exited with a usage error but left an empty `runs/<id>/`; regression test added |
| 2026-10-03 | audit 1-20 | Ten core tools (Read Many Files, File Info, Find Files, Project Outline, Repo Map, Move/Copy Path, Make Directory, Delete Project Path, List/Run Script) had unit tests but no test through `ScriptedLLM`; `tests/test_core_tools_agent.py` adds one | Tool design rules require an agent-level test for every tool; the audit of tasks 1-20 found only these missing |
| 2026-10-03 | 22 | Intake lives in `intake/` (`bundle.py`, `context_docs.py`, `templates.py`); `request_hash` moved there (the web UI must not import the pipeline) and `pipeline/state.py` re-exports it | One definition of the hash that the manifest, resume, and `RequestBundle.hash` share |
| 2026-10-03 | 22 | Sources merge as inline text, then `--request-file`s in order (`-` is stdin), each under `## Request: <name>`; one source is used unheaded; `--example` stands alone (combining it is a usage error). This replaces the 0.2.0-dev precedence `--request` > `--example` > `--request-file`, where `--request` silently dropped a file | The done-when needs the same text to hash the same from a file, stdin and the API, so a single source must add no header; silently ignoring a supplied file was a trap |
| 2026-10-03 | 22 | `Spec` gained `confidence`, `blocking_questions`, `clarifications` (additive, `schema_version` stays 1); `open_questions` keep meaning "non-blocking" | The analyst must say what would change the product; unknown fields are ignored by older readers |
| 2026-10-03 | 22 | The controller writes `docs/spec.md` from the validated contract (the stage promises `file:docs/spec.md` with `verification_policy: artifacts`); the analyst is read-only (`READ_ONLY_TEAMMATES` in `pipeline/stages.py`, until T23's registry assigns groups) | The file always matches the contract the later stages read, and an agent cannot leave different words in it |
| 2026-10-03 | 22 | Every stage agent got the `knowledge` group (Search Docs); `Search Docs` adds `.engineering-team/context/` to its corpus when it exists. `knowledge_tools` imports `intake` lazily (intake imports the tools: a cycle otherwise) | `.engineering-team/` is closed to the file tools, so Search Docs is how agents read `--context-dir` documents; only the suffixes Search Docs indexes are copied, since agents could not read the rest |
| 2026-10-03 | 22 | An invalid spec (`spec_problems`: ids must be `AC-n` and unique, a title, at least one criterion) raises `SpecError`, and the stage's `retry: 1` is the one repair; its note omits the workspace-resume boilerplate | Reuses the existing retry instead of a second mechanism; a spec has no workspace state to inspect |
| 2026-10-03 | 22 | Clarification is one round, controller-driven: it asks through `HumanChannel` directly (not through the Ask Human tool, which stays available to agents) up to `intake.max_questions`, then re-runs the analyst once with the answers; a failed re-run keeps the first spec and records the answers; unanswered or unasked questions become assumptions plus open questions. Event names stay `question` / `question.answered` / `question.unanswered` (T10's; the plan said `answer`), plus `spec.assumed`, `spec.clarified`, `spec.revise_failed` | The clarification policy (cap, timeout, what to record) is the controller's, and a failed model call must not make it ask twice |
| 2026-10-03 | 22 | `HumanChannel.skip()` added: an empty reply at the terminal declines a question, the asker gets `None` at once and the outcome is `skipped` | Without it a declined question waited out its timeout |
| 2026-10-03 | 22 | `--interactive` is opt-in and ignored (with a notice) when stdin is not a terminal or carries the request; the answerer (`cli/ask.py`) pauses the live screen while it prompts. No `intake.interactive` setting and no `intake.*` environment variables | Prompting from a script would hang; a config-file default for prompting is easy to add if asked |
| 2026-10-03 | 22 | `--context-dir` copies are kept until a later `--context-dir` replaces them (or `--reset`); a run without the option reuses them | A resumed run must keep reading the same documents; the INDEX is regenerated on each install |
| 2026-10-03 | 22 | AGENTS.md research steps: installed CrewAI 1.15.23 equals PyPI latest; the changelog (latest v1.15.23, no breaking change to agents or `output_pydantic`) was read; the agents docs page is client-rendered and could not be fetched, but the change uses no new CrewAI API (a prompt, a YAML agent, three optional fields on an existing `output_pydantic` contract) | Recorded because the protocol asks for the check |
| 2026-10-03 | 23 | Built-in teammates stay in `config/agents.yaml`, now with `tier`, `tool_groups`, `allow_delegation`; there is no `config/team/*.yaml` | The task asked both for `config/team/*.yaml` and for no second copy of the built-ins; one file is the only way to have one copy |
| 2026-10-03 | 23 | Project overrides: `.engineering-team/team.yaml` is read from the current directory (or `team_file`), beside `engineering-team.toml`, not from the workspace; precedence is built-in < team file < `[team.<key>]` (field by field). `team_file` has no environment variable | The workspace is generated and its `.engineering-team/` is closed to agents; a config next to the project config is discoverable and tool-proof |
| 2026-10-03 | 23 | Default tool groups deviate from the task's table: the architect and quality engineer keep `fs_write` (they write `docs/architecture.md`, tests, and the release report, which the stages require), and every teammate keeps `board`, `notes`, `human`, `knowledge`, `git_read` and `web`; the analyst is read-only | The recipe's promised files are written by those agents, and the table as written would have failed the `plan` and `release` stages |
| 2026-10-03 | 23 | `tier` picks a model tier only under the `standard` profile (a `lead` tier always uses the profile's lead slot); `smoke` and `max-quality` fix the models of their two slots. Changing a model is `[models.roles.<key>]`, not a `model` field of `[team.<key>]`; `models.roles` still beats tier and `max_iter` | Otherwise a `tier = "reviewer"` teammate would defeat the smoke profile's cost cap, and one mechanism for pinning a model is enough |
| 2026-10-03 | 23 | `mcp:docs` is the only `mcp:<name>` group: it attaches `docs_mcp_urls`, as every agent got before; other names are rejected. `allow_delegation` is honoured by the `hierarchical` manager only (a stage crew has one agent) | There are no other named MCP servers in settings to refer to |
| 2026-10-03 | 23 | A custom teammate gets work packages only when it lists the stage under `stages`; a new teammate with no `tool_groups` is read-only; `modes` is enforced by `Roster.assign` once a recipe names a mode (recipes do not yet, so it is not applied today) | Opt-in keeps a typo or a half-defined teammate from receiving writes; the mode filter is in place for T25/T26 |
| 2026-10-03 | 23 | Fallback order: next teammate listed, `generalist_engineer`, then the enabled teammate with the most tool groups in common with the missing one (ties by key); nobody who can write and run commands is a `TeamError` before the stage starts. `RunContext.team` builds the roster, lazily imported (the registry imports the tools, which import the runtime) | Deterministic and explained by a `team.fallback` event; avoids an import cycle |
| 2026-10-03 | 24 | `review` is a stage kind that runs its teammates through `run_parallel_readonly`; each reviewer returns a `ReviewReport` (structured output) and the controller, not an agent, validates, dedupes, numbers, and writes `docs/review.md` (excluded from the verification revision like the other controller reports) | Reviewers cannot be trusted with the report or the ids, and a controller-written file keeps the stage deterministic whichever reviewer finishes first |
| 2026-10-03 | 24 | `Finding.id` is now optional (reviewers leave it empty; the controller numbers `F-n`) and `source_role` is set by the controller; duplicates (same file, lines within 3, at least half the words in common) merge to the higher severity naming both roles | Reviewers overlap by design (a security flaw is also a correctness flaw); one finding means one repair instruction |
| 2026-10-03 | 24 | `readonly_tools` now builds the read-only tools of the teammate's own roster groups (every group for a teammate the roster does not know) and adds `Dependency Audit`, the only command-running tool that changes no file, when the teammate has `dev` | The security reviewer must run the audit, which is not "read-only" in the catalogue's sense (it runs a vetted command) |
| 2026-10-03 | 24 | Findings at or above `review.fail_on` get one repair round from the shared `budget.max_repair_rounds` (`VerificationLoop.repair_findings`, prompt `repair_review`) and the verify stage runs again; they are not reviewed again, and a review finding never changes the verdict; none left means reported, not fixed. The repair card closes with the passing checks as evidence | The plan says "fed to the repair loop (bounded), then re-verified"; a second review round would double the cost and only the checks are evidence |
| 2026-10-03 | 24 | The verify stage's repair agent is now the `debugger` (was the quality engineer), for failing checks and for review findings | The plan assigns the repair loop to the debugger |
| 2026-10-03 | 24 | `team.profile = minimal` is `team_profile = "minimal"` (env `ENGINEERING_TEAM_PROFILE`), a top-level setting: `[team]` already holds the per-teammate tables, so `team.profile` would be read as a teammate called `profile`. Optional stages (`optional: true`, `skip_if: [minimal_team]`) are also skipped when none of their teammates is enabled; conditions now take `(state, settings)` | Avoids a key clash; "can be disabled" is satisfied by disabling the teammates |
| 2026-10-03 | 24 | The recipe order is verify, review, devops, docs, release; `devops` promises `docs/devops.md` and `docs` promises `docs/usage.md` (the artifact check needs a file a stage always writes); the technical writer also updates the README and changelog. One failing reviewer is reported in `docs/review.md`; all failing fails the stage with no retry | A Dockerfile or CI file is not needed by every project, so it cannot be the promised artifact; a retry would pay for the reviewers again |
| 2026-10-03 | 25 | `analyze` is a new stage kind (`analyze`), not a controller action; the controller (not an agent) renders `codebase-map.md` from a `ChunkAnalysis` per chunk and one `CodebaseMap` synthesis | It needs the stage runner and `run_parallel_readonly`, which the `(ctx, state)` action signature does not carry; a controller-written file has fixed structure and size, and agent lines are flattened and capped |
| 2026-10-03 | 25 | `adopt.yaml` is `profile` (controller), `baseline` (controller), `map` (analyze); `analyze --deep` runs the read-only subset `analysis_recipe()` (profile + map, no baseline) through `PipelineStrategy`; a recipe cannot include another, so T26-T28 inline or reuse the stages | The baseline runs the project's own commands and `analyze` is read-only; composition (an `include`) is T26's call once there is a second user |
| 2026-10-03 | 25 | Isolation is `modes/isolation.isolate()` (API + tests); **no CLI flags yet** (`--worktree`, `--allow-dirty`, `--init-git`): they land with the first repository mode (T26). `auto` = branch for a clean repository, worktree for a dirty one; `allow_dirty` turns `auto` into in-place; only an explicit `branch` on a dirty tree is refused | The plan's "worktree is the default when dirty" and "dirty without worktree is refused unless --allow-dirty" only fit together this way; no command exists yet to attach the flags to, and `analyze` is read-only |
| 2026-10-03 | 25 | Copy mode copies everything except `.engineering-team/`, `__pycache__`, tool caches and `.venv` (symlinks kept as links), refuses over 2 GiB, never overwrites, and `--init-git` initialises the **copy** (first commit = the import), never the original | A virtual environment holds absolute paths and does not survive a copy; `node_modules` is kept so `npm test` can run; the original must stay byte-identical |
| 2026-10-03 | 25 | `new --adopt` works in place on an existing non-owned directory under the workspace root, is refused for a Git repository, marks it `"adopted": true` in `owner.json`, and `--reset` then needs `--force-reset` | In-place adoption of a repository would make stage checkpoints commit onto the user's current branch; isolation is the safe door for repositories |
| 2026-10-03 | 25 | `GitPort` gained `state_dir` (git's home, caches and temp go elsewhere), `exclude_controller_state()`, `git_dir()`/`common_dir()` (linked-worktree aware `info/exclude` and index), and `engineering_team.git` imports `engineering_team.tools` first; `command_environment(workspace, state_dir)` takes the same option | Any command through the port created `.engineering-team/{tool-home,cache,tmp}` in the project, which broke "`analyze` writes nothing"; a worktree's `.git` is a file; importing `git.port` first hit the codeintel import cycle (it failed on a clean checkout) |
| 2026-10-03 | 25 | The baseline reuses `build_checks` + `Verifier` (new `label` argument so its batch is `verification/baseline.json`, not a round); a test command that ran and found no tests is `skipped` (no test baseline), not failed; known failures are keys `test:<dir>:<id>`, `lint|typecheck:<dir>:<file>:<rule>` (no line numbers), `build:<dir>`; nothing is installed; **comparing against it ("no new failures") is left to T26's verify** | The plan says T18's verifier compares, but T18 shipped before a baseline existed; the comparison belongs where a brownfield recipe first needs it |
| 2026-10-03 | 25 | The map's cache key is `workspace_revision` (content hash of non-ignored files, first line of the file), not `GitPort.tree_hash`; `map_context(root, cap)` is injected into the `{context}` prompt input for every stage except `analyze`; new settings `analysis.max_chunks` (6) and `analysis.context_chars` (8000) | Works without Git (copy mode, plain directories); the analysts must not read the map they are replacing |
| 2026-10-03 | 25 | Analysts' only writable path is `.engineering-team/tmp/analysis/<chunk>.md` (the agent scratch area `run_parallel_readonly` scopes their write tools to), so no source file is reachable; `codebase_analyst` has no `human` or `web` group | `ReadOnlyJob` requires a report path; scratch is the one place the file tools allow under `.engineering-team/` |
| 2026-10-03 | 25 | `analyze --deep` runs inside the project (mode `analyze`, run record in its `.engineering-team/runs/`, no ownership marker, `git.enabled = false`, workspace lock held); `engineering-team runs` does not list it because it looks under the workspace root | No stage commits may land in the user's history, and the project is not a generated workspace |
| 2026-10-03 | 25 | AGENTS.md research: installed CrewAI 1.15.23 equals PyPI latest; the changelog (v1.15.23, 2026-09-28) shows no breaking change to agents, tasks, `output_pydantic` or tools; the docs concept pages were not re-read because no new CrewAI API is used (two more prompts and two more `output_pydantic` contracts on the existing stage runner) | Recorded because the protocol asks for the check |
| 2026-10-03 | 26 | `feature.yaml` inlines the adopt stages (profile, baseline, map) instead of an `adopt` stage, and adds two stage options: `prompt` (a `stages.yaml` key replacing the kind/name default) and `allow_shared` (a parallel stage's packages may own shared root files) | A recipe cannot include another yet; a change to an existing project has no foundation stage to own `package.json` and friends, so the plan check and the write scope's `deny` had to follow the recipe |
| 2026-10-03 | 26 | The controller's reports (`spec.md`, `verification.md`, `review.md`, `qa-notes.md`) go to `<run dir>/reports/` for adopted workspaces through `RunContext.reports` (`RunContext.create(adopted=True)`); a new project keeps `docs/` | "Minimal diff": the plan's own recipe would otherwise add four controller-written files to the user's repository |
| 2026-10-03 | 26 | Baseline comparison happens in the verification loop: a failed detected check whose failure keys are all in the baseline is recorded `passed` with `known_failures` (raw results stay in `verification/round-<n>.json`), a mixed outcome stays failed with `new_failures`, and only detected checks are compared; with a baseline the plan's declared commands are ignored (`build_checks(plan=None)`) | Judging, repair, the board, and the report then need no special case; user checks and plan commands have no baseline key; the architect must not swap the project's own commands for its own |
| 2026-10-03 | 26 | Diff noise is informational (review report, summary, `review.diff_noise` event), not a finding or a failure; scope = plan owned paths + test directories + test-named files, measured from `GitPort.changes(base)` (a temporary-index `diff --raw --numstat --no-renames`) | The plan asks for a metric, not a gate; a finding at the repair threshold would send agents to "fix" noise by touching more files |
| 2026-10-03 | 26 | `feature` always makes a copy of a non-Git directory a repository (no `--init-git` flag on it), and has no `--no-git` | The patch, `diff`, and the noise measure all need a starting commit |
| 2026-10-03 | 26 | `CHANGE_SUMMARY.md` and `changes.patch` are written by the `change_summary` controller action (no agent), then `--squash`/`git.squash` is done by `Checkpoints.final` after the last stage | A stage commit is made after every stage, including `summary`, so squashing inside the stage would leave one more commit behind it |
| 2026-10-03 | 26 | Runs that live outside the workspace root (a branch or worktree of the user's repository) are listed in `<workspace root>/.external-workspaces.json` (`register_workspace`), which `find_runs` reads; `resume` takes the run's workspace from there, and a changed request on a non-build run is a usage error | `status`, `board`, `cancel`, `note`, `diff` and `resume` all locate runs by id under the workspace root |
| 2026-10-03 | 26 | New teammates and recipes: none; the `feature` stages use the existing teammates with five new prompts (`spec_change`, `impact`, `implement_change`, `tests`, `review_change`); reviewers are given the starting commit as `{base}` and read the change with `Git Diff Between Refs` | The roster already covers the roles; reviewers need the base because stage commits move HEAD |
| 2026-10-03 | 27 | `fix.yaml` has one new stage kind, `reproduce` (agent attempts, then the controller's red gate; retry budget `fix.max_repro_attempts`, no `retry:`), and three new contracts (`triage`, `repro`, `fix_note`) added to the recipe's contract names; the controller's own facts are `PipelineState.fix` (`FixRecord`), kept apart from the agents' accounts | A loop with a controller verdict between attempts does not fit the `agent` kind; keeping the agents' words and the controller's runs in different fields keeps "never trust agent text as evidence" visible in the types |
| 2026-10-03 | 27 | "Write-scoped" fix = every agent call after red is denied the reproduction's files (`WriteScope(allow=("**",), deny=files)`) and the files are digest-pinned; a changed or deleted file fails the verdict (`tampered`) | Scoping the fix to the triage suspects would block legitimate fixes in a file triage missed; protecting the evidence is what makes red→green mean something, and the digest catches an edit made through a command instead of the file tools |
| 2026-10-03 | 27 | Red = the controller's run of the agent's command failed for a real reason: not a pass, not "could not run", not a timeout, not exit 126/127, and for pytest not exit 2, 4 or 5. The agent's command runs as a `plan`-sourced check (allowlist applies); the person's `--repro` as a `user`-sourced one, and is required green only if it failed first | A test that cannot be collected or a typo'd command fails too, but proves nothing about the bug; an agent must not widen the command allowlist by writing the command |
| 2026-10-03 | 27 | `needs-info` is a new `RunVerdict` (manifest and `RunResult`; `VerificationRecord.verdict` is unchanged) on a run whose status is `failed`; `exit_code_for` maps it to 4 (the plan's number; it shares it with `partial`), `summary.build` adds `questions`, and `next_steps` no longer offers `resume` for it | The plan asks for exit 4 and questions; reusing `partial` would say "a check could not run", which is not what happened. Resuming replays the same request, so a new `fix` with the answers is the useful next step (`resume` still works if the run failed for another reason) |
| 2026-10-03 | 27 | The person's `--repro`, the whole trace and `--allow-unreproduced` go to `<run dir>/fix-input.json` (written once by the CLI after the run opens), and the report, trace excerpt and command are also composed into `request.md`; no new `RunBundle` field | A resumed run is rebuilt from `request.md` only, and agents must read the trace; the structured copy is what the controller trusts and what survives resume |
| 2026-10-03 | 27 | `feature` and `fix` share `cli/repo_mode.run_repository_mode` (isolation, run opening, display, exit code); `feature_command.feature` is now a thin caller. `fix` resumes (`RESUMABLE_MODES` = build, feature, fix) | Two copies of 120 lines would drift; the existing feature tests cover the move |
| 2026-10-03 | 27 | `fix` adds no teammate and no new tools: the `debugger` (all tool groups but `web`) triages, reproduces, fixes and repairs; the review stage uses a new prompt `review_fix`, the verify stage the prompt `repair_fix` (its review-findings repair keeps `repair_review`) | The roster already has the role; the repair prompt must say that the reproduction passing is part of "fixed" |
| 2026-10-03 | 27 | AGENTS.md research: no new CrewAI API is used (three more `output_pydantic` contracts and five prompts on the existing stage runner), so the installed version, PyPI and the changelog were not re-checked beyond the T25/T26 entries | Recorded because the protocol asks for the check |
| 2026-10-04 | 28 | Maintenance tasks are recipes plus two recipe-level concepts: `policies` (names from `modes/policies.py`, evaluated by the verify stage against `GitPort.changes(base)` and recorded as required `policy:<name>` check results) and `write_scope` (named path sets: tests, docs, manifests, applied by `StageExecutor._call`). A violation is repaired like a failing check | Policies as checks reuse the repair loop, the report and the board; the scope stops the honest mistake, the policy catches a command that edits a file |
| 2026-10-04 | 28 | `docs` may change documentation files only, not comments or docstrings; `upgrade-deps` may change manifests and lockfiles only (an upgrade that needs code changes is reported as not upgradable, with the failing check) | Whether a code diff is comments-only cannot be verified; the plan says "report what could not be upgraded and why" |
| 2026-10-04 | 28 | `refactor` requires the baseline's tests to be green and otherwise ends `needs-info` (exit 4) with next steps; its diff limit is `maintain.max_refactor_lines` (800) | The plan says "tests green before and after"; a project whose tests fail has no evidence of preserved behaviour |
| 2026-10-04 | 28 | `upgrade-deps`: a new stage kind `upgrade`; the analyst plans (contract `upgrades`), the agent edits manifests per group, the controller installs (`DevRunner.install`), runs the checks without repair (`verification/quick`), commits or undoes with the new `GitPort.restore`, and bisects (`bisect_upgrades`, group size `maintain.upgrade_group_size`). It refuses to start when `tools.dev.allow_network` is false | Without an install the checks would test the old versions and every upgrade would look safe |
| 2026-10-04 | 28 | `security-audit` exits 3 on a finding at `review.fail_on` when it was not asked to fix (a gate), like `review`; with `--fix` the verify verdict decides. `--fix` is persisted in `run-options.json` and read into `PipelineState.options` so a resume keeps it | The plan asks for a findings report usable in CI only for `review`; the same contract is the useful one for the audit |
| 2026-10-04 | 28 | `review` runs in place like `analyze --deep` (no isolation, `git.enabled = false`, reports in the run directory) with the `review` recipe, is not resumable, and is registered in the run index so `status` finds it; a bad `--base` or a non-repository is a usage error (exit 2) before the run opens | It must not touch the project it reviews and should be cheap to run in CI |
| 2026-10-04 | 28 | User recipes: lookup is project, then user, then bundled; the file name must equal `name:`; stages can carry `instructions` (the new `custom` prompt) so no Python or prompt file is needed; a run pins its recipe in `<run dir>/recipe.yaml`; the recipe is validated before isolation. `user_config_directory()` lives in `settings.py` (the only module that reads the environment) | A recipe cannot add a prompt otherwise; worktrees and copies do not hold `.engineering-team/`, so resume needs the pinned copy |
| 2026-10-04 | 28 | The `Verdict` literal is unchanged; `needs-info` reuses T27's `RunVerdict`. `NeedsInfo` moved to `modes/needs_info.py`. The legacy recipe-by-path test now gives its stage `instructions` because a stage without a prompt is rejected at load | Shared by fix and refactor; a stage that would fail at run time is refused at load |
| 2026-10-04 | 28 | AGENTS.md research: no new CrewAI API is used (one more `output_pydantic` contract, `upgrades`, and about fifteen prompts on the existing stage runner), so the installed version, PyPI and the changelog were not re-checked beyond the T25-T27 entries | Recorded because the protocol asks for the check |
| 2026-10-04 | 29 | Plain Python templating with `html.escape` instead of Jinja2; the HTML carries a restrictive `Content-Security-Policy`; the report is written at the end of `RunRecorder._finish` (not `execute_run`) and reads files only | Jinja2 is only a transitive dependency, and escaping by construction is simpler to prove; the CSP is defence in depth if escaping ever failed; `_finish` is where every mode, `train`, and `test` end a run, after the manifest is final; reading files keeps `report` usable on runs of other processes |
| 2026-10-04 | 29 | No timestamp in the report (it shows the run's own start/end); screenshots are embedded up to 1 MB each / 3 MB total, the rest listed; a build run has no diff section (only repository modes write `changes.patch`) | Deterministic output for the same run directory; bounded page size; `changes.patch` is what the diff viewer reads |
| 2026-10-04 | 30 | Per-run options reach the run's process as CLI flags where the CLI has them, and otherwise as one new environment variable, `ENGINEERING_OVERRIDES` (a JSON object of dotted settings, between the other `ENGINEERING_*` variables and the command line); the server validates them with `load_settings` before it starts anything | Budget, parallelism, and roster toggles have no CLI flags, and generated per-run config files would replace the user's `engineering-team.toml`; one generic layer is smaller and validated like every other setting |
| 2026-10-04 | 30 | The run is named by two hidden global options, `--run-id` (reserves the id `new_run_id()` returns once) and `--answers-via-inbox` (enables the run's `HumanChannel` with nobody at a terminal); an `answer` inbox command carries replies | The plan's `engineering-team … --run-id` without touching every command's signature; questions need a cross-process channel and the inbox is the existing one |
| 2026-10-04 | 30 | `board.*` card events now carry the whole card (`data.card`); `GET .../board?at=SEQ` folds them (progress measured at the event's time) | The plan asks SSE to carry full card payloads and a deterministic replay; one source serves both |
| 2026-10-04 | 30 | `fastapi` and `python-multipart` are in the `ui` extra and, with `httpx` (TestClient), in the `dev` group; `uvicorn` only in the extra (already locked via chromadb) | The gate (`uv sync --locked --group dev`) must run the UI tests; the wheel and `engineering-team --help` need none of them (the `ui` command imports them lazily) |
| 2026-10-04 | 30 | Tests start runs through a stand-in command (`tests/stub_run.py`) that takes the CLI's arguments and uses the real run store, events, board, lock, cancel flag, and inbox; live streaming is tested over a real localhost socket because `TestClient` buffers a streamed response | A model-free subprocess run is the only way to test cancel, resume, steering, and concurrency end to end offline |
| 2026-10-04 | 30 | A card's tool-call trail is the assignee's `tool.call` events while the card was in progress (in its lane when it has one), since events do not name the card | Adding a card id to every tool event would touch every tool; the window is exact for one card per teammate at a time and says so in the response |
| 2026-10-04 | 30 | Not in this task: a front end (T31), a `GET /examples`, uploads other than text files, and TLS (the docs say to use a tunnel) | Out of the task's scope |
