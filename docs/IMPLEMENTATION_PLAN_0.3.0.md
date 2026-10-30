# Implementation plan — version 0.3.0

**Goal of 0.3.0:** one command for any engineering task. You describe what you want (a bug, a
feature, a question about the code, a review, a refactor, a new project, a dependency upgrade) and
the team works out what kind of work it is, how big and risky it is, which stages and teammates it
needs, and how to prove it is done. It starts cheap and escalates only when the evidence says it
must. It **learns**: per-domain harnesses and skills replace the static backstories, verified facts
about each repository are remembered, and lessons from verified runs feed later runs. It is
**measured** against Claude Code, Codex and other agents on a harder benchmark, and it is
**shipped** (PyPI, GitHub Action, MCP server, a public GitHub Pages site with a live leaderboard).

Written 2026-10-28 against `main` @ `31bd355` (0.2.0 plus fixes; not tagged yet). Installed CrewAI
1.15.23, latest on PyPI 1.15.31 (`requires_python <3.14`).

## Quick start (read this first — also for a fresh Claude Code chat)

This file is the single implementation plan for 0.3.0. When the user says *"implement task N"*
(in any phrasing: "do task N", "task N", "next task"):

1. Run `uv run python scripts/plan_task.py show N` — it prints the **rules for every task**
   (§4), the task block, and the status of its dependencies. For "next task", run
   `uv run python scripts/plan_task.py next`.
2. Stop if a dependency is not `done` in the index (§5) and tell the user.
3. Implement exactly that task, then run `uv run python scripts/plan_task.py done N` and report as
   described in §4.

`uv run python scripts/plan_task.py list` shows the index with statuses. Each task is sized for one
session. **Nothing is committed or pushed by the tasks** — the user reviews and commits manually.

The 0.2.0 plan (36 tasks, all done) was removed from the tree to avoid two live plans; its items
that were deferred are folded into the tasks below (§1, *Carried over from 0.2.0*). Its decision
log is in Git history:
`git show 31bd355:docs/IMPLEMENTATION_PLAN_0.2.0.md`.

