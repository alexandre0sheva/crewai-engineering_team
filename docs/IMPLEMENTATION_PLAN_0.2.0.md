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
| 5 | `RunContext`, per-run tools, workspace lock (remove globals) | O | 4 | B | todo |
| 6 | Offline test infrastructure (FakeLLM) | S | 5 | B | todo |
| 7 | Core toolbelt: tool catalogue, search/read/patch/outline/repo-map, `ExecutionBackend` (local) | S | 5 | B | todo |
| 8 | Contracts, run store, event log | O | 5 | B | todo |
| 9 | Usage accounting, price table, budgets | S | 8 | B | todo |
| 10 | Task board (kanban model) + coordination tools: board, notes, ask-human, progress | O | 7,8 | B | todo |
| 11 | Developer tools: structured test/lint/typecheck/build/format/coverage runners | S | 7,8 | B | todo |
| 12 | Runtime tools: background processes, ports, HTTP client, SQLite inspect, environment info | S | 7 | B | todo |
| 13 | Code intelligence tools: symbols, references, import graph, hotspots, dependency inspect | S | 7 | B | todo |
| 14 | Web & knowledge tools: search, fetch, package info, local doc search (opt-in, SSRF-safe) | S | 4,7 | B | todo |
| 15 | Browser tools: headless Chromium snapshot/screenshot/click for UI verification | S | 12 | B | todo |
| 16 | Flow pipeline, recipes, strategies, resume | O | 6,7,8,9,10 | B | todo |
| 17 | Parallel execution engine | O | 16 | B | todo |
| 18 | Independent verification + bounded repair | O | 7,11,16 | B | todo |
| 19 | Git integration (`GitPort`) + Git Info tool | S | 5 | B | todo |
| 20 | Docker execution backend | O | 7,12,18 | B | todo |
| 21 | CLI v2 (Typer/Rich), live kanban, `doctor`, `init`, runs | S | 10,16,18 | C Product | todo |
| 22 | Requirements intake + Product Analyst | S | 21 | C | todo |
| 23 | Team registry, custom teammates, tool groups per teammate | S | 10,16 | C | todo |
| 24 | New teammates + reviewer fan-out | S | 17,18,23 | C | todo |
| 25 | Adopt existing project + codebase analysis (`analyze`) | O | 13,17,19,22,24 | C | todo |
| 26 | Feature mode | S | 25 | C | todo |
| 27 | Fix mode | S | 25 | C | todo |
| 28 | Maintain + review modes, user recipes | S | 25,26,27 | C | todo |
| 29 | Run report (HTML/Markdown) | S | 10,18,21 | D UX | todo |
| 30 | Web UI — API server (runs, SSE, board, steering) | S | 10,21,22,29 | D | todo |
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

