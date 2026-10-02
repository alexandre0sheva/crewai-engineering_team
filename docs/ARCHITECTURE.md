# Orchestrator architecture

## Why hierarchical

The crew uses CrewAI's hierarchical process with a custom
`engineering_lead`. The lead runs on the quality-first model tier, delegates
each task to a specialist based on role and current project state, and validates
the result before the next task starts. Specialists use the lower-cost worker
tier.

CrewAI 1.15 requires a custom hierarchical manager to be constructed without
ordinary tools. At task execution time CrewAI supplies the lead with scoped
delegation and coworker-question tools. Filesystem, command, and optional MCP
tools belong only to specialists, so workspace inspection and corrections are
explicit delegated work whose evidence is returned to the lead.

The process is deliberately staged:

1. Architecture and acceptance criteria
2. Project foundation and first vertical slice
3. Backend, domain, integration, or CLI implementation
4. Frontend or primary user interaction
5. Independent quality and security verification
6. Release-readiness review

Tasks share both explicit CrewAI context and a persistent filesystem. Task
summaries are useful handoffs, while the workspace remains the source of truth.

## Filesystem model

Generated applications live under `workspace/<project-name>/` (relative to the
directory the command is run from) by default. A run resumes that directory unless
`--reset` is explicitly used. A project is *owned* when it contains
`.engineering-team/owner.json`; non-empty directories without it are never written
to, and `--reset` deletes only owned projects (`--force-reset` overrides for foreign
directories but never for home, the current directory and its parents, the filesystem
root, symlinks, or the engineering-team installation). This supports normal
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
`cancel_event` every worker checks at its next safe point. `EngineeringTeam(ctx)` and the CLI entry
points are constructed from it.

Tools are built per run, not defined at import time. `tools/registry.build_tools(ctx, groups=, write_scope=,
read_only=)` returns fresh tool instances closed over `ctx`, so two runs in one process (or two
agents) never see each other's files. The code is split by responsibility: `tools/workspace.py`
(the path-safe `ProjectWorkspace`), `tools/commands.py` (command validation), `tools/scope.py`
(`WriteScope`), `tools/support.py` (`ToolEnv`, the wrapper every tool call goes through), one module
per group (`read_tools.py`, `search_tools.py`, `write_tools.py`, `command_tools.py`) on top of
logic modules (`search.py`, `navigation.py`, `symbols.py`, `patching.py`, `changes.py`,
`scripts.py`, `ignore.py`), and `tools/registry.py`, the catalogue.

The catalogue is data: each `ToolSpec` names a tool, its group (`fs_read`, `search`, `fs_write`,
`command`), whether it is read-only, needs the network, or needs the command gate, and the factory
that builds it. `build_tools(ctx, groups=, write_scope=, read_only=)` assembles a run's tools from
it. Every call passes through `ToolEnv.run`, which applies cancellation, the run's `tool_gate`
(the hook the budget guard uses to refuse calls), write-scope checks, the `ERROR:` convention, and
emits a redacted `tool.call` event to the run's event sink (a no-op sink until the run store lands).

### Execution backend

Tools never spawn processes. `tools/commands.py` validates a command and builds a `CommandSpec`;
`ctx.backend` (an `ExecutionBackend`: `run(spec) -> CommandRecord`, `start(spec) -> ProcessHandle`)
runs it. `LocalBackend` puts each command in its own process group, streams output to a capped log
under `run_dir/commands/`, keeps a head-and-tail window in memory, and kills the whole group on
timeout or cancellation. A Docker backend will implement the same protocol. Background processes
will use `start`.

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
| `request.md` | `RunRecorder` | The request exactly as given |
| `usage.json` | `RunRecorder` | `UsageReport`: token totals and a breakdown by stage, agent, and model, with per-model cost (`null` when the price is unknown) |
| `settings.json` | `RunRecorder` | The effective `Settings` (no secrets: credentials are read from the environment, never stored) |
| `crew-log.json` | CrewAI | CrewAI's own execution log |
| `commands/<n>.log` | `LocalBackend` | Full output of each project command |

Other state under `.engineering-team/` is per workspace, not per run: `owner.json` (ownership
marker), `lock` (workspace lock), `tmp/`, `tool-home/`, and `cache/`.

**Contracts** (`contracts.py`) are Pydantic v2 models that every part of the system shares:
`Spec`, `Plan`, `WorkPackage`, `CheckSpec`/`CheckResult`, `Finding`, `StageRecord`, `RunManifest`,
`Event`. Each has `schema_version` (1) and ignores unknown fields, so a file written by a newer
version still loads; bump the version only for a breaking change in meaning.

**Run lifecycle.** `RunRecorder.begin` writes the manifest as `pending`; `recorder.running()`
moves it to `running` and finishes it as `succeeded`, `failed`, `cancelled` (the run's cancel event
was set) or `interrupted` (Ctrl-C), flushing CrewAI's pending events first. The store enforces the
transitions `pending → running → {succeeded, failed, cancelled, interrupted}` (a pending run may
also be cancelled or failed); end states are final. Manifests and JSON files are replaced
atomically (temp file, `fsync`, `os.replace`), so a reader never sees a partial file, and updates
from several threads are serialised.

**Events.** Anything that happens is `ctx.events.emit(type, **data)`. The default sink appends
one scrubbed JSON line per event under a lock, so lines never interleave and `seq` is the file
order; a log can be tailed while the run is live, and a torn last line after a crash is skipped
by `read_events`. Event types today: `run.started`/`run.finished`; `tool.call` (our tools: tool,
redacted args, duration, ok); and, bridged from CrewAI, `crew.*`, `task.*`, `agent.*`,
`tool.finished`/`tool.error` (CrewAI's view of tool use, including delegation) and `llm.call`
(model as `provider/model`, call id, token usage) / `llm.failed`; `stage.started`/`stage.finished`;
`budget.warning`/`budget.exceeded`.

**The CrewAI bridge** (`runtime/bridge.py`). CrewAI has one process-wide event bus. The bridge
registers its handlers once and routes each event to the run bound in the *emitting* context
(`bind_run`, a `contextvar`; CrewAI copies the emitter's context to its handler threads), so two
runs in one process never mix. Events from an unbound context are dropped. Worker threads must
run under `contextvars.copy_context()` to stay attributed.

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

## Settings and model routing

`settings.py` is the only module that reads configuration. It builds an immutable, typed
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

## Local state

Each generated app contains `.engineering-team/` with the normalized request,
run metadata, the workspace lock, local command homes/caches, and one `runs/<run-id>/`
directory per run holding the CrewAI execution log. It should not
contain credentials. Product code, tests, docs, and its own README remain at the
normal project root.
