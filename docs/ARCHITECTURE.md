# Orchestrator architecture

## Strategies

`settings.strategy` (`--strategy`) picks how a run is orchestrated. All implement
`Strategy.run(ctx, recipe, bundle) -> RunResult` (`pipeline/strategies.py`) and run inside the run
recorder, so every one leaves a manifest, an event log, and usage:

| Strategy | What it is | Resumable |
|----------|-----------|-----------|
| `auto` (default) | The escalation ladder: start on the cheapest rung the task's size and risk allow (a lead alone, a lead with teammates, the full pipeline) and escalate on the same workspace while verification fails ([below](#escalation-ladder-the-auto-strategy)). Each rung is a walk of the pipeline machinery | yes |
| `pipeline` | A recipe's stages as a CrewAI Flow with typed hand-offs, a task board, resume, and cancellation ([below](#pipeline-recipes-and-resume)) | yes |
| `single` | One agent (`generalist_engineer`) with every tool and one task: the baseline for the benchmarks. It runs through the pipeline machinery as a one-stage recipe, so it has a board card, a stage record, resume, and cancellation too | yes |
| `team` | A lead agent that does the work and calls teammates on demand for bounded, typed jobs; the recipe's controller skeleton and verification stay ([below](#dynamic-team-the-team-strategy)). Also through the pipeline machinery, as a recipe derived from the mode's | yes |