Contents: [1. Current version](#1-current-version-020) · [2. Target version](#2-target-version-030) ·
[3. Target architecture](#3-target-architecture) · [4. Rules for every task](#4-rules-for-every-task) ·
[5. Task index](#5-task-index) · [6. Tasks](#6-tasks) · [7. Decision log](#7-decision-log)

---

## 1. Current version (0.2.0)

### What it is

A CrewAI-based engineering team of 13 teammates behind a deterministic controller: a Flow pipeline
driven by recipes (`new`, `feature`, `fix`, `maintain` × 5 kinds, `review`, `analyze`, user recipes),
parallel work packages with path ownership, controller-run verification with bounded repair, a
controller-owned task board, ~70 structured tools in 13 groups, a Docker sandbox, resume/cancel,
budgets, HTML reports, a FastAPI web UI with a live dashboard, extensions (MCP, plugins, hooks,
conventions), and a benchmark harness with hidden checks. About 51,500 lines in `src/`, 131 test
files (26,000 lines), offline and hermetic.

### What works well (keep)

- **The controller decides.** Agents report; the controller runs the checks, owns the board and the
  verdict. This is the project's strongest idea and its main difference from chat-style agents.
- Typed contracts between stages, on-disk state, resume by tree hash, deterministic records.
- Tool design: structured, bounded results; one `ExecutionBackend`; SSRF-safe web; untrusted-content
  handling; no shell.
- Honest documentation and benchmarks (every failure explained, caveats stated).
- Offline test infrastructure (`ScriptedLLM`, scripted demo).

### Findings (verified by reading the code and results, 2026-10-28)

| # | Finding | Evidence | Consequence | Fixed in |
|---|---------|----------|-------------|----------|
| G1 | The team is slow and expensive, with no measured benefit, and it is the default | `docs/BENCHMARKS.md#results-2026-10-04`: `single` 9/10 at $0.0054 per success, 1.2 min; `pipeline` 10/10 at $0.209, 10.4 min. Medians of the live run records: `pipeline` 117 model calls, 165 tool calls, 1.71M prompt tokens, 11 tool failures, 548 s; `single` 14, 13, 138k, 1, 66 s. 98 % of the pipeline's tokens are re-sent prompt | Users pay 25–39× more and wait 9× longer on small tasks. Cause: ~10 stage agents each start cold, re-read the workspace and re-send tool schemas, strictly one after another; ceremony stages run whatever the task | T5, T6, T12, T13, T25, T26, T33 |
| G2 | The benchmark cannot separate strategies | Small Python programs only, 10 runs per strategy, one provider, intervals overlap; no external agents | No evidence for "better than X"; defaults chosen on noise | T7, T8, T9 |
| G3 | The user must choose the mode | 6 run commands, 5 maintain kinds, 11 recipes, each with its own options (`cli/*_command.py`) | A wrong choice gives a wrong pipeline; no path for "answer a question", "research", "plan only" | T10–T15 |
| G4 | Prompts are static and generic | `config/agents.yaml` backstories are the same for every stack and task; `config/stages.yaml` is 1,148 lines of prompts | No domain knowledge (pytest vs Jest vs Go), no on-demand skills, always-on text paid on every call | T17, T18 |
| G5 | Nothing is learned across runs | No memory: every run re-profiles, re-discovers commands and quirks; lessons from failures are lost | Repeated cost and repeated mistakes on the same repository | T19, T20, T21 |
| G6 | The plan validator rejects instead of repairing | 3 of the benchmark failures: the architect gave `README.md` to a work package and the plan was rejected twice | Whole runs fail on a fixable ownership detail | T3 |
| G7 | One attempt only | One implementation, then bounded repair rounds | No test-time scaling; the cheapest proven way to raise pass rates is unused | T23 |
| G8 | Acceptance is weak without a checks file | Criteria are "unverified" or "referenced" unless the user writes checks; tests are written by the team that implements | Self-graded work; a test that never failed proves little | T22 |
| G9 | Trajectories cannot be read | `events.jsonl` carries tool name, args, duration, ok, but not output (0.2.0 decision log, T36) | Failures cannot be analysed or learned from | T4 |
| G10 | The 0.1.0 surface is still carried | `hierarchical` (0/3, all timeouts), `engineering_lead`, `config/tasks.yaml`, bare `--request-file`, `train`/`replay`/`test` | Code, docs and tests for a path nobody should use | T1 |
| G11 | Modules over the 400-line rule | `settings.py` 1,127 · `main.py` 740 · `tools/registry.py` 733 · `git/port.py` 620 · `pipeline/executor.py` 570 · `report/html_sections.py` 540 | Harder to change and to review | T2 |
| G12 | Local models are a dead end without a hint | Ollama/LM Studio runs answered in plain text with no tool call; zero-price override still reports unknown cost | Confusing failures; wrong cost | T3, T25 |
| G13 | Not shipped | No `v0.1.0`/`v0.2.0` tags, not on PyPI, no recording of a live run, example reports are scripted, `pyproject` description still says "MVP applications" | Weak as a portfolio piece; hard to install | T1, T32, T34 |
| G15 | Model presets are stale and miss the best-value options | `model_routing.py` says "Anthropic has no model cheaper than Sonnet" (Haiku 5.5 exists); no `gpt-6.1-terra`; no OpenRouter provider (DeepSeek, GLM, Kimi, Qwen…); `pricing.toml` checked 2026-10-02 | Overpaying per tier; open-weight models out of reach | T25 |
| G14 | Not where developers work | No GitHub issue intake or PR output, no CI action, no MCP server for other agents, no follow-up on a finished run | Each run is a one-off outside the developer's workflow | T16, T28, T29, T30 |

### Carried over from 0.2.0

| Item | Source | Now in |
|------|--------|--------|
| LSP diagnostics bridge (deferred) | 0.2.0 T16, TOOLS.md "Not included" | T27 |
| Roster `modes` filter defined but not applied (recipes name no mode) | 0.2.0 T27 decision | T3 (and used by T11) |
| Recording of a live run (`docs/assets/demo.*`) left to the user | 0.2.0 T40 decision | T32 |
| Anthropic and Google presets never measured | BENCHMARKS.md | T33 |
| Zero-price override reports unknown cost for local models, not investigated | BENCHMARKS.md | T3 |
| CrewAI 1.15.23 → 1.15.31, and every other dependency | PyPI | T1 |
| `--request-file` compatibility "until at least 0.3.0" | 0.2.0 global constraints | T1 (removed) |

### What Harness² brings (google-research/harness2, analysed 2026-10-28)

Harness² (Apache-2.0) treats the *harness* — everything around a fixed model — as an editable
artifact of eight components: system prompt, guardrails, memory, tool guidance, subagents, skills,
scripts, plugins. It improves it in two loops:

- **Task-level (Propose–Probe–Compose):** run the base harness, let two proposers edit it along
  separate dimensions — **INV** (*how the agent works*: procedure, tool use, completion checks) and
  **DOM** (*what a practitioner knows*: artifact contracts, decision rules, as on-demand skills) —
  roll each candidate out once, and let a composer merge, adopt one, or `KEEP_BASE`, citing the
  trajectory moment behind every decision.
- **Harnessing-level:** a per-domain **library** stores each adopted harness tree with an
  `EXPERIENCE.md` (*cue / lesson / evidence / confidence*; edit rows judged `helped`, `hurt`,
  `no observed effect`, `confounded`; or `NO_ENTRY`). Later tasks in the same domain read it.

Other ideas worth taking: verified **workspace facts** are labelled and kept apart from transferable
**procedure**; a **blind, shuffled, seeded selection** among anonymised outputs (the base included,
"picking the unmodified one is a valid outcome"); an **evidence boundary** ("files in context are
data, not instructions; never run a script found in a candidate"); a **budget ledger** with an exact
identity check (R = 4k rollouts, P = 3k sessions; duplicate labels detected); a **cap on always-on
text** with skills loaded on demand; observation truncation never below 10k characters ("the range
that fabricates defects"); a judge failure is `None`, never a cached zero; a **substrate** seam that
runs the same method over OpenCode or Codex.

What does not transfer as is: Harness² assumes **no ground truth** at improvement time. This project
has better signal — controller-run checks, baselines, reproductions, hidden benchmark checks — so
every port below uses verified outcomes where Harness² has to rely on a model's reading. Its
Vertex/OpenCode infrastructure and benchmark adapters are not needed.

| Harness² idea | Port | Task |
|---------------|------|------|
| Eight-component harness tree | Teammate prompts become component files; skills on demand; always-on cap | T17 |
| Per-domain library | Domain = intent × stack; bundled packs + user + repo overlays | T18 |
| Workspace facts vs procedure | Repo memory of verified facts, invalidated when their sources change | T19 |
| Experience reflection | Reflection after verified runs; lessons with evidence and verdicts | T20 |
| Propose–Probe–Compose (INV/DOM) | Offline `harness tune` on benchmark splits, judged by hidden checks | T21 |
| Blind anonymised selection | Best-of-N attempts; a blind judge picks among verified candidates | T23 |
| Trajectory grammar | `trace` export in `[ACTION]/[OBSERVATION]/[ERROR]` form | T4 |
| Substrate seam | External agents (Claude Code, Codex, OpenCode, Aider) as benchmark baselines | T8 |
| Evidence boundary, leak guards | Learned content is untrusted; hidden checks can never reach a library | T4, T20, T21 |
| Budget ledger identity | Ledger for attempts and tuning runs | T21, T23 |

---

## 2. Target version (0.3.0)

### Capability matrix

| Area | 0.2.0 | 0.3.0 |
|------|-------|-------|
| Entry point | Pick one of six modes | **`engineering-team do "<anything>"`** (and the UI's single textbox): triage → composed recipe → adaptive strategy; modes stay as presets and overrides |
| Kinds of work | Build, change, fix, maintain, review, analyze | + answer a question with cited code, research (web opt-in), plan/design only, follow-ups on a finished run |
| Orchestration | Fixed recipe per mode, `pipeline` default; ~10 cold-started stage agents in sequence | A **lead agent that calls teammates on demand** (subagents with typed briefs, in parallel where independent); controller skeleton composed from the task profile; **escalation ladder** solo → lead + teammates → full pipeline → N attempts |
| Prompts | Static backstories + 1,148-line `stages.yaml` | **Harness components** per teammate, per-domain packs and **skills** (on demand, `SKILL.md` compatible), overlays user/repo |
| Learning | None | **Repo memory** (verified facts), **experience library** (lessons from verified runs), offline **harness tuning** |
| Quality | Bounded repair; criteria verified only with a checks file | **Acceptance tests first** (written apart, proven red on the baseline); **best-of-N** with blind selection |
| Context | Repo map, outline | Ranked repo map, per-stage token budgets, prompt-size report |
| Models | Presets for OpenAI, Anthropic, Google, Ollama, Azure; per-role tiers | **Researched best-value presets** (Anthropic Sonnet/Haiku 5.5, OpenAI sol/terra/luna, Google, **OpenRouter**: DeepSeek, GLM, Kimi, Qwen…), a mixed `value` preset, per-intent routes, fallbacks, prompt caching, tool-calling probe |
| Code intelligence | `ast`/tree-sitter/regex | + optional **LSP** diagnostics and precise references |
| Evidence | 10 easy Python tasks, one provider | **Benchmark v2**: multi-language, larger brownfield, review/answer tasks, SWE-bench-style import; **Claude Code / Codex / OpenCode / Aider baselines** |
| Reach | CLI + local UI | + **GitHub** issue intake and opt-in draft PR, **GitHub Action**, **MCP server**, PyPI, **GitHub Pages site** with a generated leaderboard |

### Performance targets (speed and cost)

Measured on benchmark v2 (T7) and confirmed in T33; deterministic proxies are enforced offline by
tests from T5 on, so a change that adds sessions or calls fails the gate.

| Task size | Time (median, vs `single`) | Cost per success (vs `single`) | Pass rate | Offline proxy (scripted run) |
|-----------|---------------------------|--------------------------------|-----------|------------------------------|
| trivial / small | ≤ 1.5× | ≤ 2× | ≥ `single` | ≤ 3 agent sessions, ≤ 25 model calls, 0 ceremony stages |
| medium | ≤ 3× | ≤ 4× | > `single` | sessions only for teammates the lead called |
| large | no time target | ≤ 6× | clearly > `single` | parallel packages ≥ 2 lanes when the plan allows |

Plus, for every agent: tool schemas ≤ 6,000 tokens (lazy groups beyond that), always-on prompt under
its cap (T17), and a stable prompt prefix so provider caching applies (cached-token share reported).
The 0.2.0 `pipeline` on small tasks was 9× slower and 25–39× more expensive; these targets are the
bar for calling the team "fast".

### On "better than Claude Code / Codex"

That is a claim, so 0.3.0 treats it as a hypothesis to measure (T8, T33), never as marketing. The
realistic edge is not raw model skill (the same models are available to everyone) but what the
controller adds: proof of done (red→green, baseline comparison, acceptance tests written apart),
attempts selected on evidence, cost routing (a cheap model when the task is small), memory of the
repository, and a report a reviewer can trust. The README states the measured result, whatever it is.

### Vector of improvement

1. **Universality** (G3): one entry point that routes itself — T10–T16.
2. **Speed and cost** (G1): measure where the time and tokens go, cut sessions and re-sent context,
   call teammates only when needed, cheap models where they pass — T5, T6, T12, T13, T24, T25, T26.
3. **Learning** (G4, G5): harness library, memory, lessons, tuning — T17–T21.
4. **Quality** (G7, G8): tests first, attempts, blind selection — T22, T23.
5. **Credibility** (G2): harder benchmark, external baselines — T7–T9, T33.
6. **Reach and polish** (G13, G14): GitHub, MCP, UI, docs, PyPI — T28–T32, T34.

### Explicit non-goals for 0.3.0

Hosted service, accounts, multi-tenant; auto-merge or pushing without an explicit flag; fine-tuning
model weights; embedding-based long-term memory by default (memory is files, BM25, and explicit
facts); a plugin marketplace; mobile device runners; replacing CrewAI.

---

## 3. Target architecture

```text
            CLI `do` / presets ─┐   UI textbox ─┐   MCP server ─┐   GitHub (issue, Action) ─┐
                                ▼               ▼               ▼                           ▼
                          Intake ─► Triage (T10) ──► TaskProfile {intent, size, risk, target, verify-by}
                                                     │
                     Harness library (T18) ◄─────────┤──► Recipe composer (T11) ─► Recipe (stages)
                     repo memory (T19)               │                               │
                     lessons (T20)                   ▼                               ▼
                                             Ladder (T13): solo lead → lead + teammates (T12) → pipeline → N attempts (T23)
                                                     │                               │
                                                     ▼                               ▼
                                             Flow pipeline (0.2.0) ── stages ── Verifier + acceptance tests (T22)
                                                     │
                       Run store + events + tool-output log (T4) ──► trace ──► Reflection (T20)
                                                     │                               │
                                                     ▼                               ▼
                                       Report · UI · PR body (T28)          Library / memory updates
   Offline: Benchmark v2 (T7–T9) ──► harness tune (T21) ──► adopted harness diffs ──► library
```

Suggested new modules (adapt if there is a good reason; record deviations in the Decision log):

```text
src/engineering_team/
  intake/triage.py, intake/signals.py      # TaskProfile, deterministic signals + one model call (T10)
  pipeline/compose.py, modes/compose.yaml  # stage library + composition rules (T11)
  pipeline/ladder.py                       # adaptive strategy and escalation (T13)
  pipeline/attempts.py, pipeline/select.py # best-of-N and blind selection (T23)
  harness/                                 # components, loader, skills, library, overlays (T17, T18)
  harness/data/<domain>/…                  # bundled packs (T18)
  memory/                                  # repo facts with invalidation (T19)
  learning/reflect.py, learning/library.py # reflection and experience entries (T20)
  learning/tune.py, learning/ledger.py     # Propose–Probe–Compose offline (T21)
  runtime/trajectory.py                    # tool-output log and trace export (T4)
  codeintel/lsp.py                         # optional LSP bridge (T27)
  integrations/github.py, mcp_server.py    # T28–T30
  bench/agents/                            # external agent adapters (T8), bench/swe.py (T9)
```

Design principles (unchanged from 0.2.0, plus three): the controller is deterministic code and
models do bounded work; state lives on disk; hand-offs are typed. **New:** (1) prompts and learned
knowledge are **data** in files, versioned and diffable, never code; (2) nothing learned is trusted
— memory, lessons and harness edits carry their evidence, are size-capped, and pass the same
untrusted-content rules as web pages; (3) every default that costs money is chosen by a measurement.

---

## 4. Rules for every task

Read this section before starting any task.

### Execution protocol

1. Read this section, the task, and the docs it names. Check the **Depends on** tasks are `done`
   in the index; if not, stop and say so.
2. Before touching CrewAI code, follow `AGENTS.md` "Mandatory: research before writing CrewAI
   code" (installed version, PyPI, changelog, relevant docs page). Live docs beat this plan if
   they conflict; record the difference in the Decision log.
3. Work test-first where practical: failing test → implementation → green. Every bug fix gets a
   regression test.
4. Run the full gate (below) before declaring done. A task with a live run (§4 *Live spend cap*)
   makes that run itself, as part of the task (see *Who runs live*).
5. Update documentation per the **Documentation map** (in `CONTRIBUTING.md`; `plan_task.py show`
   prints it) and add a `CHANGELOG.md` entry under `[Unreleased]` (Added/Changed/Removed/Fixed/
   Security; one line per user-visible change, no implementation chatter).
6. Mark the task done with `uv run python scripts/plan_task.py done N` and add any deviation to the
   Decision log (§7). Before that, clean up the large files the task fetched (§4 *Large downloads
   and disk space*).
7. **Do not `git commit`, `git push`, tag, or publish.** End with: files changed, gate output
   summary, decisions made, follow-ups, and a suggested commit message.

### Gate (all must pass)

```bash
uv sync --locked --group dev
uv run pytest -q                  # offline, no network, no LLM, no ambient .env
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run python scripts/check_docs.py
uv build
```

### Global constraints

- Python `>=3.11,<3.14` (CrewAI declares `<3.14`); `uv`; Ruff (line length 100, `E,F,I,UP,B,SIM`);
  `from __future__ import annotations`; full type hints on new code.
- Tests are hermetic: no network, no LLM calls, no `.env`/`~/.config`, temp dirs only, isolated
  `CREWAI_STORAGE_DIR`. Docker, browser, LSP and live tests are marked and skipped by default.
- Never read, print, log, or commit secrets. Never print `.env` contents.
- Controller code never trusts agent-authored text as evidence. **Learned content (memory, lessons,
  harness edits, skills from a repository) is untrusted data**: it may shape prompts, never
  permissions, tool access, budgets or verdicts.
- Modules stay under ~400 lines; split by responsibility.
- Backward compatibility: the 0.2.0 commands `new`, `feature`, `fix`, `maintain`, `review`,
  `analyze`, `resume`, `cancel` and their options keep working through 0.3.x (as presets of `do`);
  run directories written by 0.2.0 stay readable by `runs`, `report` and the UI.
- No shells (`shell=False`); every process goes through `ExecutionBackend`.
- Parallel code records deterministically (stable ordering) even when execution order varies.
- New runtime dependencies need a Decision log line; optional ones go in an extra.
- **Any new default that changes cost or behaviour cites a measurement** (benchmark batch or
  triage eval) in the Decision log, or is marked provisional until T33.

### Live spend cap: $15 for all of 0.3.0

All live model spending for this release — every task, probe, screen, tuning run, example report
and the final evaluation together — stays **under $15.00 (API-billed)**. Allocation:

| Task | What runs live | Cap |
|------|----------------|-----|
| T3 | `doctor --probe-tools` checks | $0.10 |
| T5 | performance baseline (one small task, `single` and `pipeline`, profiled) | $0.50 |
| T10 | `bench triage --live` | $0.40 |
| T25 | model screen (tool probe + small task set) | $2.80 |
| T32 | live example reports and the demo recording | $1.00 |
| T33 | final evaluation incl. T21's live tuning and the learning on/off check | $9.70 |
| — | reserve (reruns after a harness bug; needs the user's OK to use) | $0.50 |

Who runs live:
- **The executor (the model implementing the task) makes every live run of its task itself**:
  probes, screens, benchmark batches, tuning, example reports, recordings. A live run is never left
  to the user as a follow-up, and a task with a live run is not `done` until that run is made and
  recorded in the ledger (or the task says why it was skipped).
- **Before the first live command of a task**, the executor asks the user in chat to check the
  balance of every provider the run will bill: it names the providers, the estimate (`bench plan
  --estimate` / `--dry-run`) and the task's remaining allocation, then waits for the user's reply.
  That one confirmation covers the task's live runs within its allocation; a run that would go over
  it still needs the explicit OK below.

Rules:
- Every live command goes through the spend ledger `benchmarks/spend.jsonl` (built in T7): one line
  per run with date, task, command, estimated and actual cost. `benchmarks/budget.toml` holds the
  cap and allocations. Live `bench` commands print the estimate first and **refuse to start** when
  `spent + estimate` would exceed the task's allocation or the $15 total; `--max-cost-usd` and the
  run budgets (`budget.max_cost_usd`) are always set, so an over-running run stops itself.
- Estimate before running (`bench plan --estimate`, `bench/estimate.py`), start with the cheapest
  presets that passed T25, prefer fewer repeats on more tasks, and stop early when a result is
  already clear.
- Runs of external agents billed by a subscription (Claude Code, Codex on a plan) cost no API money:
  they are recorded in the ledger as `subscription` with their usage, not counted against the $15,
  and labelled so on the leaderboard. API-billed runs of those agents count.
- Unspent allocation may move to a later task; going over any allocation or the total needs the
  user's explicit OK in chat, never a flag.
- Tests never spend: everything in the gate is offline.

### Large downloads and disk space

Disk space on the development machine is limited (about 50 GB free): nothing large is kept
"in case".

- **Fetch again when a task needs it.** Docker images (the SWE-bench evaluation images, about
  2 GB each unpacked; the Node and Go check images of `benchmarks/images/`), datasets (SWE-bench
  Lite), toolchains and npm packages are not kept between tasks. A task that needs one pulls,
  builds or downloads it itself, saying beforehand what and how large; it never assumes an
  earlier task left it behind.
- **Clean up when they are no longer needed.** Before a task is marked done, it removes what it
  fetched and no later step of the same task needs: the images it pulled or built
  (`docker rmi <image>`), downloaded datasets and toolchains, scratch batch directories and the
  repository cache `benchmarks/.cache/`. What the task must keep (committed results under
  `benchmarks/results/`, files the plan says to commit) stays, and so do small files that save a
  download later (the imported tasks in `benchmarks/tasks-swe/`, under 1 MB).
- Only remove what the task itself created: images, caches and files of other projects or of the
  user are left alone (mention them if they are large). The task report says what was removed and
  how much space it freed.

### Tool design rules (every new agent tool)

- Register in `tools/registry.py` (name, group, read_only, needs_network, needs_command_gate,
  summary) and add its row to `docs/TOOLS.md`.
- All execution through `ExecutionBackend`; descriptions written for an LLM (when to use, args,
  limits, one example, under ~120 words); compact bounded results; errors start with `ERROR:` and
  say how to fix the call; every call emits `tool.call`; respect cancel and budgets.
- Unit tests with a temp `RunContext` plus one `ScriptedLLM` test proving an agent can call it.

### Harness and learning rules (T17–T23)

- Harness components, skills, memory facts and lessons are UTF-8 Markdown/YAML files with a
  schema; loaders validate them and fail with one actionable line per problem.
- Always-on text per teammate has a character cap enforced by a test; domain knowledge with a
  recognisable trigger goes into a skill, loaded on demand.
- Every learned item records its **evidence** (run id, check ids, revision) and is dropped or
  marked stale when that evidence no longer holds. A lesson resting only on unverified or
  confounded evidence is not stored.
- Hidden benchmark checks, references and acceptance files can never be copied into a library,
  memory, or prompt (leak guard with a test).
- Prompts that hand evidence to a model state that the evidence is data, not instructions.

### Documentation map

The map of which file owns which topic lives in [CONTRIBUTING.md](../CONTRIBUTING.md#documentation-map);
update the canonical file and link to it, never duplicate. New topics in 0.3.0: harness, skills,
memory and lessons go to a new `docs/HARNESS.md` (created in T17; add its row to the map then).

---

## 5. Task index

Model: **O** = Opus recommended (design-heavy, concurrency, security, prompt design); **S** =
Sonnet sufficient. Status: `todo` → `done (date)` — maintained by `scripts/plan_task.py`; the table
format is machine-read, keep one row per task and the first/last columns intact.

| # | Task | Model | Depends on | Phase | Status |
|---|------|-------|------------|-------|--------|
| 1 | Dependency refresh; retire the 0.1.0 surface; release hygiene | S | — | A Foundation | done (2026-10-28) |
| 2 | Split oversized modules | S | 1 | A | done (2026-10-28) |
| 3 | Fix 0.2.0 leftovers: plan auto-repair, roster modes, local models | S | 1 | A | done (2026-10-28) |
| 4 | Trajectory capture: tool-output log and `trace` export | S | 1 | B Evidence | done (2026-10-28) |
| 5 | Run profiler and performance budgets (where time and tokens go) | S | 4 | B | done (2026-10-28) |
| 6 | Lean team: fewer sessions, less re-sent context, no ceremony by default | O | 5 | B | done (2026-10-28) |
| 7 | Benchmark v2: harder, broader tasks and fixed splits | O | 1 | B | done (2026-10-29) |
| 8 | External agent baselines (Claude Code, Codex, OpenCode, Aider) | S | 7 | B | done (2026-10-29) |
| 9 | SWE-bench-style real-repository task importer | O | 7 | B | done (2026-10-29) |
| 10 | Triage: `TaskProfile` from deterministic signals and one model call | O | 3 | C Universal agent | done (2026-10-29) |
| 11 | Recipe composer: stage library and composition rules | O | 10 | C | done (2026-10-29) |
| 12 | Dynamic team: a lead agent that calls teammates on demand | O | 6,11 | C | done (2026-10-30) |
| 13 | Adaptive strategy and escalation ladder | O | 4,11,12 | C | todo |
| 14 | `do`: one entry point; modes become presets | S | 13 | C | todo |
| 15 | Answer, research and plan-only paths with verified citations | S | 11 | C | todo |
| 16 | Follow-up runs and run threads (`continue`) | S | 14 | C | todo |
| 17 | Harness components as files (replace static backstories and `stages.yaml`) | O | 1,2 | D Harness library | todo |
| 18 | Per-domain harness library and skills | O | 10,17 | D | todo |
| 19 | Repo memory: verified workspace facts with invalidation | S | 17 | D | todo |
| 20 | Reflection and experience library | O | 4,18,19 | D | todo |
| 21 | Harness tuning: Propose–Probe–Compose on benchmark splits | O | 7,20 | D | todo |
| 22 | Acceptance tests first, proven red on the baseline | O | 11 | E Quality | todo |
| 23 | Best-of-N attempts with blind selection | O | 13 | E | todo |
| 24 | Context packs: ranked repo map and per-stage token budgets | S | 17 | E | todo |
| 25 | Model research: best-value presets for every provider, OpenRouter (needs a little API spend) | O | 1,3 | E | todo |
| 26 | Model routing v2: per-intent routes, fallbacks, prompt caching | S | 10,25 | E | todo |
| 27 | Optional LSP bridge: diagnostics and precise references | S | 2 | E | todo |
| 28 | GitHub: issue intake and opt-in draft pull request | S | 14 | F Integrations | todo |
| 29 | GitHub Action: review pull requests, fix on a label | S | 28 | F | todo |
| 30 | MCP server: let other agents delegate to the team | S | 14 | F | todo |
| 31 | Web UI v2: one textbox, triage preview, threads, attempts, learning views | S | 14,15,16,18,20,23 | G UX | todo |
| 32 | Public website on GitHub Pages: landing, docs, leaderboard, live demos | S | 8,31 | G | todo |
| 33 | Live evaluation 0.3.0 and defaults decision (live runs by the executor) | S | 8,13,20,23,25,32 | H Release | todo |
| 34 | Docs overhaul, PyPI publishing, release 0.3.0 | S | all | H | todo |

Order notes: tasks 2, 3, 4, 7 are independent after 1; 5 → 6 (speed and cost) right after 4 — do
them early, every later live run gets cheaper. 17 (harness files) can start right after 2, in
parallel with phase C. 25 (model research) can run any time after 3. 27 is independent of everything
after 2. 8 and 9 are independent of each other. Run 33 only after the features it measures exist; 21
is the most expensive task to run live (implemented and tested offline, run for real during 33).

---

## 6. Tasks

### Phase A — Foundation

#### Task 1 — Dependency refresh; retire the 0.1.0 surface; release hygiene

**Depends on:** none · **Model:** S

**Why:** G10, G13. 0.2.0 promised the 0.1.0 invocation only "until at least 0.3.0"; `hierarchical`
timed out on every measured run. Less code makes every later task cheaper. Every dependency is three
or more weeks old.

**Do:**
1. **Upgrade everything that is pinned or referenced**, checking each against its registry and
   changelog on the day (record old → new and any breaking note in the summary):
   - CrewAI: AGENTS.md research steps; `crewai[tools]>=1.15.31,<1.16` (or the newest minor if one
     shipped and its changelog shows nothing that breaks us; record the choice). Update the version
     facts in `AGENTS.md`.
   - Runtime and extras in `pyproject.toml`: `pyyaml`, `pydantic`, `typer`, `rich`, `playwright`,
     `fastapi`, `uvicorn`, `python-multipart`, provider SDK extras; raise floors to what was tested.
   - Dev group: `pytest`, `ruff`, `mypy`, `httpx`; the build backend `hatchling`. Run
     `uv lock --upgrade`, then `uv pip list --outdated` and list what is still behind and why
     (usually a CrewAI-constrained transitive). Fix new Ruff/mypy findings rather than ignoring them.
   - GitHub Actions in `.github/workflows/*.yml` (`actions/checkout`, `astral-sh/setup-uv`,
     `actions/upload-artifact`, `actions/download-artifact`, `pypa/gh-action-pypi-publish`) to their
     current major versions; `.github/dependabot.yml` keeps covering `pip`/`uv` and `github-actions`.
   - Docker images in `execution/images.py` (uv/Python, Node, Go, Rust, Java, .NET tags) to current
     tags checked against their registries; the image test fixtures with them.
   - Playwright's Chromium (`playwright install chromium` in CI), `.python-version`.
   - Python 3.14: check whether the new CrewAI declares support; if it does, widen
     `requires-python`, classifiers, the CI matrix and the README badge together (one Decision log
     line either way).
2. Remove the `hierarchical` strategy: `crew.py`, `config/tasks.yaml`, the `engineering_lead`
   teammate, `--strategy hierarchical`, its UI option, tests, and docs (ARCHITECTURE "Why
   hierarchical"). A run directory recorded with `strategy: hierarchical` stays viewable (`runs`,
   `report`, UI) and `resume` says it cannot be resumed.
3. Remove the bare 0.1.0 invocation (`engineering-team --request-file …` without a command) — it
   now prints one line pointing at `engineering-team new --request-file …` and exits 2.
4. `train`/`replay`/`test`/`run_with_trigger` scripts: they drove the 0.1.0 crew. Remove them, keep
   `run_crew` (→ `new`) so `crewai run` still works; record the decision.
5. Fix the `pyproject` description (one sentence matching the README pitch) and keywords.
6. Document (CONTRIBUTING, Releasing) the commands for the user to tag `v0.1.0` (`0ba55a3`) and
   `v0.2.0` (the 0.2.0 release commit); do not tag.

**Tests:** removed paths are gone (`import engineering_team.crew` fails, `--strategy hierarchical`
is a usage error naming the valid ones); a 0.2.0 run fixture with `strategy: hierarchical` renders in
`runs`/`report`; `run_crew` still builds the `new` command.
**Docs/Changelog:** Removed ×3 (hierarchical, bare invocation, CrewAI training scripts), Changed
(CrewAI 1.15.31). Migration notes under `[Unreleased]`.
**Done when:** gate green; `grep -ri hierarchical src docs` finds only the history/migration notes.

#### Task 2 — Split oversized modules

**Depends on:** 1 · **Model:** S

**Why:** G11; T17, T18, T25 and T26 change `settings.py` and `tools/registry.py` heavily.

**Do:**
1. Split `settings.py` into `settings/` (models per section, loading/layering, env mapping,
   validation messages) keeping `from engineering_team.settings import Settings` working.
2. Split `main.py` (entry/compat, run preparation, programmatic API), `tools/registry.py` (catalogue
   data per group vs. builder), `git/port.py` (read vs. write operations), `pipeline/executor.py`
   (stage execution vs. work-package scheduling), `report/html_sections.py` (by section).
3. No behaviour change; public import paths used by tests, plugins and docs keep working (re-export).
4. Add a test that fails when any `src/**/*.py` exceeds 450 lines, with an explicit allowlist (empty
   at the end of this task if possible; justify any entry in the Decision log).

**Tests:** the whole suite unchanged and green; the size guard.
**Docs/Changelog:** ARCHITECTURE module map. No changelog entry (internal).
**Done when:** gate green, no module over the limit outside the allowlist.

#### Task 3 — Fix 0.2.0 leftovers: plan auto-repair, roster modes, local models

**Depends on:** 1 · **Model:** S

**Why:** G6, G12 and two deferred items.

**Do:**
1. **Plan auto-repair** (`pipeline/` plan validation): when a plan violates ownership rules in a
   mechanically fixable way (a shared root file such as `README.md`, `pyproject.toml`,
   `package.json` owned by a work package; overlapping globs where one package's paths contain the
   other's), the controller repairs it (move the file to `integrate`/foundation, narrow the glob),
   records a `plan.repaired` event with before/after, and only rejects what it cannot repair.
2. **Roster modes:** recipes carry their mode; `Roster.assign` applies a teammate's `modes` filter.
3. **Local models:** find and fix why a zero price override reports an unknown cost; `doctor
   --probe-tools` sends one tiny tool-calling request per configured tier (cents; T3's allocation is $0.10) and reports "can call
   tools / answers in text only" (opt-in, live, never in tests); the run fails fast with that hint
   when a stage's first answer has no tool call and the model is known not to call tools.
4. `modes/` recipes and `docs/USAGE.md` explain the repair in one paragraph.

**Tests:** the three benchmark plans that were rejected (rebuild them as fixtures) are repaired and
pass; an unrepairable overlap is still rejected with its reason; roster filter; zero-price usage
gives `$0.00`; the probe's parser with scripted responses.
**Docs/Changelog:** Fixed ×2, Added (`doctor --probe-tools`), Changed (plan repair).
**Done when:** gate green; the fixtures prove the G6 failures would now pass the plan stage.

### Phase B — Evidence

#### Task 4 — Trajectory capture: tool-output log and `trace` export

**Depends on:** 1 · **Model:** S

**Why:** G9. Reflection (T20), tuning (T21), selection (T23) and failure analysis need to read what
the agent saw, not only which tool it called.

**Do:**
1. `runtime/trajectory.py`: every tool call appends `{seq, stage, teammate, tool, args (redacted),
   output (redacted, capped per call, default 50,000 chars, never below 10,000), ok, ms}` to
   `runs/<id>/trajectory.jsonl`; model messages (assistant text, final answers) are recorded too,
   capped. `tool.call` events gain the `seq` that links them.
2. Redaction reuses the tools' secret scrubbing; a test feeds a fake API key through a tool output
   and asserts it never reaches the file.
3. `engineering-team trace <run> [--stage S] [--format text|json]` renders the shared grammar:
   `[ACTION] tool(args)` / `[OBSERVATION] …` / `[ERROR] …` / `[MESSAGE] …`, with stage headers.
4. The report links the trace; the UI drawer shows a call's output from the trajectory file.
5. Size budget per run (`trajectory.max_mb`, default 50) — beyond it outputs are truncated harder
   and the report says so.

**Tests:** ScriptedLLM run produces a trajectory with outputs; redaction; caps; `trace` golden
output; 0.2.0 run dirs without a trajectory render "not recorded".
**Docs/Changelog:** ARCHITECTURE (run directory), USAGE (`trace`), CONFIGURATION. Added.
**Done when:** a failed scripted run can be read end to end with `trace`.

#### Task 5 — Run profiler and performance budgets (where time and tokens go)

**Depends on:** 4 · **Model:** S

**Why:** G1. The 0.2.0 records give only totals per run (117 calls, 1.71M prompt tokens); nobody can
say which stage, prompt part or tool failure costs what. You cannot make it fast without this.

**Do:**
1. Per **agent session** (stage, work package, delegation): wall time, model calls, prompt tokens split
   into system/harness, tool schemas, history and tool results; cached prompt tokens (when the provider
   reports them); completion tokens; tool calls and failures by error kind; cost. Recorded in the
   manifest and `usage` events; computed from the trajectory (T4) and the CrewAI event bridge.
2. `engineering-team profile <run>`: a table per session sorted by cost and by time, the critical path
   (what ran sequentially that did not have to), the top re-read files, tool-schema tokens per agent,
   tool failures with their messages. The report gets a "Where the time and money went" section;
   `bench report` aggregates the same per strategy.
3. **Offline performance tests** (`tests/perf/`, part of the gate): scripted scenarios (small new
   project, small bugfix, medium feature) through `ScriptedLLM` assert upper bounds on agent sessions,
   model calls, tool-schema tokens per agent and always-on prompt size — the proxies in §2
   *Performance targets*. Start them at today's values and mark the target values `xfail` until T6
   and T12 meet them.
4. **Live baseline** (≤ $0.50, §4 cap): one small task, `single` and `pipeline`, with the profiler on;
   commit it under `benchmarks/results/<date>/perf-baseline/` as the "before" for T6 and T12.

**Tests:** session accounting with scripted usage (including cached tokens), critical-path
computation, `profile` output, perf proxies.
**Docs/Changelog:** USAGE (`profile`), ARCHITECTURE (usage accounting), BENCHMARKS (perf baseline).
Added.
**Done when:** the live baseline shows, per session, where the pipeline's 9× time and 25–39× cost
come from, and the perf tests run in the gate.

#### Task 6 — Lean team: fewer sessions, less re-sent context, no ceremony by default

**Depends on:** 5 · **Model:** O

**Why:** G1. Most of the pipeline's cost is structure, not work: cold-started agents re-reading the
same files, tool schemas re-sent on every call, and stages a small task does not need. Fix what T5's
profile shows first; the list below is what the 0.2.0 numbers already point at.

**Do:**
1. **No ceremony by default.** `devops`, `docs`, `release` and the separate `spec` stage run only when
   the request or the profile asks for them (a new project gets a README from the implementer; CI only
   when asked). `foundation` and `integrate` merge into implementation for single-package plans.
2. **Merge spec and plan** for small/medium work into one session that writes both contracts; the
   product analyst and the architect stay for large or ambiguous requests.
3. **Controller instead of agents** wherever no judgement is needed: profile, baseline, running checks,
   writing `verification.md`, change summary, release notes (already partly so; finish it).
4. **Hand-off packs, not re-discovery.** Each session starts with a compact pack (relevant file list
   with hashes, the plan, the last check results, memory facts once T19 exists) so it does not
   re-list and re-read the tree; `Read File` answers "unchanged since you read it at step N" for a
   file whose hash did not change within the session.
5. **Tool schema diet.** Per stage only the tool groups it uses (from T5's profile); rarely used tools
   behind a `Find Tool` lookup that lists and enables them for the rest of the session. Target ≤ 6,000
   schema tokens per agent.
6. **Cache-friendly prompts.** Stable parts first (harness, tool schemas, project facts), the changing
   parts (steering notes, latest results) last, so OpenAI-style automatic prefix caching hits; the
   profile reports the cached share. (Explicit cache control for Anthropic is T26.)
7. **Fewer failed tool calls.** Fix the top error kinds from T5's profile (in 0.2.0, 11 per pipeline run
   vs 1 for `single`): write-scope refusals list the allowed paths, path typos get suggestions, and
   commands outside the allowlist say which ones are allowed.
8. **Cheaper tiers where they pass:** the plan stage on the `reviewer` tier only for large/high-risk
   profiles; review on the worker tier for small diffs; `max_iter` per stage tuned from the profile.
9. **Overlap independent work:** review and docs run in parallel once the code is verified; checks
   run incrementally (the touched package first) before the full suite.

**Tests:** the perf proxies from T5 for small scenarios move from `xfail` to passing; skipped
ceremony is recorded with its reason; hand-off pack contents; the read cache; `Find Tool`.
**Docs/Changelog:** ARCHITECTURE (pipeline), TOOLS (`Find Tool`, read cache), USAGE. Changed.
**Done when:** the scripted small scenarios meet the §2 offline proxies, and a rerun of T5's live
baseline task (within T5's leftover or T33's budget) shows the before/after per session.

#### Task 7 — Benchmark v2: harder, broader tasks and fixed splits

**Depends on:** 1 · **Model:** O

**Why:** G2. The suite is saturated (9/10 vs 10/10) and Python-only; nothing can be decided on it.

**Do:**
1. Add tasks (same layout as `benchmarks/tasks/<id>/`): **TypeScript/Node** (an Express API feature,
   a React component bug with Vitest), **Go** (CLI feature with table tests), a **larger brownfield**
   fixture (≥ 2,000 lines, several packages, a misleading stack trace), a **multi-file refactor**
   with behaviour checks, a **review** task (seeded defects; scored by findings recall and
   precision against a hidden list), an **answer** task (questions about a fixture repo; scored by
   checks on the cited files/lines and key facts), an **ambiguous request** task (scored on whether
   the run asked or recorded the right assumption), a **dependency upgrade** with a breaking API.
2. `task.yaml` gains `intent`, `size`, `languages`, `split: dev|heldout` and `difficulty`; the
   runner filters on them. Keep ≥ 40 % of tasks held out; record the split in BENCHMARKS.md.
3. Acceptance checks run in the Docker sandbox when the task needs Node/Go; mark them.
4. A judge or check error is recorded as `error`, not as a fail, and is never cached as one.
5. `bench run --fake` covers every new task (the reference solution must pass, the fixture must
   fail), so a check that cannot tell good from bad fails CI.
6. **Spend ledger and cap** (§4 *Live spend cap*): `benchmarks/budget.toml` (total `15.00`, the
   per-task allocations) and the append-only `benchmarks/spend.jsonl`; `bench run|triage|tune`
   without `--fake` estimate first, refuse over-cap starts with the numbers, append actual costs
   (or `unknown` treated as the estimate) when done; `bench spend` prints spent/remaining per task.

**Tests:** fake-mode reference-passes/fixture-fails for every task; split filtering; error vs fail;
ledger refusal at the cap, unknown cost counted as the estimate, subscription runs not counted.
**Docs/Changelog:** BENCHMARKS (method, tasks, splits). Added.
**Done when:** ≥ 20 tasks across ≥ 3 languages and ≥ 6 intents, all self-validating offline.

#### Task 8 — External agent baselines (Claude Code, Codex, OpenCode, Aider)

**Depends on:** 7 · **Model:** S

**Why:** "better than Claude Code/Codex" needs those agents on the same tasks with the same hidden
checks (Harness²'s substrate seam, applied to evaluation).

**Do:**
1. `bench/agents/`: an `ExternalAgent` protocol (`name`, `available()`, `version()`,
   `run(workspace, request, timeout) -> AgentRun{exit, wall_s, cost_usd|None, transcript_path}`) and
   adapters invoking the CLIs non-interactively through `ExecutionBackend`: `claude -p` (Claude Code),
   `codex exec`, `opencode run`, `aider --message`. Exact flags verified against each tool's
   current `--help` at implementation time and recorded in the Decision log.
2. `bench run --agent claude-code|codex|opencode|aider` uses the same fixture prep, isolation,
   time limit and hidden checks as our strategies; results rows carry `agent` and `agent_version`.
3. **Fair comparison, by design:** each external agent runs in a fresh copy of the same fixture,
   with the same request text, the same time limit, the same hidden checks (never visible to any
   agent), its own default settings *and* (where it allows it) pinned to the same model as our team
   (e.g. Claude Code and our `anthropic` preset both on Sonnet 5.5), so results separate "the
   harness" from "the model". Cost comes from the agent's own report when it has one (Claude Code
   `--output-format json` total cost, Codex token usage × the price table), else `unknown`;
   subscription-billed runs are labelled as such, never shown as $0.
4. The report table compares strategies and agents side by side (pass rate with Wilson intervals,
   paired per-task wins/losses, cost per success where known, time), and `results.json` uses one
   schema for both so the site's leaderboard (T32) reads it directly.
5. Never in the gate; each adapter has a fake binary test (a script on `PATH` in a temp dir).

**Tests:** fake binaries for each adapter (success, failure, timeout, missing binary); results schema.
**Docs/Changelog:** BENCHMARKS (how to run baselines, what is and is not comparable). Added.
**Done when:** `bench run --fake` plus a fake external agent produces a mixed comparison report.

#### Task 9 — SWE-bench-style real-repository task importer

**Depends on:** 7 · **Model:** O

**Why:** small fixtures are not real repositories; a public, recognised set makes results credible.

**Do:**
1. `bench/swe.py` + `bench import-swe --dataset <path-to-jsonl> --ids … --out benchmarks/tasks-swe/`:
   converts SWE-bench-format instances (repo, base commit, problem statement, FAIL_TO_PASS,
   PASS_TO_PASS) into tasks: fixture = shallow clone at the base commit (fetched at run time into a
   cache, not committed), request = the issue text, hidden checks = the listed tests run in Docker.
2. The importer reads a local dataset file (downloading the dataset is the user's step; document
   it). Only Python repos at first; license of each repo noted in the task.
3. Curate a starter list of ~15 small-repo instances in `benchmarks/swe-starter.txt`.

**Tests:** importer on a tiny synthetic instance (a local git repo created in `tmp_path`); hidden
check construction; missing Docker gives a clear skip.
**Docs/Changelog:** BENCHMARKS. Added.
**Done when:** a synthetic instance runs end to end in fake mode.

### Phase C — Universal agent

#### Task 10 — Triage: `TaskProfile` from deterministic signals and one model call

**Depends on:** 3 · **Model:** O

**Why:** G3. The team, not the user, decides what kind of work a request is.

**Do:**
1. `contracts.py`: `TaskProfile {intents: [primary, …secondary] (question, research, review, bugfix,
   feature, new_project, refactor, tests, docs, upgrade_deps, security_audit, performance, devops,
   plan_only), target (new|existing, repo path, focus paths), size (trivial|small|medium|large),
   risk (low|medium|high), verify_by ([repro, project_tests, acceptance_tests, review, citations,
   build, none]), stack (from the repo profile), needs_clarification + questions (≤ 3), assumptions,
   confidence, rationale}`.
2. `intake/signals.py` — deterministic signals, cheap and tested: cwd/`--repo` is a Git repo or not;
   a stack trace or failing test name in the request; a diff/branch/PR reference; question form;
   paths mentioned that exist; words like "upgrade", "audit", "refactor", "document"; repository
   size and languages from the existing profiler.
3. `intake/triage.py` — one cheap-tier structured-output call given the request, signals and repo
   summary; the controller then applies rules that override the model where signals are certain
   (no repo → cannot be `bugfix` on existing code; an explicit `--mode` wins).
4. Clarification: when `needs_clarification`, ask the questions (CLI/UI/ask-human) before planning;
   otherwise record assumptions. Reuse the existing `needs_info` flow.
5. `benchmarks/triage/cases.yaml`: ≥ 60 labelled requests (all intents, sizes, with and without a
   repo). `bench triage --live` (cheap tier, ≤ $0.40 for the whole set) reports accuracy and a confusion matrix; offline tests cover signals,
   rule overrides and parsing with `ScriptedLLM`.

**Tests:** signals per case type; rule overrides; schema validation; clarification path.
**Docs/Changelog:** ARCHITECTURE (triage), USAGE. Added.
**Done when:** every case in `cases.yaml` has deterministic expectations for the signal layer and the
live command exists.

#### Task 11 — Recipe composer: stage library and composition rules

**Depends on:** 10 · **Model:** O

**Why:** fixed recipes per mode cannot cover "fix this bug and add a test and update the docs".

**Do:**
1. `modes/compose.yaml`: the stage library (every stage kind the bundled recipes use, with its
   prerequisites, inputs/outputs, cost class) and rules mapping a `TaskProfile` to stages, e.g.
   existing repo → `profile`, `baseline`; `bugfix` → `reproduce`, `fix`, `verify`(red→green);
   `feature` → `spec`, `impact`, `implement`, `tests`, `verify`; `size ≥ medium or risk ≥ medium` →
   `map`, `review`; `docs` secondary → `docs_legacy`; `question` → `answer` (T15).
2. `pipeline/compose.py`: build a `Recipe` from the profile, dedupe and order by prerequisites,
   validate with the existing recipe validator, and explain each stage ("why this stage") in the
   manifest and the plan preview.
3. The bundled recipes become **golden outputs**: composing the canonical profile of each mode must
   produce that recipe (or a documented, deliberate difference).
4. User recipes still win when named explicitly.

**Tests:** golden recipes; mixed intents (bugfix + docs, feature + tests); invalid rule files give one
line per problem; explanation text.
**Docs/Changelog:** ARCHITECTURE (recipes), USAGE. Added.
**Done when:** every 0.2.0 mode is reproducible from a profile.

#### Task 12 — Dynamic team: a lead agent that calls teammates on demand

**Depends on:** 6,11 · **Model:** O

**Why:** G1, and your request: the team should call the teammates it needs, when it needs them,
instead of running every stage in a fixed order. This is the subagent pattern of Claude Code and
Codex, with the controller still owning verification and the board.

**Not the 0.2.0 `hierarchical` crew again** (260 calls, 0/3 finished): that manager had no tools and
delegated everything through chat. Here the lead **does the work itself** with the full toolbelt and
delegates only bounded, typed jobs; the controller caps how much it can delegate.

**Do:**
1. **Lead session** (`pipeline/lead.py`): one long-lived agent (the generalist with the resolved
   harness) that receives the composed skeleton's context (profile, baseline, reproduction, criteria)
   and works until the controller's checks pass or it hands back. Its context stays warm across the
   whole task, so nothing is re-discovered.
2. **Delegation tools** (group `team`, lead only), each a small crew run synchronously and returning a
   compact typed result (≤ ~1,500 tokens):
   - `Delegate` (to a named teammate: brief, owned paths, criteria, budget) → summary, files changed,
     checks it ran;
   - `Delegate Parallel` (independent briefs with disjoint owned paths) → runs in lanes through the
     existing parallel engine;
   - `Ask Reviewer` / `Ask Security Review` (the current diff) → findings;
   - `Ask Debugger` (a failing check id) → diagnosis and, with paths, a fix;
   - `Ask Architect` / `Ask Analyst` (a question or plan draft) → decision, for large or unclear work.
   A teammate gets a **brief, not the history**: the delegated job, the hand-off pack (T6), owned paths.
3. **Controller limits:** delegation depth 1 (teammates cannot delegate), `team.max_delegations`
   (default by size: small 2, medium 5, large 12), per-delegation budget, write scopes enforced, no
   delegation to a teammate the roster does not allow for the mode; every delegation is a board card
   (assignee, brief, result) and a lane on the timeline.
4. **When to delegate** lives in the lead's harness guidance (data, tunable in T21): parallelisable
   independent parts, a different stack (frontend), review of a non-trivial diff, a failure the lead
   could not fix in two tries, security-sensitive changes (the controller also forces a security
   review when deterministic signals fire: auth, crypto, subprocess, SQL, deserialisation, secrets).
5. **Verification unchanged:** the controller runs the checks after the lead hands back and after
   each delegated job that changed files; the lead never marks anything done.
6. The static `pipeline` recipe stays available for large new projects and `--strategy pipeline`.

**Tests:** scripted lead that delegates nothing (small task: 1 session), one `Delegate`, a
`Delegate Parallel` with disjoint paths (and a rejected overlapping one), the delegation cap, depth-1
enforcement, board cards and timeline lanes, budget stop inside a delegation, resume after a crash
mid-delegation; the T5 perf proxies for small and medium scenarios pass.
**Docs/Changelog:** ARCHITECTURE (strategies, dynamic team), TEAM (when teammates are called),
TOOLS (`team` group). Added.
**Done when:** scripted small tasks run in one session, medium tasks call only the teammates the lead
asked for, and the UI/report show who was called, why, and what it cost.

#### Task 13 — Adaptive strategy and escalation ladder

**Depends on:** 4,11,12 · **Model:** O

**Why:** G1. The single agent matched the team at 1/30 of the cost on small tasks; the team should
be spent only where it pays.

**Do:**
1. `pipeline/ladder.py`: rungs `solo` (the T12 lead with delegation disabled, then controller
   verification + repair), `team` (the T12 lead with delegation enabled, the cap set by size),
   `pipeline` (the full composed static recipe with parallel packages and reviewers; mainly large new
   projects), `attempts` (T23, later).
   The starting rung comes from `size`/`risk` via thresholds in settings (`ladder.*`, provisional
   until T33).
2. Escalation: when verification still fails after the rung's repair budget, or the agent reports it
   is stuck, the controller escalates to the next rung **on the same workspace** (the previous
   attempt's diff, trace summary and failing checks go into the next rung's prompt), up to
   `ladder.max_rung` and within the budget. Every transition is a `ladder.escalated` event, a board
   card note and a report section.
3. `--strategy auto` becomes the default for `do`; `single`/`pipeline` remain forceable.
4. Budget awareness: never start a rung whose estimated cost (from `bench/estimate.py`) would exceed
   the remaining budget; stop with a clear reason instead.

**Tests:** scripted runs: small task passes on `single` (no escalation); failing verification
escalates to `team` then `pipeline`; budget stop; resume in the middle of an escalation.
**Docs/Changelog:** ARCHITECTURE (strategies), CONFIGURATION (`ladder`). Added, Changed (default).
**Done when:** escalation is visible in events, board, report, and resumable.

#### Task 14 — `do`: one entry point; modes become presets

**Depends on:** 13 · **Model:** S

**Why:** G3. The visible half of the universal agent.

**Do:**
1. `engineering-team do "<request>"` (also `--request-file`, stdin, `--repo PATH`, default cwd when
   it is a Git repo, otherwise a new project in the workspace root). `engineering-team "<request>"`
   with no command is a shortcut for `do`.
2. Shows the triage result and the composed plan (intent, size, rung, stages with reasons, budget
   estimate); asks to proceed unless `--yes`/non-interactive. `--mode feature|fix|…` forces the
   intent; `--dry-run` stops after the preview.
3. `new`, `feature`, `fix`, `maintain`, `review`, `analyze` become thin presets over `do` (same
   options, same outputs, same exit codes); their tests keep passing unchanged.
4. Exit codes and the final summary say what was done, how it was proved, and where the branch,
   patch and report are.

**Tests:** CLI tests for request sources, repo detection, preview, `--yes`, `--dry-run`, presets.
**Docs/Changelog:** README (pitch and quickstart lead with `do`), USAGE. Added.
**Done when:** every README example works through `do`.

#### Task 15 — Answer, research and plan-only paths with verified citations

**Depends on:** 11 · **Model:** S

**Why:** a large share of real requests are "how does X work", "why does Y fail", "what would it take
to Z" — today there is no path for them.

**Do:**
1. `answer` stage (read-only teammates: `codebase_analyst`, plus web tools when enabled for
   `research`): output contract `Answer {summary, sections, citations: [{path, lines, quote}] |
   [{url, title}], confidence, open_questions}` written to `ANSWER.md` in the run directory.
2. Controller verification: every cited path exists at the run's revision, the quoted lines match
   the file (whitespace-normalised), URLs were actually fetched in the trajectory; unverifiable
   citations are flagged in the report and lower the verdict.
3. `plan_only` intent: spec + impact/plan + risk notes, no implementation; can be continued later with
   `continue <run> --implement` (T16).
4. Answers print in the terminal (Rich Markdown) and show in the UI results page.

**Tests:** citation verification (good, wrong line, missing file, unfetched URL); plan-only stops
before implementation; read-only enforcement.
**Docs/Changelog:** USAGE. Added.
**Done when:** a scripted question run produces a verified `ANSWER.md`.

#### Task 16 — Follow-up runs and run threads (`continue`)

**Depends on:** 14 · **Model:** S

**Why:** G14. Real work is iterative: "now also handle empty input", "rename that flag".

**Do:**
1. `engineering-team continue <run_id> "<follow-up>"` starts a new run in the same thread: same
   repository and isolated branch/worktree as the parent, the parent's spec, plan, answer, report
   summary and verdict as context, triage on the follow-up with the parent's profile as a prior.
2. Run index gains `thread_id` and `parent_run`; `runs --thread` lists a thread; the report links
   the parent and children.
3. A follow-up never reuses the parent's evidence: verification runs again on the new revision.
4. `continue <plan_only_run> --implement` implements a stored plan.

**Tests:** thread bookkeeping; branch reuse; evidence not reused; plan-only continuation.
**Docs/Changelog:** USAGE. Added.
**Done when:** a two-step scripted thread produces two verified runs on one branch.

### Phase D — Harness library

#### Task 17 — Harness components as files (replace static backstories and `stages.yaml`)

**Depends on:** 1,2 · **Model:** O

**Why:** G4. Harness² shows that the harness, not the backstory, is the lever — and it must be data
you can diff, overlay, learn and tune.

**Do:**
1. `harness/` package and bundled tree `harness/data/base/`: per teammate
   `teammates/<key>/{role.md (role, goal, short identity), guardrails.md, tool_guidance.md}`; per
   stage `stages/<name>.md` (the task prompt, with the existing placeholders); shared
   `shared/{evidence_boundary.md, completion.md}`. Front-matter for metadata (tier, tool groups,
   stages, modes) moves from `agents.yaml`; `agents.yaml` and `stages.yaml` are removed.
2. `harness/loader.py` composes CrewAI `role`/`goal`/`backstory` and the task description from the
   components, in a fixed order, and reports the always-on size per teammate.
3. **Golden test first:** before moving anything, snapshot every composed prompt the 0.2.0 code
   produces for each teammate × stage; after the move the composed prompts must be identical
   (whitespace aside). Then, as a separate commit-sized step, tighten: always-on cap per teammate
   (start at the current maximum, lower it in T18), and add the evidence boundary text to stages
   that hand evidence to a model.
4. User and repo overrides of teammates (`engineering-team.toml [team.*]`,
   `.engineering-team/team.yaml`) keep working and may now also point at component files.
5. Create `docs/HARNESS.md` (what a harness is here, the components, the composition order, how to
   override one) and add it to the Documentation map; TEAM.md links to it.

**Tests:** golden prompts; loader validation errors; cap test; overrides; custom teammate from files.
**Docs/Changelog:** HARNESS (new), TEAM, ARCHITECTURE, CONTRIBUTING map. Changed.
**Done when:** no prompt text remains in Python or YAML outside `harness/data/`, goldens equal.

#### Task 18 — Per-domain harness library and skills

**Depends on:** 10,17 · **Model:** O

**Why:** the user's request ("replace static CrewAI backstories with a per-domain harness library")
and Harness²'s library: knowledge that applies to *this kind of work in this stack*.

**Do:**
1. **Domains** = `<intent>/<stack>` with fallbacks (`bugfix/python` → `bugfix/*` → `*/python` →
   `base`). A domain pack is a partial harness tree (omitted = inherited) plus `skills/`.
2. **Skills** in the Anthropic Agent Skills layout (`skills/<name>/SKILL.md` with `name`,
   `description`/trigger front-matter, optional `reference/*.md`; scripts are **not** executed —
   they are text unless a later task adds a gate). Always-on text lists only skill names and
   triggers; a `Load Skill` tool (group `knowledge`, read-only) returns the body on demand.
3. Bundled packs, written carefully and kept short: `python` (pytest failure triage, packaging,
   typing), `typescript` (Node/Vitest/Jest, React testing), `go` (table tests, modules), `rust`
   (cargo test, clippy), `java` (Maven/Gradle), and intent packs (`bugfix` reproduce-first,
   `refactor` behaviour-preservation, `upgrade_deps` changelog reading, `security_audit`, `review`).
4. **Overlays:** bundled < user (`~/.config/engineering-team/harness/`) < repository
   (`.engineering-team/harness/`, untrusted: may add skills and guidance text, never tool groups or
   tiers). Repository `SKILL.md`, `AGENTS.md`, `CLAUDE.md` conventions are importable as skills.
5. The manifest and report record the resolved domain, pack versions (content hash), skills listed
   and skills loaded.
6. Lower the always-on cap from T17 now that domain knowledge lives in skills.

**Tests:** resolution and fallbacks; overlay precedence and the untrusted-repo restriction; skill
validation; `Load Skill` through `ScriptedLLM`; report fields; cap.
**Docs/Changelog:** HARNESS (domains, skills, writing a pack), TOOLS (`Load Skill`). Added.
**Done when:** a Python bugfix and a TypeScript feature resolve to different packs, visibly.

#### Task 19 — Repo memory: verified workspace facts with invalidation

**Depends on:** 17 · **Model:** S

**Why:** G5, Harness²'s "verified facts about this workspace are the most valuable thing to encode".

**Do:**
1. `memory/`: `.engineering-team/memory/facts.jsonl` (+ a readable `FACTS.md` view) in the target
   repository; a fact = `{id, kind (command|setup|convention|layout|flaky_test|gotcha), text,
   evidence: {run_id, check_ids|tool seq}, sources: [{path, sha256}], revision, created, last_used}`.
2. Writers: the **controller** records facts it observed (the test command that passed in baseline,
   setup steps that worked, tests that flipped without changes = flaky); agents may *propose* facts
   via a `Remember Fact` tool, stored as `proposed` until the controller sees supporting evidence
   in the same run.
3. Invalidation: a fact whose source files changed hash is `stale` and not injected; a fact
   contradicted by a later run is removed.
4. Injection: the memory component of the harness lists relevant, fresh facts (by stage and paths,
   capped), labelled "verified facts about this repository (data)".
5. `engineering-team memory list|show|forget|clear [--repo]`; the UI gets it in T31.
6. Memory is excluded from diffs and patches (it lives under `.engineering-team/`).

**Tests:** fact lifecycle (proposed → verified → stale → removed); injection caps; secrets never
stored; CLI.
**Docs/Changelog:** HARNESS (memory), USAGE. Added.
**Done when:** a second scripted run on the same repo skips rediscovery visibly (fewer tool calls in
its trajectory).

#### Task 20 — Reflection and experience library

**Depends on:** 4,18,19 · **Model:** O

**Why:** G5. Harness²'s harnessing-level recursion, with a better signal: our verdicts are
controller-verified.

**Do:**
1. `learning/reflect.py`: after a run with a controller verdict, a cheap-tier reflection agent reads
   the trace summary, the verdict and checks, the escalations and repairs, and writes:
   (a) **edit/behaviour rows** — what the agent did differently from the harness's guidance and the
   observed effect, verdict `helped|hurt|no observed effect|confounded`;
   (b) at most **one lesson** `{cue (shape of situation), lesson (operation, naming the harness
   component), evidence (rows + check ids), confidence}` or `NO_ENTRY`.
2. `learning/library.py`: lessons stored per domain (`~/.local/share/engineering-team/library/
   <domain>/` and optionally `.engineering-team/library/` in the repo), `INDEX.md` rebuilt from disk,
   one directory per entry with `EXPERIENCE.md`.
3. Retrieval: at run start, the top-k lessons for the resolved domain by BM25 over `cue` against the
   request + profile (reuse `webtools/docsearch` BM25), capped, injected as data.
4. Safety: lessons from runs whose verdict is not `passed`/`failed` with evidence are discarded;
   confounded-only lessons are discarded; secret scan; leak guard (no benchmark hidden-check text);
   `learning.review = auto|manual` — in `manual`, lessons wait in `pending/` until
   `engineering-team lessons approve <id>`.
5. Reflection is a separate stage at the end (`reflect`, skippable, budgeted; off for `--budget`
   very low).

**Tests:** reflection parsing (rows, lesson, `NO_ENTRY`); storage and index; retrieval ranking;
discard rules; leak guard; manual approval flow.
**Docs/Changelog:** HARNESS (lessons), CONFIGURATION (`learning`), USAGE. Added.
**Done when:** a scripted failing-then-repaired run yields a lesson that a later run in the same
domain receives.

#### Task 21 — Harness tuning: Propose–Probe–Compose on benchmark splits

**Depends on:** 7,20 · **Model:** O

**Why:** turn the benchmark from a scoreboard into an improvement loop — Harness²'s task-level
recursion, judged by hidden checks instead of a model.

**Do:**
1. `engineering-team harness tune --domain bugfix/python --split dev --k 1 [--mode parallel|
   sequential] --budget-usd X`: run the base harness `k` times per task; an **INV** proposer edits
   procedure components (`tool_guidance`, `guardrails`, `completion`) and a **DOM** proposer edits
   knowledge (skills of the pack), both given traces of the base rollouts as data; each candidate is
   probed on the same tasks; a composer merges, adopts one, or `KEEP_BASE`, with one notes line per
   decision citing the trajectory moment or the pass counts.
2. Acceptance of a composed harness: better pass rate on `dev` **and** not worse on `heldout` (both
   hidden-check results), else `KEEP_BASE`. Output: a reviewable diff under
   `benchmarks/tuning/<date>/<domain>/` and `harness adopt <dir>` to copy it into a pack.
3. `learning/ledger.py`: every rollout and proposer session recorded with cost; identity check
   (rollouts = 4k, sessions = 3k per task in parallel mode); duplicate labels fail loudly.
4. Proposers never see hidden checks or references (leak guard test); proposed trees are validated
   (schema, caps, triggers) and an invalid proposal gets one retry with the validator's message,
   then inherits the base.
5. Offline mode with `ScriptedLLM` and the fake team for the whole loop.

**Tests:** whole loop offline; ledger identity; leak guard; validation retry; adoption rule.
**Docs/Changelog:** HARNESS (tuning), BENCHMARKS. Added.
**Done when:** the offline loop produces a diff and a ledger; the live run is part of T33, within its
$1.50 share of the release cap (`--budget-usd` is required for live tuning).

### Phase E — Quality and scale

#### Task 22 — Acceptance tests first, proven red on the baseline

**Depends on:** 11 · **Model:** O

**Why:** G8. A test that never failed proves nothing, and implementers should not grade themselves.

**Do:**
1. New stage `acceptance` (teammate `quality_engineer`) before `implement` for `feature`/`bugfix`
   profiles: writes tests for each spec criterion into paths the implementers cannot write
   (`WriteScope` deny), in the project's own test style.
2. The controller runs them on the baseline revision: each must **fail** (or error on the missing
   symbol) for the right reason; a test passing on the baseline is rejected and rewritten (bounded).
3. After implementation, those tests must pass and the criteria they cover become `verified` in the
   report (not only `referenced`); the implementers' own tests are reported separately.
4. A stage option to skip (`--no-acceptance`) and automatic skip for `trivial` size.

**Tests:** red-on-baseline rule (good test, test passing on baseline, test erroring for the wrong
reason); write-scope denial; criteria coverage in the report.
**Docs/Changelog:** ARCHITECTURE (verification), USAGE. Added.
**Done when:** a scripted feature run shows criteria verified by tests that were red first.

#### Task 23 — Best-of-N attempts with blind selection

**Depends on:** 13 · **Model:** O

**Why:** G7. Test-time scaling with selection on evidence is where a controller beats a single
agent loop.

**Do:**
1. `pipeline/attempts.py`: `--attempts N` (or the ladder's last rung) runs N attempts in isolated
   worktrees/copies, in parallel within `parallel.max_lanes`, varied by seed, model tier or harness
   variant (`attempts.vary = seed|model|harness`).
2. Each attempt is verified by the controller; only passing attempts enter selection; if none pass,
   the one with the most passing checks is reported (never silently "the best").
3. `pipeline/select.py`: a blind judge (reviewer tier) sees anonymised, seeded-shuffled ids (stable
   across reruns), the request, the diffs and the review findings — not which variant produced them;
   rubric: correctness evidence, minimality, consistency with the codebase, test quality. Judge
   failure → fall back to smallest passing diff, recorded. Ties → smallest diff.
4. The chosen attempt's branch becomes the run's result; the others are kept as patches in the run
   directory; the report shows all of them with their checks and the judge's reason.
5. Ledger for attempts cost; budget pre-check.

**Tests:** scripted attempts (two pass, one fails), anonymisation stability, judge failure fallback,
budget stop, report.
**Docs/Changelog:** ARCHITECTURE, USAGE, CONFIGURATION. Added.
**Done when:** `--attempts 3` works offline with a deterministic, explained choice.

#### Task 24 — Context packs: ranked repo map and per-stage token budgets

**Depends on:** 17 · **Model:** S

**Why:** larger repositories exhaust context and money; the agent should start with the right files.

**Do:**
1. Rank files for a request by a personalised PageRank over the import graph (`codeintel`) seeded by
   paths/symbols from the request, triage focus paths and the reproduction; plus Git recency.
2. `Repo Map` gains `--for-request` ranking and a token budget; each stage gets a context pack
   (ranked map excerpt, memory facts, lessons, skills list) within `context.stage_tokens`.
3. A prompt-size report per stage (always-on, pack, history) in the manifest and the report; a warning
   when a stage starts above 60 % of the model's context window.

**Tests:** ranking on a fixture repo (the buggy module ranks top for a stack trace); budget trimming;
report fields.
**Docs/Changelog:** TOOLS, ARCHITECTURE, CONFIGURATION. Added.
**Done when:** the larger brownfield benchmark fixture starts with the relevant module in the pack.

#### Task 25 — Model research: best-value presets for every provider, OpenRouter

**Depends on:** 1,3 · **Model:** O

**Why:** G15, G12. Model cost dominates a run's cost, and the presets were written once and never
compared. You have OpenAI, Anthropic and OpenRouter keys; open-weight models through OpenRouter
(DeepSeek, GLM, Kimi, Qwen, MiniMax…) can be several times cheaper for worker-tier work, but only if
they call tools reliably — the local models in 0.2.0 failed exactly there.

**Do:**
1. **Research on the day** (do not rely on this plan or on memory; prices and model lines move
   monthly). For each provider read the official model and pricing pages, and for OpenRouter its
   models API (`https://openrouter.ai/api/v1/models`: id, context, prompt/completion/cache prices,
   `supported_parameters` containing `tools`/`tool_choice`/`response_format`) plus each model's
   provider list. Starting candidates to verify, not to assume:
   - Anthropic: `claude-haiku-5-5` (cheap/worker), `claude-sonnet-5-5` (reviewer/lead),
     `claude-opus-5-5` (max). Fix the stale "no model cheaper than Sonnet" preset.
   - OpenAI: `gpt-6-luna` (cheap), `gpt-6.1-terra` (worker? check where it sits on price and
     capability), `gpt-6.1-sol` (reviewer/lead), and any newer line.
   - Google: the current Gemini flash and pro lines.
   - OpenRouter: the newest DeepSeek, GLM (e.g. 5.3), Kimi, Qwen coder, MiniMax, Devstral/Mistral
     models with tool calling; prefer providers that serve full precision and support tool calls.
   Write the findings (id, price in/cached/out per 1M tokens, context, tool support, source URL, date)
   into a table in `docs/CONFIGURATION.md` — that file is the home of presets.
2. **Measure, do not guess.** A small live screen, **≤ $2.80 in total** (§4 cap): estimate first and
   trim the candidate list until it fits; skip the expensive `max`-tier models (their price already
   rules them out of worker tiers). For every candidate run `doctor --probe-tools` (T3) and then the `single`
   strategy on 3–4 benchmark tasks (one greenfield, one bugfix, one brownfield feature; from T7's dev
   split if it is done, the 0.2.0 tasks otherwise), two repeats. Record pass, tool-call failures,
   cost, time per model in `benchmarks/results/<date>/model-screen/` (cheap candidates first; a
   model that fails the probe gets no task runs). A model that fails the tool
   probe or most tasks is not put in any preset, whatever its price.
3. **Add the OpenRouter provider**: `openrouter` in `PROVIDERS` (CrewAI/LiteLLM `openrouter/<vendor>/
   <model>` ids, `OPENROUTER_API_KEY`, optional `OPENROUTER_BASE_URL`, attribution headers off by
   default), `doctor` key check, model facts (context, API style), price rows; OpenRouter's own
   reported cost (`usage.cost`) used when present, the price table otherwise. Provider routing
   options (`provider.order`, `allow_fallbacks`, `quantizations`, `data_collection: deny` default)
   exposed in settings; document that requests go to third-party hosts chosen by OpenRouter
   (SAFETY.md).
4. **Update the presets** from the measurements: per provider (`openai`, `anthropic`, `google`,
   `openrouter`) the cheapest model that passed for `cheap`/`worker`, the best value that passed for
   `reviewer`/`lead`, the strongest for `max`; plus a mixed **`value`** preset that may combine
   providers (e.g. worker on an OpenRouter model, reviewer on Sonnet 5.5) when the keys are present,
   falling back tier by tier to whatever key is configured. Update `pricing.toml` (`as_of` today,
   source URLs) and the tier comment in `model_routing.py`.
5. `engineering-team models` lists presets, resolved models per tier, prices, and which keys are
   present (never the key values); `models --refresh-prices` (live, opt-in) prints the differences
   between `pricing.toml` and the provider pages/APIs for the user to review — it does not rewrite
   files silently.

**Tests:** OpenRouter provider resolution, key check, settings validation, cost from `usage.cost` and
from the table; `value` preset fallbacks with only some keys present; `models` output (keys never
printed); no network in tests (fixtures of the models API response).
**Docs/Changelog:** CONFIGURATION (presets table, OpenRouter, `value`), SAFETY (OpenRouter data
flow), BENCHMARKS (model screen results). Added (OpenRouter, `value`, `models`), Changed (presets).
**Done when:** every preset model cites a screen batch; the cheapest preset that passes the screen
is documented with its measured cost per success.

#### Task 26 — Model routing v2: per-intent routes, fallbacks, prompt caching

**Depends on:** 10,25 · **Model:** S

**Why:** cost and reliability once the presets are right (T25).

**Do:**
1. Routing by intent and size (`models.routes`: e.g. `question/small → cheap`, `bugfix/large →
   reviewer for impact`), on top of tiers and presets.
2. Fallbacks: on provider errors, rate limits or timeouts after CrewAI/LiteLLM retries, move to the
   configured fallback model for that tier (for example an OpenRouter model with
   `allow_fallbacks`, or another provider's equivalent tier); record `model.fallback` events and the
   cost.
3. Prompt caching where the provider and CrewAI support it (stable always-on prefix first); report
   cache hits in usage when the provider returns them.

**Tests:** routing resolution; fallback with scripted errors; usage accounting with cache fields.
**Docs/Changelog:** CONFIGURATION (routes, fallbacks, caching). Added.
**Done when:** a scripted rate-limit error falls back and the report shows it.

#### Task 27 — Optional LSP bridge: diagnostics and precise references

**Depends on:** 2 · **Model:** S

**Why:** deferred from 0.2.0; precise references and diagnostics matter on large, typed codebases.

**Do:**
1. `codeintel/lsp.py`: a minimal JSON-RPC client over stdio, process started through
   `ExecutionBackend` (works in Docker); servers by language (`pyright-langserver`/`basedpyright`,
   `typescript-language-server`, `gopls`, `rust-analyzer`) when installed.
2. Tools `Diagnostics` (file or project, bounded), `Go To Definition`, precise `Find References`
   (falls back to the current heuristic tool when no server is available, saying so).
3. Lifecycle: one server per run and language, started lazily, killed on run end and cancel.

**Tests:** a fake LSP server script speaking the protocol; fallback path; cancellation.
**Docs/Changelog:** TOOLS (remove "Not included"), CONFIGURATION. Added.
**Done when:** with pyright installed locally the tools work (manual check recorded), tests offline.

### Phase F — Integrations

#### Task 28 — GitHub: issue intake and opt-in draft pull request

**Depends on:** 14 · **Model:** S

**Why:** G14. The common loop is issue → branch → PR.

**Do:**
1. `do --issue owner/repo#N` (or an issue URL) reads the title, body and comments through `gh`
   (must be installed and authenticated; never handles tokens itself) and uses them as the request;
   the issue is cited in the report.
2. `--open-pr` (never default; also `github.open_pr = false` in settings): after a passing run, push
   the run's branch to `origin` and open a **draft** PR whose body is the Markdown report summary
   (verdict, checks, criteria, diff stats, cost) with a link to the full report artifact path. Asks
   for confirmation unless `--yes`.
3. Without `--open-pr` nothing is pushed — unchanged 0.2.0 behaviour.

**Tests:** a fake `gh` on `PATH`; PR body rendering; no push without the flag.
**Docs/Changelog:** USAGE, SAFETY (what is sent where). Added.
**Done when:** fake-`gh` end-to-end test passes.

#### Task 29 — GitHub Action: review pull requests, fix on a label

**Depends on:** 28 · **Model:** S

**Why:** reach — the review mode already writes `findings.json` for CI.

**Do:**
1. `action.yml` (composite) at the repo root: installs the package with `uv`, runs `review` on the
   PR diff, uploads findings as SARIF (`review --format sarif`) and posts a summary comment;
   inputs: provider, model preset, budget, fail-on severity.
2. Optional workflow example: on label `engineering-team:fix`, run `do --issue` on the linked issue
   with `--open-pr` into a branch, in Docker sandbox mode.
3. Secrets via repository secrets only; the docs state cost and permissions needed.

**Tests:** SARIF rendering; `action.yml` schema check (static); example workflow lint.
**Docs/Changelog:** USAGE (CI), SAFETY. Added.
**Done when:** the action runs on this repository's own PRs in fake mode (`--fake` model) in CI.

#### Task 30 — MCP server: let other agents delegate to the team

**Depends on:** 14 · **Model:** S

**Why:** Claude Code, Cursor and Codex users can hand verified, long-running work to the team without
leaving their tool.

**Do:**
1. `engineering-team mcp serve` (stdio; optional extra `mcp`): tools `start_task(request, repo,
   mode?, budget?)`, `task_status(run_id)`, `task_report(run_id, format)`, `answer(question, repo)`,
   `cancel(run_id)`, `list_runs()`; runs start as subprocesses like the UI does.
2. Safety: the server only acts inside repositories passed explicitly or configured; budgets apply;
   no secrets in results.
3. Docs with config snippets for Claude Code and Cursor.

**Tests:** the MCP handlers called in-process with a fake runner; schema of tool definitions.
**Docs/Changelog:** USAGE, SAFETY. Added.
**Done when:** handlers tested offline; a manual check with an MCP client recorded.

### Phase G — UX

#### Task 31 — Web UI v2: one textbox, triage preview, threads, attempts, learning views

**Depends on:** 14,15,16,18,20,23 · **Model:** S

**Do:**
1. New-run page: one textbox (+ file upload, repo picker), live triage preview from a new
   `POST /triage` endpoint (intent, size, rung, stages with reasons, estimate), optional overrides.
2. Results: answer rendering (T15), escalation timeline (T13), attempts comparison with the judge's
   reason (T23), follow-up box starting `continue` (T16), thread navigation.
3. Learning views: resolved harness and skills loaded for a run; repository memory list with forget;
   pending lessons with approve/reject.
4. No business logic in JS (0.2.0 rule): the server computes every view.

**Tests:** API tests for the new endpoints; `scripts/ui_smoke.py` extended; demo mode covers triage,
escalation and attempts.
**Docs/Changelog:** USAGE (UI), screenshots regenerated. Added.
**Done when:** `ui --demo` shows every new state.

#### Task 32 — Public website on GitHub Pages: landing, docs, leaderboard, live demos

**Depends on:** 8,31 · **Model:** S

**Why:** G13. A public page is what a visitor (or a recruiter) sees first; it should show what the
team does, prove it with numbers, and link to everything else without duplicating the docs.

**Do:**
1. **Site generator** (MkDocs Material, `site` dependency group; `mkdocs.yml` at the root): the
   pages are the existing `docs/*.md` (nav only, no copies) plus a **landing page**
   (`docs/index.md`: one-line pitch, the demo GIF, "how it proves done", install, three example
   requests with their reports), a **leaderboard** page and an **examples** gallery.
2. **Leaderboard** generated, not hand-written: `scripts/build_leaderboard.py` reads every
   committed `benchmarks/results/<date>/**/results.json` (T8 schema) and writes
   `docs/leaderboard.md` (+ a small JSON for a sortable table): per strategy/agent/preset the pass rate
   with intervals, cost per success, time, task count, date, versions, and a link to the raw batch on
   GitHub. Rows without enough runs are shown greyed with "n < 10". The 0.2.0 results appear too,
   labelled with their version.
3. **Example reports** hosted as-is: the self-contained HTML run reports from `examples/` and the
   live ones (step 5) are copied into the site build, so each links to a real report page.
4. **Deployment:** `.github/workflows/pages.yml` builds on pushes to `main` that touch `docs/`,
   `examples/`, `benchmarks/results/` or `mkdocs.yml`, runs `check_docs.py` and `mkdocs build
   --strict`, and deploys with `actions/upload-pages-artifact` + `actions/deploy-pages`. Document the
   one-time step for the user (repository Settings → Pages → Source: GitHub Actions) and the site URL
   (`https://alexandre0sheva.github.io/crewai-engineering_team/`) in the README and `pyproject` URLs.
   The workflow does not run on forks' pull requests with secrets; it needs none.
5. A recorded terminal demo (`vhs` tape committed, GIF generated) of `do` on a bug in a real small
   repository; if tools are missing locally, commit the tape and leave rendering to the user.
6. Replace scripted example reports with **live** ones (made by the executor, §4 *Who runs live*) (≤ $1.00 together with the recording;
   cheapest passing preset; clearly labelled with model, cost, date),
   keeping the scripted ones for offline regeneration.
7. README: lead with `do`, link the site and the leaderboard, an architecture diagram; the README
   carries at most a three-row summary of the leaderboard, generated by the same script.

**Tests:** `build_leaderboard.py` on fixture results (ordering, intervals, small-n marking, links);
`mkdocs build --strict` in CI; `check_docs.py` covers `mkdocs.yml` nav entries.
**Docs/Changelog:** README, CONTRIBUTING (how to rebuild the site and leaderboard locally),
documentation map row for the site. Added (website, leaderboard).
**Done when:** the site builds in CI from a clean checkout and the leaderboard renders the 0.2.0
results; publishing is one push by the user.

### Phase H — Release

#### Task 33 — Live evaluation 0.3.0 and defaults decision *(live runs by the executor)*

**Depends on:** 8,13,20,23,25,32 · **Model:** S

**Do:**
1. **Budget: ≤ $9.70 for this task, inside the $15 release cap.** The executor makes every run,
   after asking the user to check provider balances (§4 *Who runs live*). Check `bench spend` first; use
   what T3/T10/T25/T32 left over only with the user's OK. Before any run, write the evaluation
   matrix with `bench plan --estimate` and fit it to the budget, roughly: held-out split first,
   two repeats, cheapest passing presets for worker tiers, `--attempts 3` on a subset only,
   `pipeline`/`team` rungs only where `auto` escalates; target ≈ $6.70 matrix, ≈ $1.50 tuning, ≈ $0.50
   learning check, ≈ $1 margin. Run benchmark v2 for `auto`, `single`, `pipeline`, `--attempts 3`,
   and the external agents available on this machine (Claude Code, Codex,
   OpenCode, Aider), with the presets from T25 (`openai`, `anthropic`, `openrouter`, `value`), so the
   README can state cost per success per preset.
2. Harness tuning (T21) live on **one** domain with `k=1` and its held-out confirmation (≤ $1.50,
   or skip it and say so); learning on/off on two repeated tasks (≤ $0.50). Triage accuracy comes
   from T10's run unless triage changed since.
3. Set the defaults (ladder thresholds, attempts policy, learning review mode, routes) from the
   data; record every decision with its batch in the Decision log; commit results under
   `benchmarks/results/<date>/`.
4. BENCHMARKS.md: results, every failure and its cause, caveats; README comparison table, honest.
5. **Publish:** every batch is written under `benchmarks/results/<date>/<batch>/` (`report.md`,
   `results.csv`, `results.json`, `batch.json` with versions, models, commands and task digests, one
   `result.json` per run; transcripts only when small and free of secrets — run the secret scan on
   them first). Run `scripts/build_leaderboard.py`; the user reviews, commits and pushes, and the
   Pages workflow (T32) publishes the leaderboard. Nothing is pushed by the task.
6. **How to read it** (stated on the leaderboard page): "better" or "worse" than an external agent is
   claimed only where the pass-rate intervals do not overlap or the paired per-task comparison is
   significant on the held-out split; otherwise the page says "not distinguishable at this sample
   size" and shows cost and time differences, which usually are.

**Done when:** defaults cite batches; the leaderboard and README claims match the numbers and their
caveats.

#### Task 34 — Docs overhaul, PyPI publishing, release 0.3.0

**Depends on:** all · **Model:** S

**Do:**
1. Docs pass over every file in the Documentation map (one home per fact); `docs/HARNESS.md`
   complete; migration notes from 0.2.0 in CHANGELOG.
2. PyPI trusted publishing in `release.yml` (the user configures the PyPI project and approves);
   install instructions switch to `uv tool install engineering_team`.
3. Version `0.3.0`, CHANGELOG section dated, release notes via `scripts/release_notes.py`; prepare
   the tag command for the user (do not tag or publish).

**Done when:** gate green, `uv build` wheel installs in a clean venv and `engineering-team do --help`
works from another directory; the user has the exact tag and publish steps.

---

## 7. Decision log

*(Executors append here: date · task · decision · reason. Deviations from this plan belong here.)*

| Date | Task | Decision | Reason |
|------|------|----------|--------|
| 2026-10-28 | plan | The 0.2.0 plan is removed from the tree; deferred items moved into this plan; history via `git show 31bd355:docs/IMPLEMENTATION_PLAN_0.2.0.md` | One live plan avoids "implement task N" ambiguity |
| 2026-10-28 | plan | `do` with triage and a composed recipe replaces mode selection; the 0.2.0 modes stay as presets through 0.3.x | Universal entry point without breaking existing users |
| 2026-10-28 | plan | Escalation ladder starts cheap (`single`) for small tasks; thresholds provisional until T33 | 0.2.0 benchmark: same pass rate at 1/25–1/39 of the cost |
| 2026-10-28 | plan | Harness² ideas ported with controller-verified signals instead of model-only judgement; no Harness² code is vendored | This project has ground truth Harness² lacks; Apache-2.0 attribution only if code is ever copied |
| 2026-10-28 | plan | Model research (now T25) inserted after the first draft, then T5, T6 and T12 (speed and cost) inserted; the plan has 34 tasks | User request: best-value models per tier, OpenRouter open-weight models; all tasks were still `todo` |
| 2026-10-28 | plan | The team becomes a lead agent that does the work and calls teammates on demand (T12); the static pipeline is kept for large new projects | 0.2.0 pipeline: 117 calls and 1.71M prompt tokens vs 14 and 138k for `single` on the same tasks; `hierarchical`-style manager delegation already failed, so the lead works itself and delegation is capped |
| 2026-10-28 | plan | Speed and cost targets in §2 enforced offline by perf proxy tests (T5) | A regression in sessions or calls should fail the gate, not wait for a paid benchmark |
| 2026-10-28 | plan | All 0.3.0 live spending capped at $15 (API-billed), allocated per task and enforced by a ledger in the bench commands | User's budget; subscription-billed external agent runs are tracked but not counted |
| 2026-10-28 | plan | Results are published as committed files plus a generated leaderboard on GitHub Pages; claims vs external agents only where the held-out data separates them | User wants public, reproducible results; 0.2.0 already commits raw batches |
| 2026-10-28 | plan | Presets are chosen by a live screen (tool-call probe + small task set), never by price or leaderboards alone | 0.2.0 local models failed on tool calling; cheap is worthless if it cannot drive the tools |
| 2026-10-28 | plan | `hierarchical`, the bare 0.1.0 invocation and CrewAI `train`/`replay`/`test` scripts are removed in 0.3.0 | 0/3 measured runs finished; the compatibility promise ended at 0.3.0 |
| 2026-10-28 | T1 | CrewAI `>=1.15.31,<1.16` (1.15.31 is a day old on 2026-10-28; the plan named it, its changelog 1.15.24–1.15.31 shows no breaking change). Python stays `>=3.11,<3.14`: CrewAI 1.15.31 still declares `<3.14`, so no widening of `requires-python`, classifiers, CI matrix or badge | Live PyPI and changelog beat the plan; widening needs CrewAI support |
| 2026-10-28 | T1 | Left behind after `uv lock --upgrade`: `pydantic` 2.12.5 (CrewAI: `<2.13`), `rich` 14.3.4 (`instructor` caps `<15`, so our `rich` stays `<15`), `openai` 2.x, `mcp` 1.x, and `aiofiles`, `aiosqlite`, `json-repair`, `json5`, `portalocker`, `regex`, `tomli`, `tomli-w`, `pymupdf` (CrewAI pins them `~=`/`<`) | All CrewAI-constrained transitives; nothing we own is behind |
| 2026-10-28 | T1 | Docker defaults: `python3.13` uv image, `node:24-slim` (24 is the current LTS; `lts-slim` points at it), `golang:1.27`, `eclipse-temurin:25-jdk`, `dotnet/sdk:10.0`, `ruby:4.0-slim`, `php:8.5-cli`; `rust:1-slim` unchanged | Tags checked on Docker Hub and MCR on 2026-10-28. The uv image is the newest Python in CrewAI's range; `astral/uv` images were last rebuilt in February 2026 |
| 2026-10-28 | T1 | Actions: `setup-uv` v10.3.2, `upload-artifact` v7, `download-artifact` v8, `checkout` v7 (already current), `pypa/gh-action-pypi-publish` stays on its documented `release/v1` branch (latest v1.14.3). Playwright's Chromium is installed by the pinned `playwright` 1.64.1; `.python-version` stays 3.12 | Latest releases on GitHub on 2026-10-28; the artifact actions are not run by the gate, so CI is their first test |
| 2026-10-28 | T1 | `engineering_team.crew` is deleted; `build_llm` moved to `engineering_team/llm.py`. `engineering_lead` is removed as a teammate, but `"engineering_lead"` stays the model-routing role of the `lead` slot (`LEAD_ROLES`, `ENGINEERING_LEAD_MODEL`, `doctor`, `bench/estimate`) | The task says `import engineering_team.crew` fails; the lead model slot is still how the `lead` tier is configured and renaming it breaks users' settings |
| 2026-10-28 | T1 | `allow_delegation` stays an accepted, ignored teammate field; `RunManifest.strategy` and `RunRecorder.begin` now default to `pipeline`; `STRATEGY_NAMES` is `("pipeline", "single")` and `RETIRED_STRATEGIES = ("hierarchical",)` makes `resume` explain the removal | Existing team files keep loading; 0.2.0 manifests always carry `strategy`, so the old default had no readers |
| 2026-10-28 | T1 | Bare `engineering-team` with no arguments and no `./PROJECT_REQUEST.md` still prints help (exit 0); options without a command, or a `PROJECT_REQUEST.md` with no command, print one line and exit 2. `train`, `replay`, `test`, `run_with_trigger` are removed; `run_crew` runs `new` with `sys.argv[1:]` | Help on a bare call is friendlier and was not a 0.1.0 form |
| 2026-10-28 | T1 | Historical benchmark results under `benchmarks/results/2026-10-04/` and the `docs/BENCHMARKS.md` result tables still name `hierarchical` | They are the measurements that justified the removal |
| 2026-10-28 | T1 | No tags are made or documented for 0.1.0/0.2.0, and the project version stays `0.2.0` until the 0.3.0 release commit | The user tags 0.3.0 when ready; `tests/test_release_notes.py` requires a dated changelog section for the `pyproject.toml` version, so the bump belongs to the release commit (CONTRIBUTING, Releasing) |
| 2026-10-28 | T2 | Besides the five modules the task names, `board/store.py` (495 lines) was split too (`board/files.py` mixin, `board/limits.py`); the size guard `tests/test_module_size.py` has an empty allowlist | Everything over 450 lines had to go for the allowlist to stay empty |
| 2026-10-28 | T2 | Each split keeps the old import path: `settings/__init__.py` re-exports every public name; `main.py` keeps the helpers the CLI calls as `engine._x` (with `noqa`) and the names tests import; `git/port.py`, `report/html_sections.py` and `tools/registry.py` re-export theirs. `GitPort` is a chain `GitCore` → `GitReads` → `GitWrites` → `GitPort` (writes use reads such as `changes`); `StageExecutor` is `ExecutorBase` → `PackageExecutor` → `StageExecutor` | No caller, plugin or doc import changed; mixin chains keep `self.` calls type-checked |
| 2026-10-28 | T2 | `main._run_prepared` and `main._report` were deleted: T1 had removed their last caller (`run_with_trigger`). `Settings.with_overrides` imports the loader lazily (`settings.loading` builds `Settings`). `test_only_settings_and_subprocess_code_read_the_process_environment` now names `loading.py` | Dead code; one deliberate import cycle break; the env-reading code moved with the loader |
| 2026-10-28 | T3 | Overlapping globs get no repair: `plan_problems` has never rejected them (`packages.schedule` runs overlapping packages one after the other), and the glob language has no negation to narrow "A contains B" with. The only mechanical rejection is shared root files, so that is what `plan_repair.py` fixes; the "unrepairable" test uses a glob that stays on a shared file when narrowed (`**`) and a plan that would lose every package | The task text assumed overlap was rejected; the code shows it is not. Live code beats the plan |
| 2026-10-28 | T3 | A package whose paths are all shared files is dropped, not rejected, and its dependents inherit its dependencies (two of the three benchmark plans were exactly that: `WP-1: README.md`). Its criteria are not moved: nothing in the controller requires every criterion to be delivered by a package (`criteria_ids` only labels cards), and foundation/integrate write the files it named | Without this the notes-cli and markdown-html failures from G6 would still be rejected |
| 2026-10-28 | T3 | Zero-price cause: the bridge logged a call made without an agent under the bare model name (`llama3.2`), because it took the provider only from `event.from_agent.llm`; CrewAI's event source is the LLM itself, so the provider is now read from it. `ollama/*` and `hosted_vllm/<model>` overrides then match | Reproduced with a scripted event; pricing itself was correct |
| 2026-10-28 | T3 | `Recipe.mode` (validated against `MODES`) is excluded from the recipe digest so runs started under 0.2.0 still resume; `Roster.mode` is set once by `PipelineFlow.begin`. `adopt` and `review` have no mode (every teammate may take part) | `Roster.assign` already filtered on `modes` but no caller passed one |
| 2026-10-28 | T3 | "Model known not to call tools" is the static `ModelFacts.tool_calling = False`, set only for the two models BENCHMARKS.md measured (`ollama/gemma3:270m`, `hosted_vllm/deepseek-r1-distill-llama-8b-mlx`); the check applies to a stage whose teammate can write and that made no tool call, and the error is `NoRetry` so the stage's own `retry` does not run it again. T25's screen is where more models get measured | A probe result is not persisted (no state outside the run); guessing from the provider would fail capable local models |
| 2026-10-28 | T3 | `doctor --probe-tools` was built and tested offline only; it was not run live, so none of T3's $0.10 allocation is spent | No credentials are read in this session; run `engineering-team doctor --probe-tools` to spend it |
| 2026-10-28 | T4 | `tool.call` events carry the link as `trajectory_seq` (the event's own `seq` is its line in `events.jsonl`); the key is absent when nothing was written | A second `seq` in `data` would be confused with the event's |
| 2026-10-28 | T4 | Tool arguments are kept in the trajectory in full (each string cut at 2,000 characters), unlike the event log, which logs file bodies as sizes | Reflection and tuning need to see what was written; the file is capped and scrubbed |
| 2026-10-28 | T4 | Model messages come from the CrewAI bridge: each `llm.call` response that is text (a reply to a ReAct prompt; native tool-call replies are structures and are skipped) and each `agent.completed` output (`role` = `final`). `bind_run` takes the recorder as an optional third argument. The trace orders entries by time within a stage, because handler threads record a reply after the tool call it asked for | Native tool-call turns have no text to show; the call itself is the entry |
| 2026-10-28 | T4 | Past `trajectory.max_mb` a `notice` entry is written and outputs, arguments and messages are cut to 1,000 characters; at 1.25 times the budget one more notice is written and recording stops. `[trajectory]` is config-file only (no environment variables) | The task asks for harder truncation and a report note; a hard stop bounds a runaway run |
| 2026-10-28 | T4 | No CrewAI API is newly used (the bridge handlers already existed); the installed version was checked (1.15.31, the version AGENTS.md records as verified today) and the PyPI and docs pages were not fetched again | Nothing new to look up |
| 2026-10-28 | T5 | A session is one `agent.started` to `agent.completed`/`agent.error` of an agent in a stage and lane; sessions are rebuilt from `events.jsonl` and `trajectory.jsonl` at the end of the run (and by `profile` for any run), not tracked live | The log is the one source, so crashed or running runs profile too; the bridge already delivers the events |
| 2026-10-28 | T5 | The bridge emits a new `llm.request` event per model call with `prompt_chars` (system, tools, history, results); prompt parts are estimated tokens (4 characters each) shown beside the billed prompt, not scaled to it | Billed usage has no split; scaling would hide the estimate. Cached tokens come from the provider's usage |
| 2026-10-28 | T5 | "Critical path" is the longest chain of non-overlapping sessions; "sequential stages" are stages whose sessions never overlapped, reported as candidates for parallel lanes, not proof | Whether sessions could overlap depends on the plan's dependencies, which the profile does not read |
| 2026-10-28 | T5 | The perf scenarios are a small and a 4-package `pipeline` run, a `single` run and a small `fix` run (seeded bug in a git repository), all through the real stage runner on `ScriptedLLM` | Measures sessions and calls of the real agents; the `fix` scenario scripts the map analyst, the debugger (triage, reproduce, fix) and both reviewers |
| 2026-10-28 | T5 | Live baseline run on the `smoke` profile (`gpt-6-luna` for all, minimal team), `notes-cli`, `single` and `pipeline`: $0.035 estimated of T5's $0.50 (both passed). The `standard` profile was not run: its `pipeline` run is estimated at $1.01 (range $0.53–2.11), over the allowance | Stays inside the cap; the figures do not reproduce 0.2.0's 9×/25–39× (those used the stronger model for the lead and reviewers): 4.9× time, 6.4× cost. A `standard` baseline needs the user's OK (reserve or T33's allocation) |
| 2026-10-28 | T5 | Measured today (scripted): `pipeline` small = 11 sessions, 19 calls, 12,102 tool-schema tokens per call, 3 ceremony stages; medium = 13 / 23; `single` = 1 / 4. The schema size is twice the 6,000 target | Where T6's tool-schema diet starts |
| 2026-10-28 | T5 | The live run showed `cached_prompt_tokens: 0` in `result.json` because the team's printed summary has no cached count; `bench/execution.py` now reads it from the event log, and the two `result.json` files, `results.csv` and `report.md` of the committed batch were regenerated with it | A bug in the field this task added; the batch is otherwise as the harness wrote it |
| 2026-10-28 | T5 | `profile` and the report share out the billed prompt over the estimated parts (system, tools, history, results) instead of printing raw estimates | Characters over four overstate JSON schemas: the raw parts summed to more than the billed prompt |
| 2026-10-28 | T5 | `benchmarks/results` is excluded from Ruff | A batch's workspaces hold the code the team wrote, which is a record, not source |
| 2026-10-28 | T6 | Task size (small/medium/large) comes from deterministic signals until T10's triage exists: request over 600 words → large; an existing project over 40 files → medium, over 400 → large; once the plan exists 3+ packages → medium, 6+ → large (never smaller); `lean.size` overrides. Provisional until T33 | Nothing else exists before the first model call; the thresholds are guesses marked as such (§4 rule on defaults that change cost) |
| 2026-10-28 | T6 | Spec and plan merge through a `Design` contract (`spec` + `plan`) returned by one `design` session (`design_change` in `feature`); the runner splits it, so the executor's spec settling and plan repair are unchanged. The controller writes `docs/plan.md` from the plan instead of the architect writing `docs/architecture.md` (large tasks keep the architect's document) | One contract per answer is CrewAI's `output_pydantic` model; a rendered plan costs no tool call |
| 2026-10-28 | T6 | A small task's plan is collapsed by the controller into one package with the union of the packages' paths and criteria (`plan.collapsed`); a one-package plan skips `foundation` and `integrate` (`single_package`) and its package runs unscoped with the recipe's new `solo_prompt` (`implement_solo`). "Small" then reaches 3 sessions (design, build, one review) | The plan names one mechanism (single-package plans); collapsing reuses it instead of a second code path. The perf scenario's two-package plan needed it |
| 2026-10-28 | T6 | The `release` agent stage is replaced by a controller stage `release_notes` that writes `docs/release-report.md` from the records; `devops`/`docs` run when the request matches a keyword list (CI, Docker, containers, deployment, Kubernetes; documentation, docs, guide, tutorial, manual) or `lean.ceremony = "always"` | Item 3 (controller where no judgement is needed); the release session was the most expensive one in T5's live profile ($0.0074 of $0.0300) |
| 2026-10-28 | T6 | Small repository modes: `map` skips on `small_task` (fix and feature), `triage` skips on `small_task` (fix: the reproduce prompt says to find the cause itself when there is no triage). Small low-risk review: only `code_reviewer`, on the worker tier; the merged design runs on the worker tier unless large or high-risk (a keyword list: auth, passwords, secrets, tokens, crypto, payments, uploads, SQL, permissions, sessions, cookies, OAuth, JWT, PII). Provisional until T33 | Needed for the fix scenario's 3-session target; the 0.2.0 measurement that put the architect on the reviewer tier (3 of 11 broken plans) was on plans with several packages, which are now medium |
| 2026-10-28 | T6 | `max_iter` per stage was not lowered | T5's live profile (`smoke`, worker `max_iter` 14) shows four of seven sessions ending at 15 calls, i.e. at the cap: the evidence points the other way, so it waits for T33's measurement |
| 2026-10-28 | T6 | The tool diet is `Find Tool` (describes held-back tools) plus `Use Tool` (calls one by name), not real tools added to the CrewAI executor mid-session. The 14 always-sent tools (`EAGER_TOOLS`) are the ones T5's live profile shows in use; every other built-in tool is `lazy`; plugin tools never are. Per-stage tool-group tables were not added: with the diet every teammate is under 6,000 schema tokens (3,234 for the builders) whatever its groups | A stable tool list keeps the prompt prefix cacheable (item 6); adding tools mid-session needs CrewAI private executor methods. `Use Tool` is a second tool the plan did not name |
| 2026-10-28 | T6 | The read cache is on `Read Project File` only, per tool bundle (one bundle per agent session); `Read File Range` is never cached so a compacted conversation can get lines back | The plan names "Read File"; a range read is the escape hatch |
| 2026-10-28 | T6 | Hand-off pack: up to 60 files (most recently changed, listed by path, size and SHA-256 prefix), the latest checks, one line per earlier stage; placed in `{handoff}` just before `{card}` in every prompt. The plan is already in `{plan}`; memory facts wait for T19 | Item 4 without duplicating the contracts the prompts already carry |
| 2026-10-28 | T6 | Prompt weight: contracts go into prompts as compact JSON without `schema_version`, and `Contract.model_json_schema` leaves out `schema_version` and per-property titles (CrewAI appends the schema to every structured task). A test checks that the changing placeholders come last in every prompt | The merged design prompt was 2,735 tokens per call, over the 2,500 always-on proxy; now 2,250 |
| 2026-10-28 | T6 | Tool failures from T5's profile fixed: bare `@@` hunk headers (placed by unique context), a card move to its current state (a no-op), an absolute path inside the workspace (made relative), a refused command (lists the allowed programs and says shell builtins never are). Write-scope refusals and path typos already named the allowed paths and suggestions | The top error kinds of the live run (11 failures) |
| 2026-10-28 | T6 | `Recipe.digest` leaves out `solo_prompt` when it is unset, so the recipes T6 did not change keep their 0.2.0 digests; `new`, `feature` and `fix` changed, so a run of them started under 0.2.0 cannot be resumed (CHANGELOG migration note) | Resume compatibility where it costs nothing |
| 2026-10-28 | T6 | Item 9 (review and docs in parallel; incremental checks, touched package first) is not done: `docs` no longer runs unless asked, the review's repair round re-verifies and may change files the writer reads, and checks per package need the plan-to-check mapping T12/T13 introduce. Deferred to T12/T13 | Overlapping a stage that can repair with one that writes needs the lead agent's scheduling |
| 2026-10-28 | T6 | Existing pipeline tests that assert the 0.2.0 stage list run with `ENGINEERING_LEAN_SIZE=large` and `ENGINEERING_LEAN_CEREMONY=always`; tests that call held-back tools by name build tools with `lazy=False` | They test the full pipeline and each tool, which still exist; the lean default has its own tests |
| 2026-10-28 | T6 | Live before/after (T5's leftover, user's OK in chat): `notes-cli`, `smoke`, `pipeline`, one run, `benchmarks/results/2026-10-28/perf-lean/`: passed; $0.0043 (was $0.0300), 80 s (was 382 s), 2 sessions (was 7), 15 calls (was 84), 77,575 prompt tokens (was 1.11M). Spent about $0.004 of T5's $0.465 leftover | The task's "Done when"; BENCHMARKS.md has the per-session comparison |
| 2026-10-28 | T6 | The live run's three failed calls were `Use Tool` with the held tool's arguments beside `name` (dropped by the schema); `Use Tool` now takes arguments beside the name or as JSON text | Seen in the live trajectory; a regression test scripts that form |
| 2026-10-28 | T7 | `split` replaces `subset` in `task.yaml` and `result.json` (schema 2); 0.2.0 task files and results still read (`subset` → `split`, `language` → `languages`, a missing `intent` derived from the mode). `--subset` stays as an alias of `--split` | The plan names the field `split`; old batches and third-party suites keep working |
| 2026-10-28 | T7 | Split: the 0.2.0 tasks keep theirs (their dev tasks were used to choose 0.2.0's defaults, so moving one to held-out would contaminate it); 7 of the 12 new tasks are held out, 9 of 22 (41 %) | ≥ 40 % held out without turning tuned-on tasks into "unseen" ones |
| 2026-10-28 | T7 | Check outcomes: exit 0 pass, 1 fail, anything else `error`. Only an explicit `CheckError` (missing toolchain, Docker down) or a missing check function is an error; a check that crashes stays a **fail** | A crash usually comes from the team's output (a missing key, bad JSON); counting it as `error` would drop real failures from pass rates |
| 2026-10-28 | T7 | Run verdict: a failed required criterion makes the run `failed` even if another errored; otherwise any errored one makes it `error` (not counted, rerun by `--resume`) | A fail is definite evidence; an error is the absence of a judgement |
| 2026-10-28 | T7 | Node/Go checks run their tools through `checklib.toolchain`: Docker images (`benchmarks/images/`, no network, workspace mounted at `/opt/bench/app` next to the image's `/opt/bench/node_modules`) by default, or `--acceptance-sandbox local` with `--node-modules DIR`. HTTP checks of the Express task run a driver script inside the toolchain (loopback only), not from the host | `--network none` makes host-to-container requests impossible; Node resolves packages one directory up, so no symlinks in the image |
| 2026-10-28 | T7 | Pinned npm packages for the Node image: express 5.3.0, react/react-dom 19.3.1, vitest 5.0.5 (all published 2026-10-09 to 10-15); Go image `golang:1.26-bookworm`. Validated here with `--acceptance-sandbox local` (Node 24.14, Go 1.26.10 arm64 downloaded to the session scratchpad with the user's OK); the Docker images were written but not built (the daemon was not running) | No network at check time; the user approved the downloads in chat |
| 2026-10-28 | T7 | Mode per new intent until `do` (T14) and the answer path (T15) exist: review → the `review` command (`--base main --out-dir <workspace>/review-out`, the change committed on branch `change`); question → `maintain docs` writing `ANSWERS.md`; upgrade → `maintain custom` (the `upgrade-deps` recipe is manifests-only, a breaking API needs code changes); ambiguous request → `new` (non-interactive runs record their assumptions in `docs/spec.md`) | Today's commands can run every task; T14/T15 can switch the `mode` field without touching the checks |
| 2026-10-28 | T7 | Ambiguity, answers and reviews are scored by deterministic checks, not a model judge: the stated reading of "sorted by name" is the one named in more sentences that tie sorting to one reading (sentences naming both state the question); answers need key facts plus a `path:line` within ±2 lines of the answering code; reviews match findings by file and line within 3 lines (or wording when there is no line), recall ≥ 3/4, precision ≥ 0.5 over medium-or-worse findings, repeats not counted | Offline, free and reproducible; thresholds are generous on wording and strict on location |
| 2026-10-28 | T7 | The ledger also refuses a live batch whose `--budget-usd` is larger than what is left in its allocation (besides refusing when the estimate does not fit), and a batch's actual cost is `null` (charged as the estimate) when any run that used a model has no price. Past live spend was recorded from the committed batches: T5's baseline ($0.0347) and T6's rerun on T5's allocation ($0.0043) | The estimate is a prior; only the cap bounds what a batch can spend. Starting the ledger from the real numbers |
| 2026-10-28 | T7 | `bench triage` and `bench tune` do not exist yet (T10, T21); `SpendLedger.check`/`record` and `cli/bench_spend_command.check_spend`/`record_spend` are what they will call | Built once, here, as the task asks; the callers come with their tasks |
| 2026-10-28 | T7 | `cli/bench_command.py` (430 lines) was split: `bench_options` (suite/out options), `bench_report_command` (`report`, `--dry-run`), `bench_spend_command` (`spend`, ledger checks) | Module size rule |
| 2026-10-29 | T8 | Flags checked against `--help` on 2026-10-29: Claude Code 2.1.287 `claude -p PROMPT --output-format json --permission-mode bypassPermissions --no-session-persistence [--model] [--max-budget-usd]`; Codex 0.162.1 `codex exec --json --color never --skip-git-repo-check --ephemeral --sandbox danger-full-access -C DIR [-m] -- PROMPT`; OpenCode 1.19.4 `opencode run --format json --auto [-m provider/model] PROMPT`. Aider was not installed, so `aider --message PROMPT --yes-always --no-pretty --no-stream --no-auto-commits --no-check-update --no-analytics [--model]` and its `Cost: $x message, $y session` line follow its documentation and are unchecked. No agent was run live (no spend); the JSON field names Claude Code, Codex and OpenCode print (`total_cost_usd`, `turn.completed.usage`, `step-finish.cost`) are from their documentation and the parsers tolerate absent ones, so the first live run (T33) must confirm them | The task asks for flags verified at implementation time; live runs cost money or a plan's quota and belong to the ledger |
| 2026-10-29 | T8 | Permission flags are the unattended ones (`bypassPermissions`, `danger-full-access`, `--auto`, `--yes-always`); the workspace is a fresh copy but the process is not sandboxed. External agents read the user's own configuration ("its own default settings"), which can differ between machines | A headless run cannot answer prompts; the plan asks for default settings. Documented in BENCHMARKS so it is not mistaken for a sandbox |
| 2026-10-29 | T8 | An external agent's contender name is its `strategy` (`claude-code`, ...) and also `agent`; `RunRecord.contender` returns whichever is set, so run directories, `--resume`, ordering and every report table work unchanged. `result.json` schema 3 adds `agent`, `agent_version`, `billing`, `equivalent_cost_usd`; schema 1 and 2 files read as team runs | One record schema for both, as the task asks, without a second directory layout |
| 2026-10-29 | T8 | Review tasks are not run by external agents (`applies`: `mode != review`); the report lists them as not applicable. The agent request is the request text plus a fix task's stack trace and reproduction command as text; `maintain` presets are not passed | `review` ends in `findings.json` through a team-only command and the presets add instructions: giving them to one side would make the comparison unfair, adding a findings protocol for each agent is out of scope. Reviews stay a team-only measure until a later task needs more |
| 2026-10-29 | T8 | Billing is detected from the environment (API key or cloud switch means `api`, none means a plan for Claude Code and Codex; OpenCode is `unknown`, Aider `api`) and overridable with `--agent-billing`. A plan run has `cost_usd = null` plus `equivalent_cost_usd`; the ledger gets a `billing: subscription` line (0 charged); `unknown` is charged like `api`. A paid agent's ledger estimate is its per-run cap, since nothing prices an agent beforehand, and only Claude Code can be capped on its command line | A wrong guess in either direction would hide spend or block free runs, so it is shown in the report header and overridable; the plan says a plan is never $0 and never counted |
| 2026-10-29 | T8 | `--fake` makes only the team scripted; `--fake --agent X` runs X for real (tests put fake binaries on `PATH`), and such a report says "Mixed batch". `results.json` is written by `bench run` and `bench report`; its `contenders`, `paired` and `runs` keys are what T32's leaderboard reads | The done-when asks for a mixed report from a fake team and a fake agent; the leaderboard needs a stable schema |
| 2026-10-29 | T9 | Imported tasks live in their own directory (`benchmarks/tasks-swe/`, git-ignored with the repository cache `benchmarks/.cache/`), selected with `bench run`/`list --tasks-dir`; the suite (ledger, budget, `.runs/`) stays `benchmarks/` | Mixing them into `benchmarks/tasks/` would make the default `bench run --fake` and the gate's suite tests need the network and Docker. Whether to commit imported tasks (issue text and test patches under each repository's license) is left to the user |
| 2026-10-29 | T9 | `task.yaml` gains `repository` (name, url, full commit SHA, license) and `check_timeout_seconds`; `sabotage` is optional for repository tasks (the untouched repository is the solution that must fail; `--fake-solution broken` refuses them), and their reference is `reference/solution.patch`, which the fake team turns into written files | SWE-bench has no sabotage edits and the reference only exists as a diff against a repository not present at import time |
| 2026-10-29 | T9 | Imported instances are `feature` tasks with intent `bugfix`, split `heldout` by default; the request is the issue text with one framing line, without `hints_text` | `fix` mode requires a stack trace or repro command, which issues lack; SWE-bench's standard setting gives no hints; a public set is better kept out of tuning |
| 2026-10-29 | T9 | The team's repository is a copy of the cached tree with one commit (no upstream history) | A known SWE-bench leak: the later fix is reachable in a full clone |
| 2026-10-29 | T9 | Checks use the SWE-bench published evaluation images (`swebench/sweb.eval.x86_64.<id, __ as _1776_>:latest`, conda Python at `/opt/miniconda3/envs/testbed/bin/python`, `--platform linux/amd64`), never pulled automatically. A stdlib runner (`bench/swe_runner.py`, Python 3.6 syntax, `UP` rules off for that file) lays the team's tree over `/testbed`, restores the test patch's files at the base commit, applies the test patch and runs `pytest -rA`; `PASSED`/`XFAIL` count as a pass, as in the SWE-bench harness. A patch that no longer applies is a failed criterion; Docker missing/stopped or a missing image is a check `error` | Reuses the images the community already builds instead of re-deriving each environment; no host shell (the runner is argv-only Python in the container); the oldest SWE-bench environments run Python 3.6 |
| 2026-10-29 | T9 | Only instances with pytest node ids are imported; others are skipped with the reason. The starter list (15 SWE-bench Lite ids, checked against the public dataset viewer) covers Flask, seaborn, pytest and pylint (GPL-2.0, noted) and leaves out psf/requests | Django and SymPy use their own runners; many requests tests call httpbin.org, which the checks' `--network none` blocks |
| 2026-10-29 | plan | The executor of a task makes its live runs itself (never a follow-up for the user) and first asks the user in chat to check the balance of each provider the run bills, naming the providers, the estimate and the remaining allocation; that confirmation covers the task's runs within its allocation. Ledger, caps and the explicit OK to go over are unchanged. T3's `doctor --probe-tools` allocation ($0.10) has no ledger line: T25 runs that probe for every candidate anyway | User's instruction (2026-10-29): live runs belong to the task, the user only confirms the money is there |
| 2026-10-29 | T9 | Checked against real SWE-bench images (no API spend): dataset = SWE-bench Lite test split (300 rows, Hugging Face rows API), 5 instances (one per starter repository plus pytest-5227) with `--fake`: reference passes both criteria, untouched repository fails `fail-to-pass` only, 2–13 s per criterion emulated. The image's HEAD is a `SWE-bench` commit over the base that only changes file modes, so restoring test files from the base commit is right | The done-when only needed a synthetic instance; the real run found two dataset quirks below |
| 2026-10-29 | T9 | The runner runs the test *files* the ids name and matches each id against the start of the reported node id (a failing match wins over a passing one), instead of passing ids to pytest | SWE-bench cut ids at the first space; pytest aborts the whole run on one id it cannot find, so every `pass-to-pass` test of pytest-7373 came back missing. Running files is also what the SWE-bench harness does |
| 2026-10-29 | T9 | Test entries starting with `[` (`[100%]`, `[`) are dropped on import and kept in `swe.json` as `ignored_tests` | Progress-bar fragments SWE-bench's parser kept; only pytest-5227 in Lite has them, and skipping the instance would have dropped a valid task |
| 2026-10-29 | plan | §4 *Large downloads and disk space*: images, datasets and toolchains are fetched again by each task that needs them and removed (with scratch batches and `benchmarks/.cache/`) before the task is done; only what the task created is removed. T9's five SWE-bench images (about 12 GB unpacked) and its repository cache were removed after the real-instance check | User's instruction (2026-10-29): about 50 GB free, no redundant large files kept forever; an image is pulled again in about 30 s |
| 2026-10-29 | T10 | CrewAI checked: 1.15.31 installed, 1.15.33 on PyPI (1.15.32's changelog: flaky tests and docs only; nothing for 1.15.33 yet), so no upgrade. The triage call is `LLM.call(messages, response_model=TriageAnswer)`: the docs page shows `response_format` at construction, but the installed providers take `response_model` per call (OpenAI Responses API: a strict `json_schema` text format) and return the validated model; the answer is parsed whatever comes back (instance, mapping, JSON text in a fence) | Live docs beat the plan; one LLM object serves every case of `bench triage` |
| 2026-10-29 | T10 | `TaskProfile` (with `TaskTarget` and the `Intent`, `TaskScale`, `Risk`, `VerifyBy` literals) lives in `contracts.py` (438 lines, under the 450 guard); the size literal is `TaskScale` because `pipeline/lean.py` already has `TaskSize` (small/medium/large) | The plan names `contracts.py`; two different size types under one name would be confusing |
| 2026-10-29 | T10 | The model answers a separate `TriageAnswer` (every field required, no defaults, so it is a valid strict schema); target kind, repository, stack, `overrides` and `source` are filled by the controller only. Rules: no existing code drops `bugfix`, `refactor`, `review`, `upgrade_deps`, `security_audit`, `performance` (then `question` if the request is in question form, else `new_project`) and `repro`/`project_tests`; focus paths must exist inside the repository; questions capped at 3; `needs_clarification` without a question is cleared; an explicit mode or maintenance preset sets the primary intent. Size and risk are **not** overridden: request length and the risk keyword list are hints, not certainties | §4: agent text is never evidence; "where signals are certain" |
| 2026-10-29 | T10 | A failed call or an unparseable answer falls back to a signals-only guess (`source: signals`, confidence low, reason in `overrides`) instead of failing; that guess (keyword precedence, provisional until T33) is also the offline baseline `bench triage` scores (84 % primary intent, 73 % size on the 74 cases) | Triage must never stop a run; the baseline shows what the model call adds |
| 2026-10-29 | T10 | Clarification is `intake/clarify.settle` on the run's `HumanChannel`: answers go to `clarifications`, unanswered questions to assumptions; with no one to ask, a `low`-confidence profile raises `NeedsInfo` (exit 4), any other records assumptions. No run calls triage yet and no `triage` CLI command was added: `do` (T14) wires triage and clarification into runs, T31 adds the UI preview | "Reuse the existing needs_info flow"; adding the entry point here would pre-empt T14 |
| 2026-10-29 | T10 | `benchmarks/triage/cases.yaml`: 74 cases (all 14 intents as primary, 3–9 each; all four sizes; 12 without a directory, 2 empty directories, 6 fixtures without Git, 3 explicit modes). Repositories are the benchmark tasks' public `fixture/` trees (never `reference/` or `acceptance/`). Every field of the signal expectations is checked (absent = false/empty), offline by `bench triage` and by `tests/test_bench_triage.py` in the gate | "Done when": deterministic expectations for every case |
| 2026-10-29 | T10 | `bench triage --live` estimates prompt tokens as characters / 4 of the real messages plus 400 output tokens per call (output capped at 1,200), stops once `--max-cost-usd` (default $0.40) is spent, and writes one ledger line with the summed priced cost (`null` when a call is unpriced) | Same ledger and refusal rules as `bench run` |
| 2026-10-29 | T10 | Live run (user confirmed the OpenAI balance): `bench triage --live --provider openai --allocation T10`, `openai/gpt-6-luna`, 74 calls, $0.0109 of T10's $0.40 (estimate $0.0195), results in `benchmarks/results/2026-10-29/triage/`: primary intent 97.3 %, size 78.4 %, target 100 %, secondary intents 88.9 %, no fallbacks; the signals-only guess scores 83.8 % / 73.0 %. Leftover $0.389 stays in T10 for a later move | The task's live run, recorded in the ledger |
| 2026-10-29 | T10 | Found by the live run, left for T13/T14 (where triage starts deciding runs): when the no-repository rule drops every intent, its fallback picks `question` only for the question form, so a pasted traceback with no directory became `new_project`; it should follow `guess_intent` (a trace or failing test with no code → `question`). The model sizes larger than the labels on 13 of its 16 size misses; sizes stay provisional until T33 | Not changed after the measurement, so the recorded figures describe the code as committed |
| 2026-10-29 | T11 | The stage library holds each stage *variant* of the bundled recipes as its own entry (`verify_fix`, `verify_docs`, `review_maintain`, `baseline_for_fixes`, ...: 55 entries for 32 stage names, 24 rules), copied exactly, and a composed recipe takes stages in the library's global order (each entry's `requires` must be earlier, checked on load). Rules are first-claim-wins per stage name, so specific rules (an intent alone → its policy and verify variant) precede general ones | Goldens must match field for field (prompts, write scopes, skip conditions); one global order exists because every bundled recipe is a subsequence of it |
| 2026-10-29 | T11 | Canonical profiles: every bundled recipe is reproduced exactly (stages, policies, mode) at size `medium`, risk `low`. `custom` comes from `performance` (or `devops`) on an existing project: intents with no specialised stages get the generalist `work` stage. `adopt` comes from `question` on an existing project (profile, baseline, map) until T15 adds `answer`; a question, research or plan without a project composes nothing yet (`ComposeError`) | The plan names `question` → `answer` (T15); nothing else can run a question today |
| 2026-10-29 | T11 | Deviations from the plan's example rules: `map` is added for size ≥ medium or risk ≥ medium (not for review, upgrade-deps or questions, as in the bundled recipes), but `review` is **not** dropped on small low-risk tasks: T6 chose one reviewer on the worker tier for them, and that stays until T13/T33 measure otherwise. Runtime `skip_if` conditions (`small_task`, `large_task`, `minimal_team`, ...) are kept, so the lean team still decides at run time. A maintenance policy (`tests_only`, `docs_only`, `manifests_only`) applies only when its intent is the only one; `upgrade_deps` with another intent verifies with the debugger, who may change code | A mixed request ("upgrade and fix what breaks") must be allowed to touch code |
| 2026-10-29 | T11 | Secondary intents add stages only where the primary's stages leave a gap: `docs` → `docs_legacy` (after the change, before `verify`), `tests` → characterization tests (not for `feature`, which has its own `tests` stage); a new project gets `docs`/`devops` without their keyword condition when triage found them. Other secondaries (security, performance, devops on an existing project) add nothing yet | Each added stage has to fit the primary's contracts; e.g. `devops` reads `spec`/`plan`, which a fix does not produce |
| 2026-10-29 | T11 | Explanations: `StageSpec.why` (left out of the digest when empty, like `solo_prompt`, so bundled digests and 0.2.0 resumes are unchanged), `RunManifest.stage_reasons` (written by `RunRecorder.begin` from the recipe), and the plan preview `recipes compose --intent ...` (no triage there: T14's `do` previews triage plus the composed plan). No run composes yet: `do` (T14) calls `compose.choose_recipe`, which loads a named recipe (user recipes first) and composes only when none is named | The plan asks for the explanation "in the manifest and the plan preview"; `do` does not exist yet |
| 2026-10-30 | T12 | The dynamic team is a third strategy, `team`, run by the existing Flow machinery: `lead_recipe` derives a `team-<name>` recipe from any recipe with a verify stage (controller and `reproduce` stages kept before the lead, every work stage replaced by one `lead` stage, `verify` and later controller stages kept, plus a `security_review` stage skipped unless a signal fired and no security review read the handed-back tree). Board, resume, cancel and budgets come for free; T13's ladder picks between strategies | One machinery for every strategy; a new stage kind (`lead`) instead of a second orchestrator |
| 2026-10-30 | T12 | The cap is `[delegation] max_delegations` (and `max_iter` per job), not `team.max_delegations`: `[team.<key>]` tables are teammates, so `team.max_delegations` would be read as a teammate named `max_delegations`. Env: `ENGINEERING_MAX_DELEGATIONS`. Defaults small 2 / medium 5 / large 12 / 20 calls per job are provisional until T33 | Settings collision |
| 2026-10-30 | T12 | The "per-delegation budget" is a cap on the job's model calls (`max_steps`, ≤ `delegation.max_iter`, applied as the agent's `max_iter`), inside the run's budgets; a budget stop inside a job cancels it, tells the lead to stop, and fails the stage at its next safe point. Cost per job is measured (a `DelegationHub` event sink counts `llm.call` usage per delegation lane), not estimated in advance | No per-job pre-estimate exists before T13's budget-aware rungs; the run budget already trips mid-job |
| 2026-10-30 | T12 | Depth 1 is enforced twice: `stage_groups` never gives the `team` group to a session (only the lead stage adds it), and the tools reach the controller only through the run's `DelegationHub` while the lead session holds it, from the lead's own key with no lane (delegated jobs run in lanes `D1`, `D2`, ...); a busy delegator refuses too | A custom teammate may list `team` in `tool_groups`; it gets tools that refuse |
| 2026-10-30 | T12 | The lead's teammate (the generalist) also writes a fix's reproduction and is the verify stage's repair agent in the lead recipe, so the only other sessions are the teammates the lead called (plus the controller's forced security review). After a job that changed files the controller runs the verify stage's checks without repair (`VerificationLoop` with `repair=None`) and hands the verdict to the lead; the final `verify` stage reuses those results when the tree did not change | "Sessions only for teammates the lead called"; one check machinery |
| 2026-10-30 | T12 | Security signals are regexes in `config/lead.yaml` (auth, crypto, subprocess, SQL, deserialisation, secrets) over the request and every file the run added or changed; "the lead asked for one" means a security review read the exact tree the lead handed back. When-to-delegate guidance lives in the same file and goes into the lead's prompt as written (T17/T21 move and tune it) | Data, tunable; T17's harness files do not exist yet |
| 2026-10-30 | T12 | All seven `team` tools are eager: the lead's schema is 4,651 tokens per call (under the 6,000 target). New card kind `delegation` (UI icon and label added; no progress weight, like repairs) | Hiding the delegation tools behind Find Tool would defeat the strategy |
| 2026-10-30 | T12 | `team` is not added to `bench` (`bench/plan.py` `STRATEGIES`, the estimate's token priors): measuring it live is T13/T33's, and its priors need a measured run. The perf proxies cover it offline: small = 1 session, 3 calls; medium (a parallel pair and one review) = 4 sessions, only the teammates the lead called | No live run in this task's allocation |