The default is `auto`, provisional until the 0.3.0 live evaluation (T33) measures it: in the 0.2.0
benchmark ([BENCHMARKS.md](BENCHMARKS.md#results-2026-10-04)) one agent matched the pipeline's pass
rate at a small fraction of its cost, so the team is spent only where verification says it is needed.
`single`, `team` and `pipeline` stay forceable with `--strategy`. The 0.1.0 manager-led crew (`hierarchical`) was removed in 0.3.0 after it hit the time limit on all three measured runs; a run it recorded stays viewable but cannot be resumed.

## Filesystem model

Generated applications live under `workspace/<project-name>/` (relative to the
directory the command is run from) by default. A run resumes that directory unless
`--reset` is explicitly used. A project is *owned* when it contains
`.engineering-team/owner.json`; non-empty directories without it are never written
to (unless `--adopt` takes one over, [below](#adopting-an-existing-project)), and `--reset` deletes only owned
projects (`--force-reset` overrides for foreign and adopted directories but never for home, the current
directory and its parents, the filesystem root, symlinks, or the engineering-team installation). This supports normal
nested project structures and lets agents use the stack's own build and test
tools.

Agents reach the workspace only through the tool catalogue ([TOOLS.md](TOOLS.md)); path
checks, the command allowlist, and the process boundary are described once in
[SAFETY.md](SAFETY.md).

## Run context, tools, and locking

Nothing reads process-wide state. Each run builds one frozen `RunContext`
(`runtime/context.py`): `run_id` (sortable timestamp plus random suffix), the immutable
`Settings`, the `ProjectWorkspace`, `run_dir` (`<workspace>/.engineering-team/runs/<run_id>/`,
controller-owned and hidden from agents), a `command_gate` semaphore
(`execution.max_parallel_commands`, default 2) that caps concurrent project commands, and a
`cancel_event` every worker checks at its next safe point. The CLI entry points and every stage
runner are constructed from it.

Tools are built per run, not defined at import time. `tools/registry.build_tools(ctx, groups=, write_scope=,
read_only=)` returns fresh tool instances closed over `ctx`, so two runs in one process (or two
agents) never see each other's files. The code is split by responsibility: `tools/workspace.py`
(the path-safe `ProjectWorkspace`), `tools/commands.py` (command validation), `tools/scope.py`
(`WriteScope`), `tools/support.py` (`ToolEnv`, the wrapper every tool call goes through), one module
per group (`read_tools.py`, `search_tools.py`, `write_tools.py`, `command_tools.py`) on top of
logic modules (`search.py`, `navigation.py`, `symbols.py`, `patching.py`, `changes.py`,
`scripts.py`, `ignore.py`), and the catalogue: `tools/spec.py` (`ToolSpec`, the groups), one
`catalogue_*.py` per kind of tool, and `tools/registry.py` (`build_tools`).

The catalogue is data: each `ToolSpec` names a tool, its group (`fs_read`, `search`, `fs_write`,
`command`, the `dev`, `code_intel`, and `runtime` groups, and the coordination groups `board`,
`notes`, `human`), whether it is read-only, needs the network, or needs the command gate, and the factory
that builds it. `build_tools(ctx, groups=, write_scope=, read_only=)` assembles a run's tools from
it. Every call passes through `ToolEnv.run`, which applies cancellation, the run's `tool_gate`
(the hook the budget guard uses to refuse calls), write-scope checks, the `ERROR:` convention, and
emits a redacted `tool.call` event to the run's event sink (a no-op sink until the run store lands).

### Developer tools

`devtools/` turns "run the tests" into data. `detect.py` finds a directory's stack from its
manifests (shared with the verifier and the repository analyzer); `plans.py`, `plans_static.py`,
and `plans_deps.py` build the command for each framework or tool, asking it for a machine-readable
report (JUnit XML, JSON, `go test -json`, TRX, LCOV) in a scratch directory; `parsers/` are pure
functions from that report to the shapes in `models.py` (`TestReport`, `DiagnosticReport`,
`CoverageReport`, `InstallReport`, `AuditReport`); `runner*.py` (`DevRunner`) runs a plan through
`ctx.backend`, applies the timeout, and reads the report back; `render.py` writes the compact text
the tools return. A tool that is not installed is `unavailable` (with an install hint) and a run
that produced no usable result is `error`; neither can read as a pass. The agent tools in
`tools/dev_tools.py` are thin wrappers, and the verifier calls `DevRunner` directly.

### Code intelligence

`codeintel/` answers "where is it, and who depends on it" without a language server. `index.py`
reads the project's source files once per call (`SourceIndex`: `.gitignore` honoured, 200 KB and
5,000-file caps; it always covers the whole project because dependents live outside the directory
a caller asks about). `definitions.py` extracts definitions with their line extent (`ast` for
Python, indentation for Ruby, declaration regexes plus brace counting for the rest);
`imports.py`, `imports_base.py`, and `imports_langs.py` build the forward and reverse
`ImportGraph`, resolving specifiers to project files per language; `references.py` classifies uses
(definition, import, call, other); `related.py` pairs files and symbols with tests;
`dependencies.py` parses manifests and lockfiles; `git.py` reads `git log`/`git blame` through
`ctx.backend` (fixed argv, no pager or external programs) for `hotspots.py` and `todos.py`, and
raises `GitUnavailable` when there is no history so the tools degrade to a plain answer;
`render.py` writes the compact text. The agent tools in `tools/codeintel_tools.py` are thin
wrappers; the repository analyzer calls these modules directly. `codeintel/__init__.py`
imports `engineering_team.tools` first so the two packages, which import each other's modules,
load in a fixed order whichever one is imported first.

### Browser tools

`RunContext.browsers` (`runtime/browsers.py`, a `BrowserRegistry`) owns the run's headless browser:
one worker thread (Playwright's sync API is bound to the thread that started it), one session per
agent up to `browser.max_contexts`, sessions closed by `RunRecorder.stage` (their stage) and
`RunRecorder._finish` (the run), and an `atexit` hook. Nothing starts, and `playwright` is not
imported, until the first browser call. `browsertools/driver.py` is the Playwright driver and
session (page events become the console log), `guard.py` decides which URLs are allowed (this run's
localhost ports, plus allowlisted external hosts), `proxy.py` enforces it on every request,
`actions.py` is what each tool does to a page, `a11y.py` the accessibility heuristics, and
`tools/browser_tools.py` the thin tool wrappers; `build_tools` leaves the `browser` group out when
the extra is not installed. See [SAFETY.md](SAFETY.md#browser-tools-sandbox-scope).

### Web and knowledge tools

`webtools/` holds everything that touches the internet or documents. `safenet.py` is the only HTTP
client: a `WebFetcher` that validates and pins addresses, follows redirects by hand, and applies the
domain rules, the caps, and the run's `RequestLimiter` (`runtime/requests.py`, a field of
`RunContext`); `search.py` has the `SearchProvider` interface and the Serper, Brave, and Tavily
providers; `packages.py` parses PyPI, npm, crates.io, and Go proxy responses; `htmltext.py` turns HTML
into Markdown (standard library only); `docsearch.py` is the BM25 index over repo docs, context
directories, and `run_dir/web-cache/`; `untrusted.py` labels everything the tools return. The agent
tools in `tools/web_tools.py` and `tools/knowledge_tools.py` are thin wrappers, and
`registry.build_tools` leaves the `web` group out unless `web.enabled` allows it for the teammate.
Network access in tests is a fake (`safenet.NETWORK` is the one seam). See [SAFETY.md](SAFETY.md#web-tools-network-model).

### Runtime tools and processes

`RunContext.processes` (`runtime/processes.py`, a `ProcessRegistry`) owns every background process
of the run and the loopback ports handed out to it. Processes start through `ctx.backend.start`,
are tagged with the stage they were started in, and are stopped by `RunRecorder.stage` (its stage),
`RunRecorder.running` (the run ends or crashes), a reaper thread (cancellation and lifetime limits),
and an `atexit` hook; the tools in `tools/process_tools.py` and `tools/network_tools.py` are thin
wrappers, and `tools/net.py` holds the HTTP policy and client. See [SAFETY.md](SAFETY.md).

### Execution backend

Tools never spawn processes. `tools/commands.py` validates a command and builds a `CommandSpec`;
`ctx.backend` (an `ExecutionBackend`: `run(spec) -> CommandRecord`, `start(spec) -> ProcessHandle`)
runs it. `LocalBackend` puts each command in its own process group, streams output to a capped log
under `run_dir/commands/`, keeps a head-and-tail window in memory, and kills the whole group on
timeout or cancellation. `DockerBackend` (`execution/docker.py`) implements the same protocol by spawning
`docker run` as the "process", so output capping, timeouts, and cancellation are the same code; it adds the
container's name, labels, hardening flags and mounts (`execution/docker_args.py` builds the argv, pure and
golden-tested), image choice (`execution/images.py`), and removal of the container when a command is stopped
or the run ends. `create_backend` picks one from `execution.backend` and raises `ExecutionUnavailable` (a usage
error) instead of falling back when Docker is missing. A backend that resolves programs itself
(`resolves_on_host = False`) is not asked whether the host has them. Background processes use `start`.
See [SAFETY.md](SAFETY.md#docker-backend).

### Git

`GitPort` (`git/port.py`, `ctx.git`; built from `git/core.py`, `reads.py` and `writes.py`) is the controller's Git, a thin wrapper that builds fixed `git` argv
lists and runs them through `ctx.backend` (so cancellation, timeouts, and a Docker sandbox apply; nothing
spawns `git` itself). Its rules (no push or remote, no hooks, no outside repository) are in
[SAFETY.md](SAFETY.md#git). It offers `is_repo`, `is_dirty`, `status`, `current_branch`, `head`,
`create_branch`, `worktree_add`/`worktree_remove`, `checkpoint(message)` (stage everything, commit, return the sha; a
commit is made even when nothing changed), the work as it is now (`diff`, `diff_stat`, `export_patch`,
`tree_hash`, all via a temporary index so the real one is never touched), and bounded history for the read-only tools
and code intelligence (`log`, `show`, `blame`, `search`, `diff_refs`).

`pipeline/checkpoints.py` uses it for a run's history: when the project is **new** (nothing in the workspace but
controller state) and `git.enabled`, the run starts with `git init` (branch `main`, an initial commit,
`.engineering-team/` and heavy directories in `.git/info/exclude`); after every stage that finishes the controller
commits `stage(<name>): <the agent's one-line summary>`, so a clean run leaves `Initial commit` plus one commit per
stage (a skipped or reused stage has none, a resumed run commits only what it runs again), and after the
final verification it commits anything that gate changed (`final: ...`). A repository that exists is continued; a
project with files and no repository is left alone. A Git problem becomes a `git.warning` event and never fails
the run (events: `git.init`, `git.checkpoint`, `git.warning`). Only runs of a strategy with stages (`pipeline` and `single`) commit. The workspace revision of resume (`runtime/snapshot.workspace_revision`) stays a
content hash of non-ignored files, because it must work without Git; `GitPort.tree_hash` is the Git object hash of the same project
for tools that want to compare with commits.

### Workspace snapshot

`RunContext.create` snapshots the workspace (hashes for every non-ignored file, text for small
ones) as `ctx.baseline`, which is what `Workspace Changes` diffs against, with or without Git.

A `WriteScope(allow, deny)` of gitignore-style globs restricts which paths an agent's write,
replace, and delete tools may change; paths are judged after symlink resolution, `deny` wins, and a
directory delete needs every file inside to be in scope. The error names the owned paths. Reads are
never restricted. Parallel agents will each get a disjoint scope.

One run writes to a workspace at a time. `runtime/locks.WorkspaceLock` takes an OS-level `flock` on
`.engineering-team/lock` and records the holder's pid and run id there. The kernel drops the lock if
the holder exits or is killed, so a crash never wedges the workspace; the next run overwrites the
stale record. A second run, or `--reset` of an in-use workspace, fails with `WorkspaceBusy` (exit
code 2). The lock is held from workspace preparation until the run ends.

## Run directory, contracts, and events

Every run owns `<workspace>/.engineering-team/runs/<run_id>/` (run ids are `YYYYMMDD-HHMMSS-<hex>`,
so a directory listing is chronological). It is controller-owned: agents cannot reach it, apart
from reading `commands/` logs. This is the single description of its layout:

| Path | Written by | Contents |
|------|-----------|----------|
| `manifest.json` | `RunStore` | `RunManifest`: run id, mode, project, request and settings hashes, versions (engineering-team, CrewAI, Python), strategy, recipe, status, stage records, created/finished, and a `summary` (usage, estimated cost or `null`, budget status) once the run ends |
| `events.jsonl` | `JsonlSink` | One `Event` per line: `seq`, `ts`, `run_id`, `type`, optional `stage`/`agent`/`lane`, `data` |
| `trajectory.jsonl` | `TrajectoryRecorder` (`runtime/trajectory.py`) | What the agents saw: one line per tool call (`seq`, `ts`, `stage`, `teammate`, `lane`, `tool`, redacted `args`, redacted and capped `output`, `ok`, `ms`) or model message (`role` = `assistant` reply or `final` answer), plus `notice` lines when the size budget bites. `seq` is the line number; each `tool.call` event carries it as `trajectory_seq`. Secret values are scrubbed before the cut; output is capped per call (`trajectory.max_output_chars`) and per file (`trajectory.max_mb`, past which outputs are cut harder). Read it with `engineering-team trace`, the UI card drawer, or the report's Trace section. Runs from before 0.3.0 have no such file |
| `request.md` | `RunRecorder` | The request exactly as given |
| `usage.json` | `RunRecorder` | `UsageReport`: token totals and a breakdown by stage, agent, and model, with per-model cost (`null` when the price is unknown), and `sessions`: one `SessionUsage` per agent session ([Usage accounting](#usage-accounting-and-sessions)) |
| `settings.json` | `RunRecorder` | The effective `Settings` for the record (no secrets: credentials are read from the environment, never stored; hook addresses and MCP server environments are replaced) |
| `pipeline.json` | `PipelineState` | The pipeline's hand-offs between stages (spec, plan, work-package status, board card ids, agent summaries, the latest check results and the `VerificationRecord`); see [Pipeline, recipes, and resume](#pipeline-recipes-and-resume) |
| `ladder.json`, `rungs/<rung>.json` | `pipeline/ladder_run.py` | `auto` runs only: the `LadderRecord` (where the ladder started and why, each rung's reason, outcome, failing checks, cost and the brief it was given) and each rung's final `pipeline.json` once the run escalated past it; see [Escalation ladder](#escalation-ladder-the-auto-strategy) |
| `verification/round-<n>.json` | `Verifier` | The results of each check run (status, exit code, duration, log path, the workspace revision it ran against, the parsed test or diagnostic report); see [Verification and repair](#verification-and-repair) |
| `fix-input.json` | `fix` | `fix` runs only: the person's `--repro` command, the whole `--trace-file`, and `--allow-unreproduced`; read by the controller, never by agents |
| `run-options.json` | the repository-mode commands | What the command line asked beyond the request (`maintain --fix`, `review --base`); read again when the pipeline starts or resumes, never by agents |
| `recipe.yaml` | `pin_recipe` | The recipe, for a run that used one from a file (yours); a resume prefers it |
| `findings.json`, `findings.md` | `modes/findings_report` | `review` and `security-audit`: the consolidated findings for CI and for people |
| `checks.yaml` | `pin_checks` | The pinned copy of the user's `--checks` file (its hash is in `pipeline.json`) |
| `cancel` | `engineering-team cancel` | Flag file the running controller polls; deleted when a run starts or resumes |
| `board.json`, `board.md` | `BoardStore` | The task board: every card with its history (`BoardState`), and a Markdown view of it ([Task board](#task-board)) |
| `notes/<key>.md` | `NoteStore` | Shared notes agents wrote with `Write Note` |
| `crew-log.json`, `crew-log-<stage>.json` | CrewAI | CrewAI's own execution log (one per stage crew in the pipeline) |
| `inbox/` | `engineering-team note\|pause\|unpause`, the web UI | Commands (and answers to the team's questions) waiting for the process working on the run to apply them |
| `report.html`, `report.md` | `report/` | The run report, written when the run ends (HTML; `engineering-team report --format md` writes Markdown). Read from the files above only; see [Run report](#run-report) |
| `devtools/last_tests.json` | `Run Tests` | The failing tests of the last run, which `Rerun Failed Tests` reads |
| `commands/<n>.log` | `LocalBackend` | Full output of each project command (including `git` commands) |
| `verification/baseline.json` | `Verifier` | The baseline's batch of check results ([Adopting an existing project](#adopting-an-existing-project)); not a verification round |

Other state under `.engineering-team/` is per workspace, not per run: `owner.json` (ownership
marker; `"adopted": true` for a directory that held someone's work first), `lock` (workspace lock), `tmp/`,
`tool-home/`, and `cache/`; for an adopted project also `repo-profile.json`, `baseline.json`,
`codebase-map.md`, `isolation.json`, and `worktrees/<run-id>/` (a worktree-mode workspace).

**Contracts** (`contracts.py`) are Pydantic v2 models that every part of the system shares:
`Spec`, `Plan`, `WorkPackage`, `CheckSpec`/`CheckResult`/`CriterionCoverage`/`VerificationRecord`, `Finding`, `StageRecord` (with the workspace revision at the stage's start and end), `RunManifest`,
`Event`. Each has `schema_version` (1) and ignores unknown fields, so a file written by a newer
version still loads; bump the version only for a breaking change in meaning.

**Run lifecycle.** `RunRecorder.begin` writes the manifest as `pending`; `recorder.running()`
moves it to `running` and finishes it as `succeeded`, `failed`, `cancelled` (the run's cancel event
was set) or `interrupted` (Ctrl-C), flushing CrewAI's pending events first. The store enforces the
transitions `pending → running → {succeeded, failed, cancelled, interrupted}` (a pending run may
also be cancelled or failed). `succeeded` is final; the other end states can be left only by
`resume`, which moves the run back to `running` (and counts it in `manifest.resumes`). Manifests and JSON files are replaced
atomically (temp file, `fsync`, `os.replace`), so a reader never sees a partial file, and updates
from several threads are serialised.

**Events.** Anything that happens is `ctx.events.emit(type, **data)`. The default sink appends
one scrubbed JSON line per event under a lock, so lines never interleave and `seq` is the file
order; a log can be tailed while the run is live, and a torn last line after a crash is skipped
by `read_events`. Event types today: `run.started`/`run.finished`; `tool.call` (our tools: tool,
redacted args, duration, ok); and, bridged from CrewAI, `crew.*`, `task.*`, `agent.*`,
`tool.finished`/`tool.error` (CrewAI's view of tool use, including delegation) and `llm.call`
(model as `provider/model`, call id, token usage) / `llm.failed`; `stage.started`/`stage.finished`, `stage.skipped`, `stage.reused`, `stage.retry`, `resume.plan`,
`pipeline.started`/`pipeline.finished`, `pipeline.board_warning`, `run.cancel_requested`;
`board.*` (card created, moved, commented, updated; pause and resume; steering delivered);
`question`, `question.answered`, `question.unanswered`; `note.written`, `decision.logged`;
`budget.warning`/`budget.exceeded`; `git.init`/`git.checkpoint`/`git.warning`; `extension.warning`, `plugins.loaded`, `conventions.loaded`, `hook.ran` (see [Extension points](#extension-points)); `check.started`/`check.finished` and `verify.started`/`verify.repair`/`verify.reused`/`verify.verdict`.

**The CrewAI bridge** (`runtime/bridge.py`). CrewAI has one process-wide event bus. The bridge
registers its handlers once and routes each event to the run bound in the *emitting* context
(`bind_run`, a `contextvar`; CrewAI copies the emitter's context to its handler threads), so two
runs in one process never mix. Events from an unbound context are dropped. Worker threads must
run under `contextvars.copy_context()` to stay attributed. CrewAI runs its handlers on a thread pool,
so bridged events emitted together (an agent's and its task's completion) can be *recorded* in either
order; each carries `data.emission`, CrewAI's own emission number, and sorting by it gives the order
they happened in. Nothing in the controller depends on the order of bridged events.

**Usage, cost, and budgets.** `RunContext.events` is a fan-out of the JSONL log, a `UsageTracker`,
and a `BudgetGuard`. The tracker counts the bridged `llm.call` events (tokens per stage, agent, and
model; the stage comes from `stage_scope`, a `contextvar`, so it also tags events CrewAI delivers
from its handler threads) and our `tool.call` events. `pricing.py` turns that into USD with the
table in `data/pricing.toml` (and the user's `[pricing]` overrides); a model with no price has an
unknown cost, never `$0`, and a total with any unpriced model is unknown. `usage.json` and the
manifest `summary` are written when the run ends, and the same report can be rebuilt from
`events.jsonl`. The `BudgetGuard` re-evaluates after each model call's usage arrives and refuses
tool calls through `ctx.tool_gate`; passing a limit emits `budget.exceeded`, sets
`ctx.cancel_event` (tools then tell the agent why it must stop), and `guard.check()` raises
`BudgetExceeded` at the next safe point: `RunRecorder.stage()` entry and exit, and the end of
`running()`. A budget stop ends the run as `failed`, not `cancelled`. Overrun by model calls
already in flight is inherent and documented in
[CONFIGURATION.md](CONFIGURATION.md#budgets-usage-and-cost). Warnings are emitted once per limit
at 80%.

**Secrets never enter events.** Before a line is written, every value of an environment
variable whose name contains `KEY`, `TOKEN`, `SECRET`, or `PASSWORD` (at least 8 characters) is
replaced with `[REDACTED]`, anywhere in the event, including nested data.


### Usage accounting and sessions

`UsageTracker` counts `llm.call` events per (stage, agent, model) and `tool.call` events; that is the
live total the budget guard watches. For the profiler the same log is read back into **agent
sessions** (`runtime/sessions.py`): a session is one agent working on one task (a stage, a work
package, a delegation), from CrewAI's `agent.started` to `agent.completed` or `agent.error`
(a session that never ended is `open`). A session holds the model calls it made (`llm.call`, with
token usage and cached tokens), the prompts they were sent, its tool calls and its cost. Bridged
events are matched to a session by CrewAI's `emission` number, because the handler pool records them
out of order; `tool.call` events (ours) by time, stage and lane.

The bridge adds one event per model call, `llm.request`, with `prompt_chars`: the size in characters
of the call's `system` text (system messages and the task prompt), `tools` (the schemas sent with the
call), `history` and `results` (what tools returned), see `runtime/prompt_parts.py`. The profile turns
them into tokens at four characters per token, an estimate shown next to the billed prompt. Failed tool
calls are classified by their `ERROR:` message from the trajectory (`write-scope`, `path-not-found`,
`edit-no-match`, `command-not-allowed`, `timeout`, `bad-arguments`, `other`).

`usage.json` carries the sessions and `manifest.json`'s `summary.sessions` their number, written when
the run ends; `build_profile` (`runtime/profile.py`) rebuilds everything from `events.jsonl` and
`trajectory.jsonl` for any run, finished or not, and adds the critical path, stages that ran their
sessions one after another, re-read files and failures. Runs from before 0.3.0 have no `llm.request`
events: their profile has sessions, time, tokens and cost but no prompt parts.

The offline performance proxies (`tests/perf/`) run scripted scenarios through the real stage runner and
assert ceilings on sessions, model calls, tool-schema tokens per agent and always-on prompt size, plus
the plan's small-task targets, which the lean team (T6) meets: a small new project and a small fix
each take 3 sessions and 6 model calls, with 3,234 tool-schema tokens per call (0.2.0: 11 sessions,
19 calls and 12,102 tokens for the same project; 9 sessions for the fix).

## Run report

`report/` turns a run directory into one page. `collect.build_report` reads the manifest (required),
`events.jsonl` (one pass: tool calls per teammate, lane intervals, screenshot records, the failure
message of `run.finished`), `board.json`, `pipeline.json` (checks, criteria coverage, findings, diff
noise), `usage.json`, `changes.patch`, and `screenshots/` into a `RunReport`; `render_html` and
`render_md` render it. Every file except the manifest is optional, so a failed, cancelled, still
running, or half-written run still gets a report. It reads files only (no `RunContext`), so the
`report` command works on a run from another process.

- **Written by the controller, quoting agents.** Statuses, the banner and its reasons, warnings
  (budget, unavailable required checks, unproven criteria, unreproduced bugs, out-of-scope changes)
  come from the manifest and check results; card notes and agent summaries are only quoted and
  labelled agent-reported.
- **Self-contained and inert.** Inline CSS and one inline script (the theme toggle), no CDN, a
  `Content-Security-Policy` that allows no network, and `html.escape` on every string that did not
  come from the renderer (logs, titles, paths, diff lines). Screenshots are embedded as `data:` URIs
  (at most 1 MB each and 3 MB in all; the rest are listed by path), only from the real files of
  `screenshots/` (never a path taken from an event, never a symlink).
- **Bounded and deterministic.** Logs keep their tail (4,000 characters, saying where the full log
  is), diffs are cut per file and in total, and the page carries no "generated at" clock: the same run
  directory renders the same bytes, with stages in manifest order and lanes, teammates, and findings
  sorted. Known secret values in the environment are scrubbed from the text.
- Plain Python templating with `html.escape`, not Jinja2, so there is no new dependency.

`RunRecorder` writes `report.html` as the last step of ending a run (so `new`, `resume`, `feature`,
`fix`, `maintain`, and `review` all get one) and never lets a report failure change the run's outcome.

## Task board

The board is the run's kanban: what is planned, who is doing it, and whether it is really done.
**The controller is the source of truth.** It creates cards and moves them from stage, work-package,
and check events (the pipeline drives this), so the board is correct even when an agent
forgets to update it. Agents use the board tools ([TOOLS.md](TOOLS.md)) to add subtasks to their
own cards, start and hand over their own work, flag blockers, comment, and report progress; every
move is validated.

**Cards** (`board/models.py`) have an id (`K-001`, …), a `kind` (`stage`, `work_package`,
`subtask`, `repair`, `finding`, `check`, `user_note`, `delegation`), a `status`, an assignee (teammate key), lane,
stage, parent, dependencies, criteria, owned paths, artifacts, an attempt count, a blocked reason,
comments, the ids of the checks that justify `done`, tokens and cost, timestamps, and a `history`
of every move (who, from, to, why, with which evidence). **Columns** are `backlog · ready ·
in_progress · verifying · blocked · done · failed`; `cancelled` cards are kept, shown apart, and
leave the totals.

**Who may move what** (one table, `board/rules.py`):

| Move | Agent (own cards only) | Controller |
|------|------------------------|------------|
| `ready → in_progress` (start; counts an attempt) | yes | yes |
| `in_progress → verifying` ("I'm done, please verify") | yes | yes |
| any open state `→ blocked` (reason required) | yes | yes |
| `blocked → in_progress` (resume) | yes | yes |
| `backlog → ready`, `blocked → ready` | no | yes |
| `verifying → in_progress` (failed checks; attempt +1) | no | yes |
| `→ done` (needs check ids as evidence, or `stage_success` for a stage) | **no** | yes |
| `→ failed` (reason required), `→ cancelled` | **no** | yes |
| `failed → ready`, `cancelled → ready` (a resume reopens the card) | **no** | yes |

`done` is final. `failed` and `cancelled` cards can be reopened (`→ ready`) by the controller only,
when a run resumes. A refused move returns an `ERROR:` that lists the
allowed next states. The in-progress column has a WIP limit, `parallel.max_parallel_agents`,
counted over work packages and repairs (stage cards are containers and subtasks belong to their
parent's agent); the controller sending a card back after failed checks is exempt.

**Persistence and events.** `BoardStore` (`board/store.py`, with `board/files.py` for saving and loading) is thread-safe: every mutation runs
under one lock, rewrites `board.json` atomically (the file alone explains the run: each card carries
its history), and emits a `board.*` event in the same order: `card_created`, `card_moved`,
`card_commented`, `card_updated`, `paused`/`unpaused`, `steering_delivered`. Each card event carries
the whole card as it is after the change (`data.card`), so a viewer or a replay needs no other source.
`board.md`
(`board/render.py`) is a Markdown view with one section per column, rewritten shortly after a change
(debounced) and when the run ends; `board.json` is the machine format the CLI and web UI read.

**Progress** (`board.progress()`) is cards done over cards total by weight (stage 1, work package 2,
check 1, subtask 0.5; repairs, findings, and notes weigh nothing, so a late discovery never makes
progress go backwards), per stage (a done stage card means 100%), the count per column, the blocked
cards with their reasons, and the age of the oldest card in progress. There is no ETA.

**Steering.** A card comment authored by `user`, or a run-level note (`add_user_note`), is handed to
the assignee by `take_steering(agent)` as "User note on K-004: …" for the agent's next prompt, once
per recipient; the pipeline calls it at each stage and agent boundary. `pause()` makes every tool
call wait (the safe point between agent steps) until `unpause()` or cancellation. These are library
calls now; the CLI and the web UI use them later.

**Notes and questions.** `RunContext` also carries `notes` (run-scoped notes under
`run_dir/notes/`, plus the project's append-only `.engineering-team/decisions.md`) and `human`
(`runtime/interaction.py`). `HumanChannel.ask` blocks until `answer(question_id, text)` arrives, the
timeout passes, or the run is cancelled; it emits `question` events. It starts non-interactive,
where `ask` returns at once and the agent is told to proceed on an assumption; a front end calls
`enable()` and answers `pending()` questions.

## Triage

`intake/` turns a request into a `TaskProfile` (`contracts.py`): the intents (primary first, at most
three, from `question`, `research`, `review`, `bugfix`, `feature`, `new_project`, `refactor`, `tests`,
`docs`, `upgrade_deps`, `security_audit`, `performance`, `devops`, `plan_only`), the target (new or
existing, the repository, focus paths), size (`trivial` to `large`), risk, how to verify
(`repro`, `project_tests`, `acceptance_tests`, `review`, `citations`, `build`, `none`), the stack,
at most three questions, assumptions, confidence and a rationale. Three steps:

```text
request, --repo/cwd
   -> signals.collect     code only: a repository or not (and Git), files, languages, stacks, tests
                           (the profiler), a stack trace, failing test names, a diff/branch/PR/commit
                           reference, the question form, paths named that exist, keyword groups,
                           security-sensitive words
   -> triage.triage       one structured-output call on the `cheap` tier (role `triage`): the
                           request (as data), the signals and a repository summary -> TriageAnswer
   -> triage_rules        the controller corrects the answer where the signals are certain
   -> clarify.settle      questions asked (or recorded as assumptions, or needs-info) before planning
```

The model's answer is agent-authored text, so the rules (`intake/triage_rules.py`) never take it as
evidence: without existing code, intents that need it (`bugfix`, `refactor`, `review`,
`upgrade_deps`, `security_audit`, `performance`) are dropped and a reproduction or the project's tests
cannot verify; focus paths are kept only when they exist inside the repository; questions are capped
at three; the stack comes from the profiler; an explicit mode or maintenance preset (`--mode fix`,
`upgrade-deps`, ...) wins. Each correction is a line in `overrides`. A failed call or an answer that
does not parse falls back to a guess from the signals alone (`source: signals`, low confidence) with
the reason recorded, so triage never stops a run.

Clarification (`intake/clarify.py`) uses the run's `HumanChannel`: with a person at the terminal
(`--interactive`) or in the web UI, the questions are asked and the answers kept as
`clarifications`; unanswered ones become assumptions. With no one to ask, a low-confidence profile
ends the run `needs-info` (exit 4, the same `NeedsInfo` flow as an unreproduced bug) and any other
profile goes on with its questions recorded as assumptions.

`bench triage` measures all of this on `benchmarks/triage/cases.yaml`
([BENCHMARKS.md](BENCHMARKS.md#triage-bench-triage)).

**`do`** (`cli/do_command.py`, `cli/do_plan.py`) is where triage meets a run. It finds the project
(`--repo`, else the current directory when it is a Git repository, else a new project), classifies the
request (the signals alone when no model can be called, so `--dry-run` needs no key), settles the
questions, composes the recipe from the profile (`pipeline/compose.py`, or the recipe you name) and
shows the plan with the ladder's starting rung and a cost estimate. The route follows the profile:
a new project goes through the `new` path, a change through `cli/repo_mode.py` (a branch, worktree or
copy) with the composed recipe, a review through `review`, and a question, research or plan-only request through the answer
paths ([below](#questions-research-and-plans)). The controller still sizes the run itself (`pipeline/lean.py`) and picks the rung
from that size; the triage size decides which stages the composed recipe has. The plan is written to
`runs/<id>/task_profile.json`. `new`, `feature`, `fix`, `maintain`, `review` and `analyze` are the
same routes with the intent fixed and no preview.

## Questions, research and plans

A `question`, `research` or `plan_only` request changes nothing, so it runs where the project is
(`cli/answer_run.py`: no branch, worktree or copy; the controller's records go to the run directory)
or, with no project, in a workspace that only holds the run's records. The composed recipes are
`profile`, `answer` (existing project), `answer` alone (no project), and `design` + `plan_report`
(plan only; `design` has the write scope `nothing`).

The `answer` stage (kind `answer`, `modes/answer_stage.py`) gives its teammate `readonly_tools` (the
read-only tools of its groups, and a write tool that can reach one scratch path only) and takes an
`Answer` back (`modes/answer_contracts.py`: summary, sections, citations, confidence, open questions).
The controller then checks every citation (`modes/citations.py`): a path must be a file of the project (not
outside it, not the controller's state), the lines must exist and contain the quote after whitespace is
normalised, and a URL must appear in a successful `Fetch URL` call of the run's `trajectory.jsonl`
(as asked or as redirected to). Citations that fail go back to the agent once
(`ANSWER_REPAIRS`). `ANSWER.md` is written either way with failed citations marked; the verdict is
`verified` (all hold), `partial` (some hold, or there is none) or `failed` (none hold, or a file of the
project changed during the run, found by comparing the run's start snapshot), and a verdict below
`verified` ends the run through the same `VerificationError` path as a failed check. The agent's text is
never evidence: only the controller's check is.

## Pipeline, recipes, and resume

The `pipeline` strategy replaces the fixed six-task crew with a controller that is resumable,
skip-aware, and the base for parallel work and new modes. Design principle: the **controller is
deterministic code**; models do bounded work inside stages.

**Recipes** (`pipeline/recipes.py`, bundled in `modes/recipes/*.yaml`) are data. A `Recipe` is an
ordered list of stages; each has a `name`, a `kind` (`agent`: one small crew, one teammate;
`parallel`: one crew per work package of the plan, run in lanes ([Parallel execution](#parallel-execution)); `controller`: a registered
controller-side action, no model; `verify`: the controller's own verification, [below](#verification-and-repair); `analyze`: read-only analysts study an existing codebase, [below](#adopting-an-existing-project)), its `teammates`, `inputs` and `outputs` (contracts `spec`/`plan`,
or `file:<path>` for a promised file), `skip_if` conditions (`no_work_packages`), `retry`, and a
`verification_policy` (`artifacts`: the controller requires every promised file to exist and hold
real content; it never takes the agent's word). The `new` recipe is `design` (or `spec` → `plan`
for a large task) → `foundation` → `implement` → `integrate` → `verify` → `review` → `devops` →
`docs` → `release_notes`, of which a small task runs `design` → `implement` → `verify` → `review`
→ `release_notes` ([The lean team](#the-lean-team)); the architect's `Plan` decides which work packages exist, so a
CLI- or API-only project simply has no frontend package and a plan with none skips `implement`.
`verify` is run by the controller, not an agent; `review` is [below](#review-and-optional-stages). A
recipe is validated when loaded (unknown conditions, inputs no earlier stage produces, missing
teammates, duplicate names are one-line errors).

**Why a Flow.** `PipelineFlow(Flow[PipelineState])` (`pipeline/flow.py`) gives a typed state and
CrewAI's own `@start`/`@listen`/`@router` wiring. `begin` loads or creates the state, creates the
board's stage cards, and (when resuming) decides which stages are finished; one `@router`
(`advance`) runs the next unfinished stage and returns `next_stage`, `finished`, `failed`, or
`cancelled`, and listeners record the end. One router walking the recipe, rather than a hard-coded
method per stage, keeps recipes data. Each stage builds its own **small crew** (`pipeline/stages.py`:
one agent from `agents.yaml`, one task from `config/stages.yaml`, `output_pydantic` where a
contract is expected, the default tool groups, the stage's card id and any steering notes in the
prompt) and `StageExecutor` (`pipeline/executor.py`, over `executor_base.py` and `package_exec.py`) runs it: board moves, retries (the previous
error goes into the next attempt's prompt), the artifact check, and work packages in dependency
order. Unrecoverable failures end the stage `failed` and the run `failed`; a stage whose agent
stopped because the run was cancelled is never recorded as done.

**State and files.** Where each stage stands lives in the manifest (`StageRecord`: status,
attempts, why, and the workspace tree hash at its start and end); the contracts and per-package
status live in `pipeline.json`. The workspace *revision* (`runtime/snapshot.workspace_revision`) is
a hash of the path and content of every non-ignored file, so the run's own state never moves it.

**Resume** (`engineering-team resume <run_id>`, API `main.resume(run_id)`) reopens a cancelled,
interrupted, or failed run under the workspace lock and reloads the manifest and state; usage and
budgets continue from `events.jsonl`. A stage is **complete** only if its record says it succeeded
(or was skipped), the contracts it promised are in the state, **and** the workspace still fits: the
tree hash recorded at its end is the tree it left. Because a stage that started afterwards may have
changed files, the stage that follows the last complete one is checked against *its* recorded start
hash (chain intact), and only when that stage never started must the workspace equal the end hash.
A finished stage whose hash no longer fits is run again, and so is everything after it. Per stage:
`reuse` (nothing runs), `continue` (it started and did not finish; run it again, keeping finished
work packages), `rerun` (finished but the workspace changed; run it from scratch), `run`. Anything
that runs again after a previous attempt gets the instruction *"a previous attempt may have left
partial changes; inspect the workspace first, do not redo finished work"*. Resuming a run whose
manifest still says `running` (its process was killed) is allowed once the workspace lock is free.
Repeated resumes are safe: finished stages are never repeated. Resuming also clears a pause left by the stopped session. A run cannot be resumed when it
succeeded, used the removed `hierarchical` strategy, was started for another mode, its `request.md` no longer matches the
recorded hash, or its recipe changed since it started. **A changed request never reuses old
evidence**: `resume <run_id> --request …` with different text starts a new run instead.

*CrewAI's native checkpointing was evaluated and not used.* `checkpoint=True`/`CheckpointConfig`
(1.15) snapshots a crew's runtime state when events such as `task_completed` fire and restores a
crew, but a stage here is a single-agent, single-task crew, so a task-boundary checkpoint inside
one holds nothing between "not started" and "done"; the state that matters (files the tools
changed) lives in the workspace, which the tree hash covers, and our per-stage state is readable
JSON next to the manifest instead of a serialised runtime. Revisit when stages become multi-task.

**Cancellation** (`runtime/cancel.py`). `ctx.cancel_event` is the one stop signal; tools refuse to
work once it is set (telling the agent to wrap up), and the controller checks it before each stage,
while a stage waits for a pause, and after an agent returns. A first SIGINT or any SIGTERM sets it
(a second Ctrl-C raises `KeyboardInterrupt` for an immediate stop and ends the run `interrupted`);
`engineering-team cancel <run_id>` from another process writes `runs/<id>/cancel`, which a watcher
thread polls twice a second. A cancelled run ends `cancelled` (exit code 130) with the working stage
`cancelled` and its card `cancelled`; a budget stop is still `failed`. Cancellation is cooperative:
a model call already in flight finishes first.

**Board.** At run start the controller creates one `stage` card per recipe stage (the first `ready`,
the rest `backlog`) and moves them: `in_progress` when the stage starts (a reopened card first goes
through `ready`), `verifying` when the agent returns, `done` (stage success as the evidence) or
`failed`/`cancelled`; a skipped stage's card is `cancelled` with the reason, so it leaves the
totals. When the plan stage succeeds the controller creates one `work_package` card per package
under the `implement` card (dependencies, owned paths, criteria, assignee) and moves each as the
package runs. The agent is told its card id, receives the board, notes, and human tools, and gets
any pending steering notes at the start of each stage; `pause` is honoured before every stage and
every tool call. A board move the rules refuse is logged and reported as a
`pipeline.board_warning` event rather than breaking the run: the manifest is the record, the board
a view. Stage agents currently get one default tool table (`fs_read`, `fs_write`, `search`,
`command`, `dev`, `runtime`, `code_intel`, `board`, `notes`, `human`; `web` when enabled, `browser`
for the frontend and quality roles when its extra is installed); per-teammate groups replace it
when the team registry lands.

### Composing a recipe

A recipe can also be **composed** from a triage `TaskProfile` ([Triage](#triage)) instead of picked by
mode, so "fix this bug, add a test and update the docs" gets one recipe with all three. The data is
`modes/compose.yaml`; the code is `pipeline/compose_rules.py` (loading and checking) and
`pipeline/compose.py` (composing).

- **The stage library** holds every stage the bundled recipes use, once per variant (`verify_fix`,
  `verify_docs` and `verify_plain` are three `verify` stages), each with a cost class (`free`: the
  controller; `session`: one agent session; `sessions`: parallel packages, reviewers or analysts), a
  one-line summary, the entries it `requires`, and the stage exactly as a recipe writes it. The
  library is in one global order in which every entry's prerequisites come earlier.
- **The rules** map a profile to entries. Each has conditions (`target`, `primary`, `primary_not`,
  `intent`, `secondary`, `only`, `size_at_least`, `risk_at_least`), the entries to `add`, optional
  recipe `policies`, and a `why`. Rules are read top to bottom and the first entry to claim a stage
  name keeps it, so a specific variant (`tests` only → `tests_only`) comes before the general rule.
  Then each stage's `requires` that no rule added come in ("Needed by fix.").
- The stages are put in library order, the recipe is validated like any other (shape, actions,
  prompts, the team), and each stage keeps its reason in `StageSpec.why`. The run's manifest records
  them (`stage_reasons`), a composed recipe is pinned in the run directory like a recipe from a file,
  and `recipes compose --intent ...` previews the plan with each stage's cost and reason.

The main rules: an existing project gets `profile` and `baseline` (a review only `profile`); the
primary intent brings its stages (`bugfix`: `triage`, `reproduce`, `fix`, `verify` red to green;
`feature`: `design` or `spec`/`impact`, `implement`, `tests`, `verify`; `tests`, `docs`, `refactor`,
`upgrade_deps`, `security_audit`, `review`: the maintenance and review stages with their policies,
which apply only when that intent is the only one; `performance` and `devops` on an existing project:
the generalist `work` stage); a size of `medium` or more, or a risk of `medium` or more, adds the
codebase `map`; secondary `tests` and `docs` add characterization tests and `docs_legacy`; a new
project gets the `new` stages, with `docs` and `devops` run unconditionally when triage found them.
Runtime conditions stay (`small_task`, `minimal_team`, ...): the lean team still skips at run time.

**The bundled recipes are golden outputs**: composing the canonical profile of each (`new`:
`new_project` on a new project; `feature`, `fix`; the maintenance presets from `tests`, `refactor`,
`upgrade_deps`, `docs`, `security_audit`, and `custom` from `performance`; `review`; `adopt` from a
`question`; all medium size, low risk) gives exactly its stages, policies and mode
(`tests/test_compose.py`). A recipe named explicitly (`--task`, `--recipe`, a user recipe) always wins
over composition (`compose.choose_recipe`). Questions, research and plans without a project compose
nothing yet (the answer path is T15).

### The lean team

Most of a pipeline run's cost used to be structure rather than work: about ten cold-started
sessions, each re-reading the workspace and re-sending every tool schema with every call
(`benchmarks/results/2026-10-28/perf-baseline`). `pipeline/lean.py` sizes each run before any model
is called and the `new`, `feature` and `fix` recipes skip what that size does not need (a `review` or
`maintain` run keeps every reviewer and tier it asks for); every skip is a stage record
`skipped: <condition>` and a cancelled card, so the report says what was left out and why.

- **Size.** `assess` reads deterministic signals only: the request's length (over 600 words:
  large), the existing project's file count (over 40: medium, over 400: large) and `lean.size`.
  Once the plan exists, `refine` makes the task larger, never smaller (3+ packages: medium, 6+:
  large). The result (`LeanProfile`: size, high risk, requested ceremony, reasons) is in
  `pipeline.json` and the `lean.profile` event; a resume keeps it. The thresholds are provisional
  until the live evaluation (T33) measures them.
- **One design session** (`design`, `design_change` for a feature) returns a `Design` (the spec
  and the plan together) for small and medium tasks, on the worker tier; the controller splits it,
  checks both as before, and writes `docs/spec.md` and `docs/plan.md` itself. Large tasks keep a
  separate `spec` (product analyst) and `plan` (architect, on its own tier).
- **One implementer for a small plan.** A small task's plan is collapsed into one work package
  with every package's paths and criteria (`plan.collapsed` event; the architect's split stays in
  its description). A one-package plan skips `foundation` and `integrate` (`single_package`): the
  sole package runs unscoped with the recipe's `solo_prompt` (`implement_solo`) and does both.
- **No ceremony by default.** `devops` and `docs` run only when the request asks for them (CI,
  Docker, deployment; documentation, a user guide) or `lean.ceremony = "always"`. The release
  report is written by the controller (`release_notes`) from the spec, the plan, its own check runs
  and the review, so it costs no session and claims nothing the records do not show.
- **Repository modes.** A small fix or feature skips the codebase `map` (the few files are read
  directly) and a small fix skips `triage` (the reproduce session finds the cause itself).
- **Reviewers and tiers.** A small, low-risk task has one reviewer (`code_reviewer`, on the worker
  tier; `lean.reviewers` event). A request touching authentication, secrets, payments, uploads,
  SQL and the like is high-risk: the security reviewer and the architect's tier stay.
- **Hand-off packs** (`pipeline/handoff.py`). Every session's prompt carries the controller's
  record: up to 60 files (the most recently changed, each with its size and a SHA-256 prefix), the
  latest check results, and one line per earlier stage (quoted as the agent's words, not
  evidence), so a session does not list and re-read the tree to find out where things stand.
- **Fewer re-sent tokens per call.** Lazy tools sit behind `Find Tool`/`Use Tool`
  ([TOOLS.md](TOOLS.md#held-back-tools-find-tool-and-use-tool)), `Read Project File` does not
  resend a file unchanged since the session read it, contracts are sent as compact JSON, and the
  output schema an agent answers in leaves out `schema_version` and per-property titles.
- **Cache-friendly prompts.** In every task prompt the parts that change between sessions
  (`{handoff}`, the card, steering notes, the retry note) come last, after the request, the
  contracts and the project context, and the tool list never changes during a session, so a
  provider's automatic prefix caching can serve the start of each call; `profile` reports the
  cached share. (Explicit cache control for Anthropic is T26.)

### Dynamic team: the `team` strategy

The `pipeline` strategy runs every stage of its recipe in a fixed order, each a cold session. The
`team` strategy (`pipeline/lead.py`, `pipeline/lead_exec.py`, `pipeline/delegation.py`) is the
subagent pattern instead: one **lead** session works the whole task with a warm context, and calls
a teammate only when that pays for a session of its own. It is not 0.2.0's `hierarchical` crew,
whose manager had no tools and delegated everything through chat: the lead has the generalist's
full toolbelt and works itself, and the controller caps how much it may delegate.

- **The lead's recipe.** `lead_recipe` derives it from the mode's recipe (any recipe with a verify
  stage; `team-<name>`, deterministic, so a run resumes under it): the controller stages before
  verification (`profile`, `baseline`, ...) and the `reproduce` stage stay, every stage that does
  the work (design, spec, plan, packages, integration, tests, the map, reviews) becomes one `lead`
  stage, and `verify` and the controller's write-ups after it stay. The lead's teammate (the
  generalist) also writes a fix's reproduction and repairs failing checks, so no teammate works
  unless the lead called it. The lead's prompt (`{lead}`) carries what the skeleton established
  (the baseline, the reproduction seen failing, any criteria), the teammates it can call, and when
  to call them: guidance kept as data in `config/lead.yaml` (tunable in T21).
- **Delegation tools** (group `team`, the lead only; [TOOLS.md](TOOLS.md#team-tools)): `Delegate`
  (a teammate, a brief, owned paths, criteria, a step budget), `Delegate Parallel` (independent
  briefs with disjoint owned paths), `Ask Reviewer` / `Ask Security Review` (the current change),
  `Ask Debugger` (a failing check id; with paths it may fix them), `Ask Architect` / `Ask Analyst`
  (a question or plan draft). Each job is a small crew of its own run synchronously; the lead gets a
  compact result (at most about 1,500 tokens): what the controller saw (files changed, the commands
  the teammate ran, the controller's checks after a job that changed files, model calls, tokens and
  cost) and, labelled as such, what the teammate said or found. A teammate gets the brief and the
  hand-off pack, never the lead's history.
- **Controller limits.** The tools reach the controller through the run's `DelegationHub`
  (`runtime/delegation.py`), which only the lead's own session can use: a teammate, or the lead
  outside its session, gets a refusal, so delegation is one level deep. `delegation.max_delegations`
  caps the jobs per run (by size: small 2, medium 5, large 12; every brief of a parallel call
  counts), `delegation.max_iter` the model calls per job (the lead may ask for fewer); a job that
  writes must name its owned paths and runs with a write scope of exactly them (the reproduction
  stays read-only); parallel jobs' paths must not overlap; a teammate the roster does not allow for
  the mode cannot be called; the run's budgets cover every job, and a budget stop inside a job ends
  the lead's session and the run.
- **Board and timeline.** Every job is a `delegation` card under the lead's stage card (assignee,
  brief, the lead's reason, owned paths; the result as a comment; done, failed or cancelled by the
  controller) and runs in its own lane (`D1`, `D2`, ... with `lane.started`/`lane.finished`), so the
  UI's lanes and the report's timeline show it beside the lead. `delegation.started` and
  `delegation.finished` events carry the teammate, the reason, the files and the cost; the report's
  *Delegations* section lists who was called, why, and what it cost.
- **Verification unchanged.** The controller runs the verify stage's checks after each job that
  changed files (without repair; the lead reads the result) and as the `verify` stage after the
  lead hands back; the lead never marks anything done. When a deterministic security signal fired
  (auth, crypto, subprocess, SQL, deserialisation, secrets: in the request or in a file the run
  changed; `config/lead.yaml`) and no security review read the tree the lead handed back, the
  controller runs its own `security_review` stage, whose serious findings go to repair as in the
  pipeline.
- **Resume.** Each job is recorded in `pipeline.json` (`delegations`) before it starts. A run that
  stopped mid-job resumes the `lead` stage: the job is marked `interrupted` (its card cancelled,
  its use counted against the cap), and the lead's new session is told which jobs earlier sessions
  gave out and how they ended.

The static `pipeline` strategy stays for large new projects and whenever it is asked for
(`--strategy pipeline`); choosing between them per task is the [escalation
ladder](#escalation-ladder-the-auto-strategy).

### Escalation ladder: the `auto` strategy

`auto` (`pipeline/ladder.py`, `ladder_run.py`, `ladder_brief.py`, `ladder_contracts.py`) spends the
team only where the evidence says it pays. It climbs rungs, cheapest first, each a walk of the same
Flow machinery:

| Rung | What runs | Recipe |
|------|-----------|--------|
| `solo` | The lead alone (delegation cap 0), then the controller's verification and repair | `solo-<mode>`: the lead's recipe |
| `team` | The lead with its teammates on demand, the cap set by size ([Dynamic team](#dynamic-team-the-team-strategy)) | `team-<mode>` |
| `pipeline` | The mode's full recipe: design, parallel packages, reviewers | `<mode>` |

Best-of-N attempts become the rung above `pipeline` in a later 0.3.0 task.

- **Start.** The run is sized once, before any model is called ([The lean team](#the-lean-team)),
  and the size picks the first rung (`ladder.start_small` `solo`, `start_medium` `team`,
  `start_large` `pipeline`); a high-risk request starts at least on `ladder.start_high_risk`
  (`team`), and nothing starts above `ladder.max_rung`. The ladder climbs for the bundled (or
  composed) `new`, `feature` and `fix` recipes only: a maintenance task (grouped upgrades, a
  tests-only or docs-only policy), a review and a recipe of your own run as written, on the
  `pipeline` rung alone. The thresholds are provisional until T33.
- **Escalation.** A rung escalates when the controller's evidence says it failed: its verification
  still fails after the rung's repair rounds (`budget.max_repair_rounds` per rung: each rung starts
  a fresh verification record), or a work stage could not deliver after its retries (the agent is
  stuck). A budget stop, a cancel, a bug the team could not reproduce (`needs-info`), a setup problem
  (a model that cannot call tools) or a failing controller stage ends the run where it is: another
  rung would not change those. The next rung works **on the same workspace**; its sessions get the
  controller's brief through the hand-off pack: the checks that still failed, the diff since the run
  started, and the earlier attempt's trace in short (tool counts, failed calls, its last answer),
  quoted as data, not instructions. The ladder stops at `ladder.max_rung`.
- **The skeleton stays.** An escalated rung leaves out the controller stages and the reproduction
  before verification (profile, baseline, `reproduce`) and carries their facts over (`isolation`,
  `profile`, `baseline`, `repro`, `fix`, `lean`, the pinned checks' digests): the baseline must stay
  what the checks said before any change, and a reproduction seen failing is not written again.
- **Budget.** Before a rung starts, its cost is estimated from the benchmark priors
  (`bench/estimate.py`, by the task's size). The first rung steps down to the highest cheaper rung
  the budget can pay for; an escalation whose rung costs more than `budget.max_cost_usd` has left is
  not started, and the run stops with that reason. Without a cost limit (or a price) nothing is held
  back.
- **Visible.** `ladder.planned`, `ladder.rung_started`, `ladder.escalated` (from, to, reason, the
  failing checks, the rung's cost) and `ladder.stopped` events; a controller note on the card where a
  rung stopped, and every rung's cards titled with it (`Solo: Lead`, `Team: Verify`); the report's
  *Escalation* section (one row per rung: why, outcome, failing checks, cost).
- **Resume.** `ladder.json` is written before each rung starts. A rung's earlier stage records are
  renamed `<rung>:<stage>` (they stay in the timeline) so resume never takes them for the next
  rung's; `pipeline.json` belongs to the current rung and the one before is kept as
  `rungs/<rung>.json`. `resume` walks the rung the ladder reached, continuing its stages as any
  pipeline resume does, and escalates from there.

## Parallel execution

Work packages run side by side, safely, by **ownership, not by merging**. The architect's plan gives each
`WorkPackage` the gitignore-style globs it owns (`owned_paths`); the agent that works on it gets write tools
scoped to them (a `WriteScope`, so another path is an `ERROR:` naming what it may change), reads stay unrestricted,
and the shared root files (README, dependency manifests and lockfiles, CI configuration, `.gitignore`; the list is
`SHARED_FILES` in `pipeline/packages.py`) belong to `foundation` and `integrate`: they are denied to every scoped
agent, and a plan that gives one to a package is invalid. Git worktrees per package were considered and rejected:
merging needs an LLM to resolve conflicts, which is the nondeterminism ownership avoids.

**The roster** (`team/registry.py`). Built-in teammates are defined once, in `config/agents.yaml` (prompt,
`tier`, `tool_groups`, and so on). `build_roster(settings)` layers `.engineering-team/team.yaml` (or `team_file`) and the
`[team.<key>]` tables on top, field by field, validates everything (unknown tool groups, modes, missing prompt fields
for a new teammate) and returns a `Roster`, held on the `RunContext` as `ctx.team`; `check_ready` and `doctor` build it too, so
a bad definition is a usage error before any state is created. The pipeline builds its agents programmatically from it
(`CrewStageRunner._agent`: role, goal, backstory, the teammate's tool groups, its model from `Settings.resolve_model(key,
tier=, max_iter=)`), because a roster that changes per project cannot be a fixed set of `@CrewBase` methods. `Roster.assign` picks who works a stage when the one the recipe
names is disabled or missing (next listed, then the `generalist_engineer`, then the enabled teammate with the most similar
tools; a `team.fallback` event records it), and a work package's `role` is matched against `package_pool` (the stage's
teammates plus enabled teammates that list the stage under `stages`), which the plan prompt also shows the architect. A recipe carries its `mode`; when the flow starts it sets `Roster.mode`, which `usable` and `assign` use for a teammate's `modes` filter.

**Plan repair** (`pipeline/plan_repair.py`). After the plan stage, `repair_plan` fixes ownership mistakes that are mechanical (a shared root file named by a package is removed, a broad glob is narrowed to the subdirectories, a package left with no paths is dropped and its dependents inherit its dependencies) and the stage emits `plan.repaired` with each package's `before` and `after`; `plan_problems` then checks the result, so what cannot be repaired is still rejected with its original reason. Overlapping globs are not an error and need no repair (`packages.schedule` runs overlapping packages in turn).

**Intake and the spec stage** (`intake/`, `pipeline/spec_stage.py`). `RequestBundle.from_sources` is the one door
for a request (CLI, library, web UI): it normalises, merges (inline text, then files in order, `-` is stdin), caps
the size, refuses templates, and hashes (`request_hash`, the manifest's value, so the same text hashes the same from
any source). `--context-dir` is scanned by `scan_context_dir` and copied read-only to `.engineering-team/context/`
(`install_context`); agents reach it only through `Search Docs`, which adds that directory to its corpus. The
`spec` stage runs the read-only `product_analyst`, who returns the `Spec` contract (numbered `AC-n` criteria,
assumptions, non-goals, `confidence`, `blocking_questions`). The controller then (1) rejects an unusable spec
(`spec_problems`: ids must be `AC-n` and unique) through the stage retry, which is the one repair; (2) settles blocking
questions with `Clarifier`: with a human channel (`--interactive`) it asks up to `intake.max_questions`, via
`HumanChannel` (`question` / `question.answered` events) and re-runs the analyst once with the answers; otherwise, or for
unanswered questions, it records them as assumptions and open questions; (3) writes `docs/spec.md` itself from the
contract. The plan stage receives the spec as the contract and the original request for context; `AC-n` ids flow into
work packages and checks.

**Plan validation** (`plan_problems`) runs when the plan stage ends. Invalid means: duplicate or empty ids,
unknown or self dependencies, a cycle, a package that owns nothing or a shared file, a package that delivers no
acceptance criterion or names one the spec does not have. The architect gets one structured repair attempt (the
stage retry, with every problem in its prompt); a second failure fails the run with the list. Overlapping
`owned_paths` are *not* invalid: they are detected (`paths_overlap`, conservative: a glob is reduced to the
directory it is anchored in) and the packages are simply run in turn, with a `parallel.serialised` event.

**The engine** (`pipeline/parallel.py`, `run_work_packages`). The plan is cut into topological layers; within a
layer, packages with disjoint ownership run together in a thread pool of at most `parallel.max_parallel_agents`;
the next layer starts when the batch has finished. Each worker runs under `contextvars.copy_context()`, so the run,
stage, and **lane** tags reach everything it does, including CrewAI's own events. A lane is a number (the lowest
free one); events (`lane.started`/`lane.finished`/`lane.retry`, `tool.call`, `llm.call`) and the package's board card
carry it, and the lane owns its browser context and processes. Outcomes always come back in plan order, whatever
finished first, so reports are stable. With `max_parallel_agents = 1` nothing runs concurrently, packages run in
dependency order, and no write scope is applied (nothing runs beside the agent), which is exactly how a sequential
run behaves.

**Failure isolation.** A failing package is retried `retry` times (the recipe's `retry`, per package) with its error
in the prompt, then recorded `failed`; its siblings carry on, and the packages that depend on it are `skipped`
(their cards `cancelled`). When every runnable package has finished, the stage fails if a *required* package
(`WorkPackage.required`, default true) is missing, naming each one; finished packages are kept and `resume` redoes
only what is missing. A missing optional package does not fail the run: the **integrate** stage (sequential,
unrestricted scope, after `implement`) is told each package's status, runs the build and tests, fixes cross-package
contract mismatches, and records what it ran and changed in `docs/integration.md`.

**Cancellation and limits.** Cancelling sets the one `cancel_event` every lane's tools watch; the engine starts
nothing more, waits for the running lanes to wrap up, and raises `RunCancelled`. `parallel.max_rpm` installs one
sliding-window `RateLimiter` (`runtime/requests.py`) as every stage agent's CrewAI rate controller, so the cap is
shared across lanes instead of applying to each crew alone (provider 429s still rely on CrewAI's own retry). The
board's WIP limit for `in_progress` work packages is the same `max_parallel_agents`.

**Read-only fan-out** (`run_parallel_readonly(ctx, jobs, runner)`) is the same lane machinery for reviewers and
codebase analysts: each `ReadOnlyJob` (name, teammate, report path) gets `readonly_tools` — every read-only tool,
plus write tools that can touch nothing but the job's own report path — and a failing job is reported `failed`
without stopping the others. Results come back in job order.

## Verification and repair

**Principle: success is evidence the controller produced, never something an agent says.** The
`verify` stage of the `new` recipe (kind `verify`, `verification/`) is controller code. It decides
what runs, runs it through `ctx.backend`, judges the results, and only then involves an agent, and
only to repair.

**What runs** (`verification/profiles.py`). Per kind of check (setup, tests, lint, type check,
build, smoke): the user's `--checks` file, then the commands the plan declares, then what
`devtools/detect.py` finds in the project (one detector, shared with the developer tools); custom
user checks are added. Detected tests, lint, type-check, and build checks run the structured developer tools
(`DevRunner`), so the result carries the parsed `TestReport`/`DiagnosticReport`; commands from
the user or the plan run as plain commands (exit code, log tail, and the files the output points
at). Each project of a monorepo gets its own checks. A required `tests` check always exists.

**Results** (`verification/verifier.py`). `Verifier.run(checks)` returns one `CheckResult` per check:
`passed`, `failed`, `skipped` (not run because a required setup failed), or `unavailable` (it could not
run: the program or runtime is missing, or the command is one the controller may not run); a missing tool is
never a pass, and neither is a tool run that produced no usable result (`No tests ran` is `failed`).
Each result stores the exit code, duration, log path, a bounded log tail, and the **workspace revision** it ran
against; the batch is written to `verification/round-<n>.json`. Every check is a `check` card that the
controller moves (`in_progress → verifying → done` with the check id as evidence, `failed`, `blocked` when
unavailable, `cancelled` when skipped).

**Revision.** `verification_revision` is the workspace tree hash of `runtime/snapshot.py` minus the files
that are about the verification rather than what was verified: `docs/verification.md` (the controller's report),
`docs/qa-notes.md` and `docs/release-report.md` (agent narrative). Everything else counts, so writing the
report cannot make its own evidence stale and an edit to the code always does.

**Verdict** (`verification/verdict.py`). `verified` only if some check is required, every required check
passed, and every result is for the current revision. A required check that failed is `failed`; none failed
but a required one is `unavailable` or `skipped` is `partial`; results for another revision are stale and never
count. Lint and type checks are advisory unless `verify.static_required`.

**The report.** The controller renders `docs/verification.md` from the results (`verification/report.py`):
verdict, a table of every check, the output of those that did not pass, the criteria matrix, and the repair
log. It has no timestamps, durations, or run ids, so the same results give the same file. Acceptance
criteria are `verified` only when a *passing* check mapped to them (`criteria:` in the checks file) ran;
a passing suite whose test files mention a criterion id is only `referenced` (an agent wrote both); the rest are
listed under *Manual / unverified*. Unverified criteria do not block `verified`, but they are never hidden.
What the agents say lives in `docs/qa-notes.md`, appended by the controller and labelled as narrative.

**Repair loop** (`verification/loop.py`). Verify; while a required check `failed` and
`budget.may_repair(rounds)` and the run's other budgets allow, hand the failures (check id, command, summary,
suspect files, the end of the log) to the stage's first teammate through the `repair` prompt, then run the
checks **again**. The agent's reply is never read as evidence: if its round changed nothing in the project
the recorded results are reused and the round still counts. Rounds (`budget.max_repair_rounds`, default 3) are
counted over the whole run; a crashed repair attempt uses a round; `unavailable` checks are not repaired
(editing code cannot install a runtime). Each round is a `repair` card the controller closes: `done` with
the now-passing check ids as evidence, or `failed`.

**Ends and exit codes.** `verified`: the run `succeeded` (exit 0). `failed`: the stage raises
`VerificationError`, the run ends `failed`, the manifest's `verdict` is `failed`, exit code **3**. `partial`:
the same with exit code **4**. Both are resumable (`resume` gives the stage fresh repair rounds) and both
leave `docs/verification.md` with the reason. Budget and cancel stops are unchanged (exit 1 and 130).

### Review and optional stages

`review` is a stage kind of its own. Its teammates (`code_reviewer`, `security_engineer`) run through
`run_parallel_readonly` ([Parallel execution](#parallel-execution)): one lane each, with the read-only tools of the
teammate's groups (plus `Dependency Audit`, the one command-running tool that only inspects) and a write tool that can
touch nothing but a report path. Each agent returns a `ReviewReport` (a summary and `Finding`s) as its structured
answer; `pipeline/review.py` then validates the findings (a real severity, text, a path inside the project; the
reviewer's role is set by the controller), merges duplicates (same file, lines within three of each other, at least half
the words in common: one finding at the higher severity naming both reviewers), sorts them by severity and numbers them
`F-1...`, so the result does not depend on which reviewer finished first. The controller writes `docs/review.md`
itself (it does not count towards the verification revision, like the other controller-written reports).

Findings at or above `review.fail_on` go to the repair agent of the `verify` stage (the `debugger`), with the
`repair_review` prompt: one repair round from the shared `budget.max_repair_rounds` (`VerificationLoop.repair_findings`;
none left means the findings are reported, not fixed), then the verify stage runs again (`VerificationError` fails the
review stage if the project no longer verifies). The repair card closes with the checks that then passed as its evidence,
or fails if the agent changed nothing. The findings are not reviewed again, and the verdict is the checks' alone: a
review finding never makes a verified project unverified. One reviewer failing is noted in `docs/review.md` and the stage
carries on; all failing fails the stage (no retry, as a retry would pay for the reviewers again).

`review`, `devops`, and `docs` are `optional` stages with `skip_if: [minimal_team]`: the `smoke` profile and
`team_profile = "minimal"` skip them, and so does a stage whose teammates are all disabled; `devops` and `docs` also
need the request to ask for them ([The lean team](#the-lean-team)). Conditions take the
pipeline state and the settings (`CONDITIONS[name](state, settings)`). Whatever `devops` and `docs` change is covered by the
final gate.

**Final gate.** After the last stage, if the recipe has a `verify` stage, the controller compares the
workspace to the verified revision; if a later stage (delivery, documentation) changed anything, it runs
the checks again (repairing with whatever rounds are left) and fails the run if they no longer pass.
It also re-renders the report from the stored results, so an agent cannot leave its own words in it.

**Trust boundary.** The checks file is validated and pinned at the start of the run (see
[CONFIGURATION.md](CONFIGURATION.md#verification-verify-and---checks)). Checks run through the same
execution backend as every other command; under the Docker sandbox the run directory is hidden from commands,
while on the local backend an agent with a command tool can still reach it ([SAFETY.md](SAFETY.md)). Only the
`pipeline` strategy verifies: `single` keeps the interim artifact guardrails and never
reports `verified`.

## Adopting an existing project

Everything before this builds from nothing. An existing project needs four things first: to be understood, to be
worked on somewhere safe, to have its starting health recorded, and to have its code explained. All of it lives in
`modes/` and is composed by the `adopt` recipe (`modes/recipes/adopt.yaml`); the repository modes start with it.

```text
isolate()  ──►  workspace (branch | worktree | copy)  ──►  adopt recipe:  profile ─► baseline ─► map
 (before the run)                                           controller      controller    analyze
```

**Profile** (`repo_analyzer.analyze_repo`, `repo_commands.py`, `repo_profile.py`). Deterministic, no model, no writes:
one walk of the non-ignored files (`.gitignore`, heavy directories, and `.engineering-team/` skipped, capped at
20,000 files) gives languages, sizes, manifests, test directories and files, CI files, convention files, and entry
points (declared ones, from `package.json` and `[project.scripts]`, first); `devtools.detect.find_stacks` (the one
stack detector the verifier uses) gives the projects; `repo_commands.detect_commands` turns scripts, Makefile and
justfile targets, tox and nox, and the stack's tools into `DetectedCommand`s, project-defined ones first, each with
the file it came from. Git state comes from a `GitPort` (a run's, or `standalone_git`, whose home, caches, and logs
go to a throw-away directory so nothing lands in the project). The result is a `RepoProfile` contract, saved to
`.engineering-team/repo-profile.json` and held in `PipelineState.profile`.

**Isolation** (`isolation.isolate`, before a run exists, because the run lives in the workspace it returns). A clean
Git repository gets a branch in place, a dirty one a worktree at `.engineering-team/worktrees/<run-id>`, and a
directory that is not a repository is copied; the rules and the refusals are in
[USAGE.md](USAGE.md#adopting-an-existing-project) and [SAFETY.md](SAFETY.md#adopting-an-existing-project).
`GitPort` is worktree-aware for this (`git_dir()`/`common_dir()` follow a linked worktree's `.git` file, so
`info/exclude` and the real index are found), and `exclude_controller_state()` is the public way to keep
`.engineering-team/` out of a repository. The result is an `Isolation` (mode, source, workspace, branch, base
branch and commit, notes), recorded in the workspace's `.engineering-team/isolation.json`; the workspace is marked
adopted in `owner.json`.

**Baseline** (`baseline.run_baseline`, `baseline_report.py`). The controller runs the detected checks (the
verifier's own `build_checks` defaults and `Verifier`, so the same runners, parsers, backend, and limits) once
before any change, writes `BaselineReport` to `.engineering-team/baseline.json` and the pipeline state, and
reports the batch as `verification/baseline.json` (not a verification round). Failures become stable keys:
`test:<dir>:<test id>`, `lint:<dir>:<file>:<rule>` (no line numbers, so an edit elsewhere in a file keeps a
known failure known), `build:<dir>`; a test command that found no tests is `skipped`, not failed. Comparing a
later verification against the baseline ("no new failures") is the verify stage's job in the repository modes.

**Map** (`codebase_map.py`, `map_stage.py`; the `analyze` stage kind). `plan_chunks` splits the source (files of a
known language) into at most `analysis.max_chunks` chunks: top-level directories are the units, a unit holding more
than half the source is split one level down (up to three), and the two smallest are merged until the limit is met;
sizes and names decide, so the plan is deterministic and covers every file once. Each chunk goes to a
`codebase_analyst` through `run_parallel_readonly` (one lane each; read-only tools, and one scratch path under
`.engineering-team/tmp/analysis/` as their only writable place) and returns a `ChunkAnalysis`; one more call
combines them with the profile into a `CodebaseMap`, and the controller renders it (`render_map`: its own headings,
every agent line flattened to one line and capped) to `.engineering-team/codebase-map.md`. The file's first line
holds the hash of the tree it describes (`workspace_revision`), which is the cache: an unchanged tree reuses the
map without a model call (`map.cached`). A failed chunk is reported in the map and left out; the stage fails only
when no chunk could be analysed or the synthesis fails. `map_context(root, cap)` is the size-capped copy later modes
put into agent context. Events: `map.chunks`, `map.chunk_failed`, `map.written`, `map.cached`,
`baseline.recorded`.

**Recipes and CLI.** `adopt.yaml` has `profile` and `baseline` (controller actions `repo_profile` and `baseline`,
registered in `modes/adopt.py`) and `map` (kind `analyze`). `engineering-team analyze --repo PATH` computes the
profile with no run at all; with `--deep` it opens a run in the project itself (mode `analyze`, no ownership marker,
`git.enabled = false` so no stage commits, the workspace lock held) and runs the recipe's read-only subset
(`analysis_recipe()`: profile and map; no baseline, because that runs the project's commands). The repository
modes will inline or reuse the three stages; a recipe cannot include another yet.

### Feature mode

`modes/recipes/feature.yaml` is the `adopt` stages followed by `spec` (prompt `spec_change`), `impact` (the architect;
output `plan`), `implement` (a `parallel` stage with prompt `implement_change` and `allow_shared`), `tests`, `verify`,
`review` (prompt `review_change`), and `summary` (the controller action `change_summary`). Two stage options exist for
it: `prompt` names the task prompt in `config/stages.yaml` instead of the one the kind or name picks, and `allow_shared`
lets a parallel stage's packages own shared root files (`plan_problems(allow_shared=)` and the write scope's `deny`
follow it).

- **Isolation first.** `cli/repo_mode.run_repository_mode` (shared by `feature` and `fix`) calls `isolate()` (a copy is always made a repository), registers the
  workspace with `runtime/run_index.register_workspace` (a `.external-workspaces.json` beside the workspace root, so
  `runs`, `status`, `board`, `cancel`, `diff`, and `resume` find a run that lives in your repository or in a worktree
  under it), and opens the run with `_open_run(workspace=..., run_id=..., recipe=...)` (mode `feature`, strategy
  `pipeline`). `resume` reopens such a run in its own workspace (`open_resume(workspace_dir=)`).
- **Reports out of the diff.** `RunContext.reports` (`runtime/reports.py`) says where the controller's write-ups go:
  the project's `docs/` (a new project) or `<run dir>/reports/` (an adopted workspace, `RunContext.create(adopted=True)`).
  The spec, verification, QA-notes, and review writers all go through it.
- **Baseline comparison** (`verification/baseline_compare.apply_baseline`) runs inside the verification loop whenever
  the pipeline state holds a baseline: a failed detected check is turned into `passed` when every failure key is in
  `known_failures` (recorded on the result, with the new ones for a mixed outcome), the plan's commands are not used
  (`build_checks(plan=None)`), and `failures_for_repair` separates new failures from known ones. The raw results stay in
  `verification/round-<n>.json`.
- **Change report** (`modes/change_report.py`): `GitPort.changes(base)` (a temporary-index `diff --raw --numstat`) gives
  files and lines since `state.isolation['base_commit']`; `modes/diff_noise.measure` counts the lines outside the plan's
  owned paths, the test directories, and test-named files. The review stage adds the section to the review report;
  `change_summary` writes `changes.patch` (`GitPort.export_patch`) and `CHANGE_SUMMARY.md` to the run directory.
- **Squash** (`git.squash`) is done by `Checkpoints.final`, after the last stage and the final gate:
  `GitPort.squash(base, message)` (`reset --soft` to the base, then one commit), refused unless the base is an ancestor.

### Fixing a bug

`modes/recipes/fix.yaml` is the `adopt` stages followed by `triage` (the `debugger`; output `triage`), `reproduce` (a
stage of kind `reproduce`; output `repro`), `fix` (output `fix_note`), `verify` (prompt `repair_fix`), `review` (prompt
`review_fix`), and `summary` (the `change_summary` action again, with a fix section). The contracts `Triage`, `Repro`
and `FixNote` (`modes/fix_contracts.py`) are the debugger's accounts and are never read as evidence; `FixRecord`, in
`PipelineState.fix`, is what the controller saw.

- **Inputs.** `cli/fix_command.fix` composes the request (report, then the end of the trace, then the repro command;
  `modes/fix_input.compose_request`) and writes the structured inputs (`repro`, the whole trace, `allow_unreproduced`) to
  `<run dir>/fix-input.json`, which the controller reads and agents cannot reach; a resumed run finds it again.
  `modes/trace.py` parses Python, Node, and Java traces (innermost frame first) and maps their frames to files that exist
  in the workspace, whatever the leading path was where the trace came from.
- **The red gate** (`modes/fix_repro.run_reproduction`, run by the executor's `_reproduce`). Each attempt calls the
  debugger, then the controller runs the returned command through `Verifier` (the same backend, allowlist, command
  gate, and timeout `fix.repro_timeout` as any check; label `repro-red-<n>`). `not_a_failure` decides what is not a
  reproduction: a pass, a check that could not run, a timeout, exit 126 or 127, and pytest's exit codes 2, 4 and 5. On
  red the record keeps the command, files, SHA-256 digests of the files, and the run (`fix.red` event). The person's
  `--repro` command is run first and recorded (`user_red`); it becomes a required `repro-user` check only if it failed.
- **Needs info.** When `fix.max_repro_attempts` is spent and `--allow-unreproduced` was not given, the stage raises
  `NeedsInfo` (never retried), `state.needs_info` holds the questions, and `_result` reports the run verdict
  `needs-info` (`RunVerdict`; the run is `failed`, exit code 4). With the flag, the stage records the placeholder
  `Repro` so a resume treats it as finished, and the fix goes on without a `repro` check.
- **Green.** `VerificationLoop` adds `fix_checks` to the checks (`build_checks(extra=)`): the agent's command as a
  required `plan`-sourced check (allowlisted, like any plan command) and the person's as a `user`-sourced one. A pass
  is recorded as `fix.green` only if the pinned files are unchanged; `tampered` turns an edited or deleted file into a
  `failed` verdict whatever the checks say. The comparison with the baseline is untouched (it never relaxes custom
  checks).
- **Write scope.** Once the bug has been seen failing, `StageExecutor._call` gives every later agent call (the fix, the
  repair rounds, review repairs) a `WriteScope(allow=("**",), deny=<reproduction files>)`; the reproduction stage itself
  is unscoped because it writes them.
- **Run record.** `pipeline.json` (`fix`, `triage`, `repro`, `fix_note`, `needs_info`), `events.jsonl` (`fix.red`,
  `fix.green`, `fix.not_red`, `fix.unreproduced`, `fix.needs_info`), `verification/repro-red-<n>.json` and the
  verification rounds hold the evidence; `modes/fix_report.py` renders it into `CHANGE_SUMMARY.md`.

### Maintenance, review, and your own recipes

`modes/recipes/` holds `add-tests`, `refactor`, `upgrade-deps`, `docs`, `security-audit`, `custom` and `review` next to the
others; `cli/maintain_command.maintain` is a thin caller of the same `run_repository_mode` as `feature` and `fix`.

- **Policies** (`modes/policies.py`, `modes/policy_checks.py`). A recipe lists policy names; `VerificationLoop` evaluates
  them after the checks against `GitPort.changes(base)` and appends one required `CheckResult` per policy
  (`policy:<name>`; `unavailable` when there is no base, never a pass). A violation therefore fails the verdict, shows
  in the report, and goes to the repair agent with the offending paths. The same classification (`is_test_path`,
  `is_doc_path`, `is_manifest_path`) feeds the named `write_scope`s that `StageExecutor._call` turns into a `WriteScope`,
  merged with the reproduction protection of `fix`.
- **Coverage** (`modes/maintain`): the `coverage_before`/`coverage_after` actions call `DevRunner.coverage` for each
  project root and keep a `CoverageDelta` in the pipeline state; `{coverage}` hands the least-covered files to the test
  writer.
- **Refactor precondition**: `refactor_precondition` raises `NeedsInfo` (`modes/needs_info.py`, shared with `fix`) unless
  the baseline's tests passed, so the run ends `needs-info` (exit 4) before any change.
- **Upgrades** (`modes/upgrade.py`). The `upgrade` stage kind runs `bisect_upgrades` over the plan's upgrades: each
  attempt has the agent edit the manifests, checks that only manifests and lockfiles changed, installs
  (`DevRunner.install`), runs `verification/quick.quick_verify` (the detected and pinned checks, judged against the
  baseline, no repair), and either commits (`upgrade: ...`) or undoes the group with `GitPort.restore("HEAD")` and halves
  it. Outcomes are saved as they are decided, so a resume does not decide them again.
- **Audit and findings**: `dependency_audit` runs `DevRunner.audit` per project (`D-n` findings with the tool's severity
  mapped to the review scale), the `audit` review stage has `repair: false`, `security_report` merges and writes
  `findings.json`/`findings.md`, and `security_gate`/`review_gate` (`modes/findings_report.gate`) set the verdict and raise
  `VerificationError`, which is what makes the exit code 3.
- **`review`** (`cli/review_command.py`) opens a run in place (like `analyze --deep`: no isolation, `git.enabled = false`,
  the workspace lock, reports in the run directory) with the `review` recipe: `review_target` finds the merge base (or the
  dirty tree's `HEAD`, or the default branch) and records it as the run's base commit; the reviewers use the same
  prompt machinery as the `review` stage of the other modes.
- **Recipe sources** (`pipeline/recipes.py`). `load_recipe(name, root)` looks in `<root>/.engineering-team/recipes`, then
  `~/.config/engineering-team/recipes` (`settings.user_config_directory`), then the bundled ones. A recipe from a file is
  also checked by `pipeline/recipe_check.check_recipe` (controller actions that exist, prompts that exist,
  teammates on the team; the run start checks the team again with the real roster), reports each problem with its file,
  stage, and field, and must be named like its file. `Recipe.source` and `path` are not part of its digest. Stages
  gained `instructions` (the `custom` prompt wraps them), `write_scope`, `repair`, and the kinds `upgrade` and
  `reproduce`; recipes gained `policies`. The recipe is checked before the team gets a branch, worktree, or copy.
- **Run options** (`modes/run_options.py`): `PipelineState.options` is read from `run-options.json` at every start, so
  conditions such as `fixes_not_requested` hold on resume.

## Settings and model routing

`settings/` is the only package that reads configuration (`from engineering_team.settings import Settings` and every other name keep working). It builds an immutable, typed
`Settings` object from layered sources (CLI overrides > environment > project TOML > user TOML >
defaults), remembers which layer set every value, and never mutates `os.environ`. The profile (for
example `smoke`, chosen by CLI, environment, config file, or the request's marker) travels inside
`Settings`.

`model_routing.py` is pure data: provider presets mapping tiers (`max`, `lead`, `reviewer`,
`worker`, `cheap`) to model IDs, model facts that CrewAI cannot discover (context window, whether a
model needs the Responses API), and the profile definitions. `Settings.resolve_model(role)` layers
preset, tier, profile, and per-role overrides into a `ResolvedModel`; `crew.build_llm` turns that
into a CrewAI `LLM`, sending only parameters the provider accepts (reasoning effort is OpenAI/Azure
only). The architecture preserves the quality/cost distinction: the lead runs on the quality tier
and specialists on a cheaper one rather than using the flagship model for every tool call. Optional
documentation MCP servers are off by default, so the crew has no hidden network dependency.

Defaults, tiers, profiles, and every setting are documented once in
[CONFIGURATION.md](CONFIGURATION.md).

The bundled `tiny-notes` example request (`--example tiny-notes`) carries a smoke-profile marker,
which selects the `smoke` profile unless a profile was chosen explicitly.

## Extension points

`src/engineering_team/extensions/` holds what lets a project change the team without changing the
code; the user-facing description is in [CONFIGURATION.md](CONFIGURATION.md), the trust model in
[SAFETY.md](SAFETY.md#extensions-mcp-plugins-and-hooks). `extensions/config.py` has the settings
models (`McpServer`, `ConventionsSettings`, `PluginSettings`, `EmbedderSettings`, `HooksSettings`) and
imports nothing from the package, so `settings/` can use it; the other modules read a finished
`Settings`.

- **MCP** (`mcp.py`). `mcps_for(settings, teammate)` is the one place that decides which servers an
  `Agent(mcps=...)` gets: the `docs_mcp_urls` alias for teammates with `mcp:docs`, and each
  `[mcp.<name>]` for teammates with `mcp:<name>` or named in its `roles`, as CrewAI's `MCPServerHTTP`,
  `MCPServerSSE` or `MCPServerStdio` with a static `allow_tools` filter. The agent builder
  (`pipeline/stages.py`) calls `agent_extensions` (`agents.py`), which adds MCP and, when
  configured, knowledge.
- **Conventions** (`conventions.py`). `RunContext.create` reads the repository's convention files once
  (`ctx.conventions`); `pipeline/stages._context` puts the text into the prompt context after the
  reference documents and before the codebase map, for every stage but the codebase analysis.
- **Knowledge** (`knowledge.py`). The controller reads `knowledge.sources` and hands CrewAI
  `StringKnowledgeSource`s with the `embedder` from the settings; nothing is embedded until an agent
  runs.
- **Plugins** (`plugin_loader.py`, API in `engineering_team/plugins.py`). `load_plugins(settings)` imports
  entry points and, if allowed, the project files, validates each tool, and caches the result per
  configuration and file state; `RunContext.plugins` carries it. `build_tools` iterates
  `(*CATALOGUE, *ctx.plugins.specs())`, so a plugin tool is an ordinary `ToolSpec` whose factory wraps
  the function in `ToolEnv.run`. `build_roster` accepts the plugin groups as valid `tool_groups`.
- **Hooks** (`hooks.py`). `RunRecorder.stage` calls `before_stage` and `after_stage`, and `_finish`
  calls `on_finish`, through one `HookRunner` per run. Commands go through `LocalBackend` (never the
  run's own backend), webhooks through `post_json`; every outcome is a `hook.ran` event and no failure
  propagates.

## Web UI backend

`engineering-team ui` serves a FastAPI app (`ui/`, optional extra `ui`) under `/api/v1`. It is a thin layer
over what the CLI already writes; nothing runs a team inside the server.

- **Runs are subprocesses.** `ui/launcher.py` starts `python -m engineering_team --workspace-root R --run-id ID
  [--answers-via-inbox] <mode> ...` in its own session, so a run outlives the server and cancels the way a CLI run
  does (the `cancel` flag, SIGINT). The server names the run first (`--run-id`, hidden) so it can find it again; per
  start it keeps a record (pid, log file) under `<workspace root>/.engineering-team-ui/`, which explains a run that
  died before writing a manifest and counts the runs it has going (`ui.max_concurrent_runs`; the workspace lock still
  admits one writer per project). Per-run options reach the process as CLI flags or as `ENGINEERING_OVERRIDES`
  (budget, parallelism, roster toggles), validated against the settings before anything starts.
- **State is read, not held.** `ui/eventlog.py` tails `events.jsonl` incrementally (sparse byte index for resuming at
  an event, a digest of teammates' tool calls, open questions, and cancel/finish state); `ui/views.py` builds run,
  board, teammate, and card views from it and from `board.json`; the report and the diff are the CLI's own.
- **Steering** goes through the inbox (`runtime/inbox.py`: `note`, `pause`, `unpause`, and `answer`, which delivers
  a reply to a question the team asked: `HumanChannel` is enabled in the run by `--answers-via-inbox`). A command for a
  run nobody is working on waits for the next resume.
- **Replay.** `GET .../board?at=SEQ` folds the `board.*` events up to `SEQ` (each carries the whole card) into the
  board as it stood, with progress measured at that event's time: the same `SEQ` always gives the same board.

| Endpoint (`/api/v1`) | What it does |
|---|---|
| `POST /runs` | Start a run (`mode` new/feature/fix/maintain/review, request text, uploads as multipart, `repo`, options); 202 with the run id, 429 at the concurrency limit, 422 for a bad request |
| `GET /runs`, `GET /runs/{id}` | Runs of every project; one run with its manifest, progress, usage, budget against its limits, checks, `attention` (questions, blocked cards, budget, failed checks), open questions, last event `seq`, and the process the UI started for it (`status` is `starting` before the manifest exists, `failed` with the process log's tail if it never got one) |
| `GET /runs/{id}/events` | SSE tail of `events.jsonl`; `id` is the event `seq`; resume with `Last-Event-ID` or `?after=`; `?follow=false`, `?types=board.,stage.`; ends with `event: end` once the run is over |
| `POST /runs/{id}/cancel`, `/resume` | As the CLI commands (resume waits briefly for the old process to exit) |
| `GET /runs/{id}/questions`, `POST .../answer` | Questions the team asked (needs `interactive`, the default) and a reply (empty text declines) |
| `GET /runs/{id}/board[?at=SEQ]` | The board now, or replayed |
| `GET /runs/{id}/cards/{card}` | A card with its history, comments, board events, and the tool-call trail of its assignee while it was in progress |
| `GET /runs/{id}/trajectory/{seq}` | One entry of `trajectory.jsonl` (a tool call with its output, or a model message); the trail's `trajectory_seq` names it; 404 when not recorded |
| `POST /runs/{id}/cards/{card}/comments`, `/notes`, `/pause`, `/unpause` | Steering through the inbox |
| `GET /runs/{id}/agents[?at=SEQ]` | Teammates now or as they stood after an event: `working`, `waiting` (for a human), `blocked` or `idle`, current card, last tool call, counters, tokens, cost, model |
| `GET /runs/{id}/timeline` | Stage and lane bars (seconds from the start), review findings and criteria coverage so far |
| `GET /runs/{id}/artifacts[/path]` | Screenshots, command logs, reports, the patch: only what the listing names |
| `GET /runs/{id}/files[/path]` | Read-only project files, with the agents' path rules plus: no secrets (`.env`, keys, `.ssh/`, ...) |
| `GET /runs/{id}/diff`, `/report` | The change since the starting commit (feature/fix/maintain); the run report (`?format=html|md`, served with a sandboxing CSP) |
| `GET /runs/{id}/results` | The run report as JSON (banner, summaries, criteria coverage, checks, findings, parsed diff, screenshots, warnings) plus `merge`: where the work is and the commands to take it (`null` for a new project) |
| `GET /config`, `/doctor`, `/team`, `/recipes` | Masked settings, the machine check, teammates, recipes |
| `GET /repo/inspect?path=` | A project directory's stack and Git state, and how the team would be isolated from it (`branch`, `worktree`, `copy`) |
| `GET /options[?provider=&profile=]` | What the start form offers: modes, request templates, defaults, the models (with prices) the choice would use, which optional tools this machine has, and the demo sample project when serving `--demo` |
| `GET /health`, `GET /`, `GET /static/...` | Version and whether a token is needed; the app shell. These are the only calls that need no token |

**App shell** (`ui/static/`, served at `/` and `/static/`). Vanilla ES modules and one stylesheet, no build step and no
third-party code: the pages are small, nothing needs a framework's reactivity, and a bundler or CDN would add a
supply chain to a tool that runs with your model keys. Each screen is a module with `mount(root, params)` under
`js/pages/`; `js/dom.js` builds elements from text only (never from HTML strings), `js/ui.js` is the one component
vocabulary (chips, meters, tabs, states) the live dashboard reuses, `js/api.js` is the only code that calls the server
(it adds the header mutating calls need and the bearer token, and reads the event stream with `fetch` because
`EventSource` cannot send headers). Routes are hash-based (`#/runs/ID`), so the server has nothing to rewrite. The
page is sent with a policy that allows only same-origin scripts and styles (no inline code, no `eval`), which a test
enforces for every shipped file. `ui --demo` runs `ui/demo.py`, the CLI with a scripted stage runner in place of the model.

**Dashboard data flow.** The run page keeps one `Store` (`js/dash/store.js`): it loads `GET /runs/{id}` (progress, usage, budget, checks and
`attention`, all computed in `ui/views.py`), `/board`, `/agents` and `/timeline`, then applies the whole card each
`board.*` SSE event carries and re-fetches the rest at most every second or so. Replay swaps those for `/board?at=SEQ` and
`/agents?at=SEQ`, folded server-side from `events.jsonl`, so the views never compute state.

Errors are `{"error": "..."}`. **Guard** (`ui/security.py`, one ASGI middleware): in local mode only
`localhost`/`127.0.0.1`/`[::1]` Host headers are served (DNS rebinding); a request with a foreign `Origin` or
`Sec-Fetch-Site: cross-site` is refused and no CORS header is ever sent; mutating calls need `X-Engineering-Team: 1`
(a browser cannot add it cross-origin without a preflight the server never approves); beyond localhost
(`--allow-remote`) every call but `/health` and the app shell needs the random bearer token printed at start; bodies are capped
(`ui.max_request_bytes`, `ui.max_upload_bytes`; chunked bodies are refused). The server reads and writes only the
run directories, the UI's own `.engineering-team-ui/` folder (uploads, logs, start records), and the project files
it serves read-only.

## Benchmark harness

`engineering-team bench` (`src/engineering_team/bench/`, `cli/bench_command.py`) measures the team
against the task suite in `benchmarks/`; [BENCHMARKS.md](BENCHMARKS.md) is the method and threat
model. The data flow of one batch:

```text
tasks.load_suite -> select_tasks -> plan.plan_runs (task x strategy x repeat)
   -> batch.run_batch (thread pool; BudgetLedger reserves each run's cap)
        -> execution.execute_run
             prepare.prepare      run dir, workspace (fixture as a Git repo), argv + env
             subprocess           `python -m engineering_team --json ...`  (or bench.fake_team)
             metrics              the JSON summary + events.jsonl (repairs, tool/setup failures, cost)
             acceptance           hidden checks, one process per criterion, on a workspace copy
             results.write_result result.json
   -> report.render_markdown / render_csv      (stats: Wilson interval, cost per success)
```

The harness imports no CrewAI itself: the team is always a subprocess (a crash, hang or runaway
cost cannot take the batch down; a timeout kills the process group), and only the offline team
(`fake_team`, a real `Agent` on a `ScriptedLLM` writing the reference solution through the real
tools) loads it. The verdict is computed from the hidden checks alone; the team's own status is
recorded, never trusted.

## Module map

`src/engineering_team/`, by responsibility. Dependencies point downwards: nothing in a lower row imports
a higher one, apart from a few imports deferred to avoid cycles (`settings` and `contracts` import no run
code at load time; the tools import the runtime context only for type checking).

| Layer | Modules | Responsibility |
|-------|---------|----------------|
| Entry | `main.py`, `run_prep.py`, `request_sources.py`, `outcome.py`, `api.py`, `__main__.py`, `cli/` | The Typer commands, the live Rich views, `doctor`, and the `run_crew` entry point `crewai run` uses; run preparation, request sources, exit codes, `resume` |
| Web UI | `ui/` | FastAPI app over the run directories, SSE events, the static single-page app, `ui --demo` |
| Orchestration | `pipeline/`, `modes/`, `team/`, `llm.py` | Recipes, the Flow pipeline, parallel work packages, resume, strategies; the mode recipes and their actions; the roster; model construction |
| Control | `verification/`, `board/`, `git/`, `report/`, `intake/` | Controller-run checks and bounded repair, the task board and notes, `GitPort` and checkpoints, the run report, requirements intake and triage |
| Run state | `runtime/`, `contracts.py`, `artifacts.py`, `atomic_io.py`, `workspaces.py` | `RunContext`, events, budget, usage, processes, the run store, locks, cancellation; the typed contracts; crash-safe file writes; project directories |
| Agent tools | `tools/`, `devtools/`, `codeintel/`, `webtools/`, `browsertools/` | Every tool an agent can call ([TOOLS.md](TOOLS.md)) and the engines behind them |
| Execution | `execution/` | `ExecutionBackend`: the local backend and the Docker sandbox; no tool spawns a process any other way |
| Configuration | `settings/`, `model_routing.py`, `pricing.py`, `config/`, `data/`, `extensions/`, `plugins.py` | Layered settings, model presets and routing, prices, the built-in teammates and prompts, MCP / conventions / plugins / hooks |
| Evaluation | `bench/`, `testing/` | The benchmark harness; `ScriptedLLM` and the offline test helpers (shipped in the wheel) |

Modules stay under 450 lines (`tests/test_module_size.py` fails otherwise); a module that grows past it
is split by responsibility and keeps its public import path, so callers and plugins do not change. The
larger ones today:

| Package or module | Parts |
|-------------------|-------|
| `settings/` | `sections` (one model per `[table]`), `model` (`Settings`), `env` (the `ENGINEERING_*` variables), `loading` (layering), `masking`, `common` |
| `main.py` | `run_prep` (options to a prepared run), `request_sources`, `outcome` (exit codes), `api` (`resume`); `main` is the entry and re-exports them |
| `tools/` catalogue | `spec`, `catalogue_project`, `catalogue_dev`, `catalogue_external`, `catalogue_team`, `registry` (the builder), `finder` (Find Tool and Use Tool) |
| `git/` | `core` (the safe command line), `reads`, `writes`, `port` (`GitPort`) |
| `pipeline/` executor | `executor_base` (agent calls), `package_exec` (work packages), `lead_exec` (the lead stage), `executor` (stages); `lean` (task size and what it skips), `handoff` (hand-off packs), `release_notes`; `lead` (the lead's recipe and prompt), `delegation` and `delegation_briefs` (checking, running and reporting delegated jobs), `lead_contracts`; `ladder` (the escalation policy), `ladder_run` (walking it), `ladder_brief` (what the next rung is told), `ladder_contracts` |
| `report/` HTML | `html_common`, `html_overview`, `html_board`, `html_results`, `html_artifacts`; `html_sections` re-exports them |
| `board/` | `store` (`BoardStore`), `files` (saving), `limits` |

## Decisions and rejected alternatives

The reasons behind the choices that shape the code, with what was weighed. Decisions made while
building 0.2.0 are in the [0.2.0 implementation plan's decision log](https://github.com/alexandre0sheva/crewai-engineering_team/blob/31bd355/docs/IMPLEMENTATION_PLAN_0.2.0.md#7-decision-log) (in Git history); decisions for 0.3.0 are in the [0.3.0 plan](IMPLEMENTATION_PLAN_0.3.0.md#7-decision-log).

| Decision | Instead of | Why |
|----------|------------|-----|
| The controller runs the checks and owns every card's final state; agents only report | Trusting an agent's "done" | Agent-authored text is never evidence ([SAFETY.md](SAFETY.md#untrusted-content)); the 0.1.0 guardrails only checked that files existed |
| `pipeline` (a Flow of single-agent stage crews) is the default strategy | The 0.1.0 manager-led crew (removed in 0.3.0), or one `single` agent | Measured: the 0.1.0 crew did not finish in the time limit; `single` is cheaper and not distinguishable on pass rate at this sample size ([BENCHMARKS.md](BENCHMARKS.md#results-2026-10-04)) |
| The `team` strategy's lead works itself and delegates bounded, typed jobs through controller-checked tools | A manager agent delegating through CrewAI's `allow_delegation` | The tool-less manager of 0.1.0 never finished; tools let the controller cap, scope, record and verify every job, and keep teammates one level deep |
| Parallel work packages **own paths** (a `WriteScope`) | Git worktrees per package, merged afterwards | A merge conflict needs a model to resolve it, which is the nondeterminism ownership avoids |
| Per-stage state is readable JSON beside the manifest, written atomically | CrewAI's native checkpointing; a database | A stage is one agent and one task, so a task-boundary checkpoint holds nothing; the files the tools changed are in the workspace, which the tree hash covers |
| Every process goes through `ExecutionBackend` | Each tool calling `subprocess` | One place to sandbox (the Docker backend), time out, log and cancel; a test fails any tool that imports `subprocess` |
| Web tools use one SSRF-safe client, and are off unless enabled | CrewAI's `SerperDevTool` / `ScrapeWebsiteTool` | They make their own HTTP calls, so DNS pinning, per-hop checks and the request cap could not apply ([TOOLS.md](TOOLS.md#web-and-knowledge-tools)) |
| `Search Docs` is local BM25; embeddings are opt-in `knowledge.sources` | Embedding by default | Offline, free and deterministic; embeddings send text to a provider and cost money |
| No interactive shell tool | A PTY session for agents | Unbounded, invisible to the controller and not deterministic; one-shot commands plus background processes cover the need ([TOOLS.md](TOOLS.md#runtime-tools)) |
| The web UI is FastAPI plus static files, with no build step and no CDN | A JavaScript toolchain | Nothing to build or fetch to install it; works offline |
| Tests and the benchmark's offline mode use a scripted model (`ScriptedLLM`) | Mocking at the HTTP layer, or live calls | A real CrewAI `Agent` and the real tools run deterministically and free |
| Plugins and hooks are configuration-only and off or loud by default | Loading project code automatically | A repository you cloned must not execute code because you opened it ([SAFETY.md](SAFETY.md#extensions-mcp-plugins-and-hooks)) |
| Python stays `>=3.11,<3.14` | Widening to 3.14 | CrewAI 1.15 itself declares `<3.14`; widen the range, classifiers, the CI matrix and the README badge together once a CrewAI release supports it |

## Local state

Each generated app contains `.engineering-team/` with the normalized request,
run metadata, the workspace lock, local command homes/caches, and one `runs/<run-id>/`
directory per run holding the CrewAI execution log. It should not
contain credentials. Product code, tests, docs, and its own README remain at the
normal project root.
