# Benchmarks

How the team is measured: a suite of tasks, each judged by **hidden behavioural checks** the team
never sees, run as isolated subprocesses, summarised with confidence intervals and an honest cost
per success. This file holds the **method** and the **measured results** ([below](#results-2026-10-04));
the offline mode says nothing about quality.

```bash
uv run engineering-team bench list                      # the tasks
uv run engineering-team bench run --fake                # offline: validates the harness, no model
uv run engineering-team bench run --dry-run --split dev --strategy pipeline,single --provider openai
uv run engineering-team bench spend                     # the release's live spend, per allocation
uv run engineering-team bench run --split dev --intent bugfix --budget-usd 1 --allocation T33
uv run engineering-team bench run --agent claude-code,codex --strategy pipeline ...  # baselines
uv run engineering-team bench report                    # the latest batch (Markdown, CSV, JSON)
```

The suite lives in `benchmarks/` in the source checkout. It is **not** in the wheel or the sdist;
pass `--suite DIR` when running from elsewhere.

## The suite

Twenty-two tasks in three languages and nine intents, each a request a person could write. The
first ten (0.2.0) are small Python programs; 0.3.0 added work that small greenfield programs do not
measure: TypeScript and Go, a 2,000-line project with a misleading stack trace, a multi-file
refactor, reviews scored against seeded defects, questions answered with checked citations, an
ambiguous request, and a dependency upgrade across a breaking API.

| Task | Intent | Mode | Split | Languages | Size | What the team is asked |
|------|--------|------|-------|-----------|------|------------------------|
| `notes-cli` | new_project | `new` | dev | Python | small | A notes command-line tool with tags, search, and stable ids |
| `csv-validator` | new_project | `new` | dev | Python | small | Validate a CSV against a JSON schema (types, unique, bounds, patterns) |
| `url-shortener` | new_project | `new` | dev | Python | small | A URL-shortener HTTP API with redirects, stats, and a SQLite file |
| `markdown-html` | new_project | `new` | dev | Python | small | A Markdown to HTML converter as a library and a command |
| `todo-web` | new_project | `new` | dev | Python | small | A minimal todo web app (forms, JSON API, escaping) |
| `legacy-feature` | feature | `feature` | dev | Python | small | Add a low-stock report to a legacy WSGI inventory service |
| `seeded-bug` | bugfix | `fix` | dev | Python | small | Fix a `ZeroDivisionError` from a stack trace; add a regression test |
| `add-tests` | tests | `maintain add-tests` | dev | Python | small | Write tests for an untested module |
| `express-api-feature` | feature | `feature` | dev | TypeScript | small | Add loans to an Express 5 API (endpoints, 409s, a filter, tests) |
| `node-cli-bugfix` | bugfix | `fix` | dev | TypeScript | small | A duration formatter prints `59m60s`: fix the rounding carry, add a test |
| `multi-file-refactor` | refactor | `maintain refactor` | dev | Python | medium | Move money rules copied into three modules into one, behaviour unchanged |
| `review-seeded-defects` | review | `review` | dev | Python | small | Review a branch with four seeded defects (SQL injection, missing owner check, path traversal, paging) |
| `docs-cli-reference` | docs | `maintain docs` | dev | Python | small | Document every command, option and default of a CLI; the examples must run |
| `cron-scheduler` | new_project | `new` | **held out** | Python | small | Compute a cron expression's next run times |
| `ambiguous-export` | new_project | `new` | **held out** | Python | small | "Sorted by name": scored on recording which name, and building that |
| `behaviour-refactor` | refactor | `maintain refactor` | **held out** | Python | small | Split a 70-line function without changing its behaviour |
| `react-component-bug` | bugfix | `fix` | **held out** | TypeScript | small | A React component sorts its props in place (a crash on frozen state); Vitest |
| `go-cli-feature` | feature | `feature` | **held out** | Go | small | Add `-min-length` and `-format json` to a Go CLI, with table tests |
| `ledger-misleading-trace` | bugfix | `fix` | **held out** | Python | **large** | A crash traced to currency conversion whose cause is an importer that silently returns `None` (2,013 lines, six packages) |
| `review-auth-change` | review | `review` | **held out** | Python | small | Review a login-session change with four seeded defects |
| `answer-questions` | question | `maintain docs` | **held out** | Python | small | Answer four questions about a repository in `ANSWERS.md`, with citations |
| `dependency-upgrade` | upgrade_deps | `maintain custom` | **held out** | Python | medium | Upgrade a vendored library to 2.0.0 across its breaking changes |

`bench list` shows the same (`--json` adds criteria, size, difficulty and toolchains).

**Splits are fixed.** 9 of the 22 tasks (41 %) are held out. The 0.2.0 tasks keep the split they
had, since the dev ones were already used to choose 0.2.0's defaults. The new held-out tasks cover
every new kind of work except docs (a review, a bugfix in a large Python project and in a React
component, a Go feature, the question, the ambiguous request and the upgrade), so a harness tuned on
the dev split meets each of them for the first time. `--split dev|heldout|all` (also `--subset`,
the 0.2.0 name), `--intent`, `--language`, `--size` and `--difficulty` (each repeatable or
comma-separated) select tasks; `--tasks` names them.

**Answers, reviews and ambiguity are scored by checks too.** A review task commits its fixture on
`main` and its `change/` on a branch, runs `review --base main --out-dir <workspace>/review-out`, and
scores `findings.json` against the hidden `acceptance/defects.json` (each defect's file and line
range, other places a reviewer may fairly point at, and words for findings without a line): recall
(at least 3 of 4 found), the severity given to the security defects found, and precision (at least
half of the medium-or-worse findings point at a seeded defect; repeats and low or info findings do
not count against it). The question task asks for `ANSWERS.md` and checks each answer for its key
facts and for a `path:line` citation inside the code that answers it (`acceptance/answers.json`),
and that every citation exists. The ambiguous task checks that a document (the README,
`docs/spec.md`, a docstring) states one reading of "sorted by name" more often than the other, and
that the tool sorts that way. Until the `do` command (task 14) and the answer path (task 15) exist,
the question task runs as `maintain docs` and the upgrade as `maintain custom` (the `upgrade-deps`
recipe may only change manifests).

### A task on disk

```text
benchmarks/tasks/<id>/
  task.yaml        id, title, kind, mode, intent, split, size, difficulty, languages, toolchains,
                   timeout_seconds, scale, criteria, sabotage
  request.md       the only text the team sees (it is the specification: names, exit codes, formats)
  acceptance/      checks.py and its data: the hidden checks (never copied into a workspace)
  fixture/         brownfield only: the project the team starts from
  change/          review only: committed over the fixture on the branch `change`
  reference/       a known-good solution, written over the fixture (or an empty workspace)
  trace.txt        fix mode: the stack trace handed to `fix --trace-file`
```

`intent` is one of the triage intents (`question`, `review`, `bugfix`, `feature`, `new_project`,
`refactor`, `tests`, `docs`, `upgrade_deps`, ...); `size` is `trivial|small|medium|large`,
`difficulty` `easy|medium|hard`, `languages` a list, `toolchains` what the checks need besides
Python (`node`, `go`). A 0.2.0 `task.yaml` still loads: `subset` is read as `split`, `language` as
`languages`, and a missing `intent` follows from the mode.

Rules a task follows (the tests in `tests/test_bench_tasks.py` enforce most of them):

- **The request is the contract.** A check may only test behaviour the request states, so a correct
  but different design passes. Interfaces that need exact names (`python -m notes`, a port flag, an
  output format) are in the request.
- **One criterion, one behaviour.** `task.yaml` lists `criteria` (`id`, `text`, `required`);
  `checks.py` defines `check_<id>(workspace)` for each, raising `AssertionError` (via
  `engineering_team.bench.checklib.expect`) when it does not hold. A run **passes** only when every
  required criterion holds. The team's own verdict (`verified`, `succeeded`) is recorded but is never
  the evidence.
- **Brownfield checks include "did not break anything"**: the existing endpoints, the existing
  tests, unchanged source files where the task says so, and for fixes and added tests a check that
  the team's tests *fail on the unfixed code* or *catch seeded defects* (a small mutation test).
- **Every task proves its checks discriminate.** `task.yaml` has a `sabotage`: edits that break the
  reference on purpose and the criteria that must then fail. `bench run --fake --fake-solution
  broken` applies them.

- **Fixtures are small** (at most ~330 lines of source) unless the task's `size` is `large`, which
  means at least 2,000 lines in four or more packages.

To add a task, copy a similar one, keep to the rules above, and run the three offline halves of
`bench run --fake --tasks <id>`: the reference passes, `--fake-solution broken` fails the named
criteria, and `--fake-solution fixture` (nothing done) fails.

## How a run works

1. **Plan.** `--tasks` (ids, or `all`), the filters above, `--strategy` (`pipeline`,
   `single`; repeat or comma-separate) and `--repeat N` give the list of runs, in the
   order task, strategy, repeat. The repository modes (`feature`, `fix`, `maintain`) use the
   pipeline only, so a brownfield task is not planned for the other strategies; the report lists the
   pairs left out.
2. **Prepare.** Each run gets its own directory `<out>/<batch>/<task>/<strategy>-<n>/` (default
   `<out>` is `benchmarks/.runs/`, git-ignored). A new project builds in `ws/app`; a brownfield task
   starts from `repo/`, a fresh Git repository on `main` with the fixture as its only commit. The
   request is copied to `request.md`; the task directory and its checks are not copied or named
   anywhere in the run.
3. **Run.** One subprocess per run, `python -m engineering_team --json ... new|feature|fix|maintain`,
   in its own process group, with `CREWAI_STORAGE_DIR` inside the run directory (no shared CrewAI
   memory between runs), a wall-clock limit from `task.yaml` (the team is asked to stop at 90 % of
   it; the harness kills the group at 100 %), and `ENGINEERING_BUDGET_MAX_COST_USD` as the per-run
   spend cap. `--provider`, `--profile`, `--sandbox docker|local` and `--config FILE` pass through.
   **A run's conditions are those flags and that file, nothing ambient:** every `ENGINEERING_*`
   variable is blanked in the team's environment (so neither your shell nor the repository's `.env`
   can change a model or a budget: both once did, see the decision log) and your user config file is
   not read. To benchmark anything beyond provider and profile (tier models, `max_repair_rounds`,
   parallelism), put it in a `--config` file; `batch.json` records the file's hash.
4. **Judge.** Each criterion runs in its own process on a *copy* of the workspace, from a scratch
   directory, with a scrubbed environment (no API keys, a throwaway `HOME`) and a timeout. Exit
   status 0 is a pass and 1 a fail; a check that crashes is a failed criterion, never a pass (a crash
   can come from the team's output). A check that **cannot judge** (its toolchain is missing, Docker
   is not running, the check function does not exist) raises `CheckError` and the criterion is an
   **`error`**: a run with a failed required criterion is `failed` whatever else happened, otherwise
   a run with an errored one is `error`, which is left out of pass rates and never kept by
   `--resume`.

   **Node and Go tasks** (`toolchains` in `task.yaml`) run their tools through
   `checklib.toolchain`: by default in a Docker image with **no network** (build them once:
   `docker build -t engineering-team-bench-node:1 benchmarks/images/node` and
   `docker build -t engineering-team-bench-go:1 benchmarks/images/go`; the Node image holds the
   pinned npm packages of `benchmarks/images/node/package.json`). `--acceptance-sandbox local` uses
   this machine's `node` and `go` instead, with the npm packages from `--node-modules DIR` (or
   `BENCH_NODE_MODULES`), for example a directory where that `package.json` was installed.
5. **Record.** `result.json` per run (see below), the team's `stdout.log` and `stderr.log`, and the
   workspace stay on disk. `batch.json` records the options, the versions (`engineering_team`,
   `crewai`, Python, platform), the command line, and a content digest per task, so a result says
   exactly which task it measured.

`--parallel P` runs P at a time. What is *recorded* is deterministic (stable order in the report)
even though execution order is not.

### Budget

A live run needs `--budget-usd X`, a cap for the **whole batch**. Each run reserves its own cap
(`--run-budget-usd`, default X divided by the number of runs) before it starts and settles with what
it actually cost, so parallel runs can never spend more than X between them. A run that cannot
reserve its cap is recorded as `skipped`, not started. A cost that is unknown (a model with no
price) is charged as the whole reserved cap. The per-run cap is enforced by the team's own budget
guard, so the guard decides when a single run stops.

The guard can only stop a run whose spend it can compute, so a live batch **refuses to start when a
model the team could use has no price** (add a `[pricing."MODEL"]` override to the `--config` file,
see [CONFIGURATION.md](CONFIGURATION.md#budgets-usage-and-cost), or pass `--allow-unpriced` and accept that only the
wall-clock limit bounds a run).

### Live spend cap

All live model spending of a release goes through `benchmarks/budget.toml` (the release total and
one allocation per plan task, such as `T25 = 2.80`, plus a `reserve`) and the append-only ledger
`benchmarks/spend.jsonl` (one line per live command: date, allocation, command, estimate, actual
cost, `billing`). A live `bench run` needs `--allocation TASK`. Before it starts it prints the
estimate and **refuses, with the numbers**, when the estimate would take the allocation or the
release total over, or when `--budget-usd` is more than what is left in the allocation (so a batch
that over-runs its estimate still cannot overspend). When the batch ends (also on Ctrl-C) it appends
what the runs cost; a cost that is unknown (an unpriced model) is recorded as `null` and counted as
the estimate. Runs billed by a subscription are recorded with `billing: subscription` and never
counted. `bench spend` (or `--json`) prints allocated, spent and left per allocation. Fake runs and
`--dry-run` never touch the ledger. Moving unspent money to a later allocation is an edit to
`budget.toml`; going over one needs the user's explicit OK, never a flag.

`--dry-run` prices the batch from the price table without running anything. The token counts behind
it are **priors** (typical tokens per strategy and task `scale`), not measurements: read the figure
as an order of magnitude and set `--budget-usd` as the real limit. `--resume --batch NAME` continues
an interrupted batch, keeping runs that finished (passed, failed, or timed out) and repeating the
rest; a Ctrl-C stops every team process and tells you the command.

## What a run records

`result.json` (schema version 3; versions 1 and 2 still read, `subset` included):

| Field | Meaning |
|-------|---------|
| `task`, `intent`, `split` | the task, what kind of work it is, and whether it is held out |
| `strategy`, `agent`, `agent_version` | who did the run: a team strategy, or `strategy` = `agent` = the external agent's name with the version its `--version` printed |
| `billing`, `equivalent_cost_usd` | `api`, `subscription`, `unknown` or `none`; for a plan, the API-equivalent cost the agent reported (its `cost_usd` is `null`) |
| `outcome` | `passed`, `failed`, `timeout`, `error` (the harness could not run it, or a check could not judge it: not counted), or `skipped` (budget: not counted) |
| `reason`, `criteria` | why it did not pass; every criterion with its pass and `error` flags and the check's output |
| `team_status`, `team_verdict`, `exit_code` | what the team said about itself (never the evidence) |
| `duration_seconds` | wall clock of the team process |
| `tokens`, `model_calls`, `tool_calls`, `cost_usd`, `models` | from the team's summary, else rebuilt from its event log (so a crashed run still shows what it spent). `cost_usd` is `null` when a model has no price: unknown, never $0 |
| `repair_rounds` | `verify.repair` events: how often verification sent the team back to fix something |
| `tool_failures` | `tool.call` events with `ok: false` |
| `setup_failures` | setup checks (dependency install and the like) that failed or were unavailable |

### Performance baseline (T5)

Where a run's time and tokens go is read with `engineering-team profile RUN`; `bench report` adds a
"Where the time and money went, by strategy" table (sessions, model calls and prompt tokens per run,
cached share). This is the "before" for the lean team (T6) and the lead agent (T12).

**Live baseline, 2026-10-28** ([`benchmarks/results/2026-10-28/perf-baseline/`](../benchmarks/results/2026-10-28/perf-baseline/):
`report.md`, `results.csv`, `batch.json`, one `result.json` and one `profile-*.txt`/`.json` per strategy).
`notes-cli`, one run each, `--provider openai --profile smoke` (the cheap worker model `gpt-6-luna` for every
teammate; the minimal team, without the review, DevOps and docs stages). Both runs passed all required
criteria. Estimated cost (price table), $0.035 for the batch. The `standard` profile was not run: its
`pipeline` run is estimated at $1.01, above the $0.50 allowance for this measurement.

| | `single` | `pipeline` | `pipeline` / `single` |
|---|---:|---:|---:|
| Time | 78 s | 6.4 min (382 s) | 4.9× |
| Cost | $0.0047 | $0.0300 | 6.4× |
| Agent sessions | 1 | 7 | |
| Model calls | 15 | 84 | 5.6× |
| Prompt tokens (cached share) | 142,177 (87 %) | 1,110,885 (89 %) | 7.8× |
| Output tokens | 3,114 | 15,356 | 4.9× |
| Failed tool calls | 0 | 11 | |

What the profile shows for the `pipeline` run:

- **Structure, not work.** Seven sessions ran one after another (parallelism 0.98×; the two `implement`
  packages depended on each other). `foundation`, `integrate` and `release` (setup, wiring and release
  paperwork) took 63 % of the cost and 59 % of the session time; the two sessions that wrote the application
  were 25 % of the cost.
- **Tool schemas dominate the prompt.** Every call re-sends the agent's tools: about 12,100 tokens for the
  engineers and the release engineer (the target is 6,000), 7,500 for the architect, 6,300 for the analyst.
  Shared out over the billed prompt, tool schemas are about 61 % of the `pipeline` prompt tokens and 79 % of
  `single`'s; tool results are 21 % (the release session alone has 82,000 tokens of them), the task
  prompt and role text 12 %, conversation history 6 %.
- **Provider caching already works** (87–89 % of prompt tokens cached) because the tool schemas lead the
  prompt; the cost of the extra sessions is output and the uncached remainder, and the time is model calls
  (84 against 15), which caching does not shorten.
- **Failed calls**: 11 against 0 — a malformed patch (2), moving a card to the state it was in (2), commands
  that need a working directory or do not exist for the project (4), a path outside the workspace, a command
  outside the allowlist, a note written to the decision log. `docs/spec.md` was read by two sessions.

**After the lean team (T6), same day, same task and profile**
([`benchmarks/results/2026-10-28/perf-lean/`](../benchmarks/results/2026-10-28/perf-lean/), `pipeline` only, one
run): it passed every required criterion. The run was sized small, so one `design` session wrote the spec and
the plan and one engineer built the collapsed plan; the `smoke` profile's minimal team skips review either way.

| `pipeline` | before (T5) | after (T6) | after / before | `single` (T5) |
|---|---:|---:|---:|---:|
| Time | 382 s | 80 s | 0.21× | 78 s |
| Cost | $0.0300 | $0.0043 | 0.14× | $0.0047 |
| Agent sessions | 7 | 2 | | 1 |
| Model calls | 84 | 15 | 0.18× | 15 |
| Prompt tokens (cached share) | 1,110,885 (89 %) | 77,575 (83 %) | 0.07× | 142,177 (87 %) |
| Tool-schema tokens per call (engineer) | 12,102 | 3,243 | 0.27× | 12,102 |
| Failed tool calls | 11 | 3 | | 0 |

Per session: `design` 28 s, 6 calls, $0.0013 (before: `spec` and `plan`, 52 s, 11 calls, $0.0036);
`implement` 46 s, 9 calls, $0.0030 (before: `foundation`, two packages, `integrate` and `release`, 5 min 21 s,
73 calls, $0.0264). On this small task the pipeline now costs and takes what `single` does, while keeping the
controller's verification. The three failed calls were all `Use Tool` calls whose arguments the model put
beside the tool's name; `Use Tool` accepts that form since. One run on one small task, with the minimal team,
is a sanity check of the structure, not evidence about pass rates; T33 measures those.

The 0.2.0 standard-profile figures (9× time, 25–39× cost against `single`) mix in the stronger model the
`pipeline` uses for lead and reviewer work; on the cheap model alone the structure costs 5–6×. Comparing
the two profiles requires the `standard` batch, which needs the user's OK to exceed the allowance.

The offline proxies in `tests/perf/` pin the structure on scripted runs (measured 2026-10-28; before
and after the lean team, T6):

| Scenario | Sessions | Model calls | Tool-schema tokens per call | Ceremony stages |
|----------|---------:|------------:|----------------------------:|----------------:|
| `pipeline`, small new project (2 packages) | 11 → **3** | 19 → **6** | 12,102 → **3,234** | 3 → **0** |
| `pipeline`, medium (4 chained packages) | 13 → **9** | 23 → **15** | 12,102 → **3,234** | 3 → **0** |
| `pipeline`, every stage (`lean.size = large`, `ceremony = always`) | 11 → **10** | 19 → **17** | 12,102 → **3,234** | 3 → **2** (the release report is the controller's) |
| `fix`, small bugfix (seeded bug, repro, fix, review) | 9 → **3** | 12 → **6** | 12,102 → **3,234** | 0 |
| `single`, small new project | 1 | 4 | 12,102 → **3,234** | 0 |

The targets for a small task (3 sessions, 25 calls, 6,000 schema tokens per agent, no ceremony;
`docs/IMPLEMENTATION_PLAN_0.3.0.md`) are tests in the gate now. The medium target ("sessions only for
teammates the lead called") is T12's. Whether the leaner structure keeps the pass rate is a live
question: the scripted proxies measure structure, not quality (T33 measures both).

## External agent baselines (`--agent`)

"Better than Claude Code or Codex" is a claim about a harness, so those tools run on the **same
tasks, the same time limit and the same hidden checks** as the team:

```bash
# Claude Code on a plan (no API key in the environment), next to the team's pipeline
uv run engineering-team bench run --split dev --strategy pipeline --agent claude-code \
    --budget-usd 3 --allocation T33 --agent-model claude-code=claude-sonnet-5-5 --provider anthropic
uv run engineering-team bench run --agent codex,opencode --repeat 3 --budget-usd 2 --allocation T33
uv run engineering-team bench run --fake --agent codex --strategy pipeline   # mixed, offline team
```

`--agent` takes `claude-code`, `codex`, `opencode` and `aider` (repeat or comma-separate). Alone, it
replaces the default `pipeline`; with `--strategy` the report compares both. A missing command stops
the batch (exit 2) instead of dropping the contender. `--fake` only makes the *team* scripted: an
external agent always runs for real, so a fake agent in a test is a script on `PATH`.

**What is the same for every contender.** A fresh copy of the fixture per run (an empty project
directory, or a clean Git repository on `main` with one commit); the same request text (a fix task's
stack trace and reproduction command are appended to the text for an agent, because the team gets
them as files); the task's wall-clock limit (the team is asked to stop at 90 % of it, an agent is
killed at 100 %); and the hidden checks, which are never in the run directory. Only the checks
decide the verdict: what an agent says about itself is a note in the record.

**What differs, on purpose.** Each agent runs with *its own default settings* (your user-level
Claude Code or Codex configuration is read, as it would be for you), its own tools, and no
presets: the team's `maintain` recipes and its `review` command add instructions the others never
see, so review tasks are not run by external agents (the report lists them as not applicable).
To separate the harness from the model, **pin the same model**: `--agent-model AGENT=MODEL`
(`claude-code=claude-sonnet-5-5` beside `--provider anthropic`, `codex=<model>`,
`opencode=provider/model`, `aider=<model>`). The report header and `result.json` record the model
each agent actually used when it says so, and the pinned one otherwise; an unpinned agent is a
comparison of products, not of harnesses, and the report says "its own default".

| Agent | Command (non-interactive) | Cost |
|-------|---------------------------|------|
| `claude-code` | `claude -p PROMPT --output-format json --permission-mode bypassPermissions --no-session-persistence [--model M] [--max-budget-usd CAP]` | `total_cost_usd` of its result; the only agent a spend cap can stop |
| `codex` | `codex exec --json --color never --skip-git-repo-check --ephemeral --sandbox danger-full-access -C DIR [-m M] -- PROMPT` | reported tokens times the price table (`unknown` when the model has no price) |
| `opencode` | `opencode run --format json --auto [-m provider/model] PROMPT` | the `cost` of each `step-finish` event |
| `aider` | `aider --message PROMPT --yes-always --no-pretty --no-stream --no-auto-commits --no-check-update --no-analytics [--model M]` | the `Cost: $x session` line it prints |

Everything goes through the same `ExecutionBackend` the team's tools use, in its own process group,
with the batch's cancellation. The permission flags are what an unattended run needs; they are not a
sandbox, so run baselines where you would run the agent unattended (the team's own `local` backend
is no stricter). Flags were checked against each tool's `--help` on 2026-10-29 (Claude Code 2.1.287,
Codex 0.162.1, OpenCode 1.19.4); Aider was not installed there, so its line follows its
documentation and is unchecked.

**Cost and billing.** A run's `billing` is `api` (per token), `subscription` (a plan), `unknown`
(OpenCode can be either), or `none` (the offline team). It is detected from the environment (an
`ANTHROPIC_API_KEY`, a cloud-provider switch, `OPENAI_API_KEY` or `CODEX_API_KEY` means `api`;
without one Claude Code and Codex are taken to be signed in with a plan) and overridden with
`--agent-billing AGENT=api|subscription`. A plan's runs have **no `cost_usd`** and the report shows
`plan (≈$x)` with the API-equivalent figure the agent reported, never `$0`; they are written to
`spend.jsonl` as `billing: subscription` with their token counts and are not charged. API and
`unknown` runs are charged like the team's: a batch needs `--budget-usd`, `--allocation`, and an
affordable ledger, and an agent's estimate is its per-run cap (`--run-budget-usd`, else the budget
over the paid runs) because nothing prices an agent before it runs. Only Claude Code takes the cap
on its command line; for the others the time limit is the only brake, so give them a small budget.

**What is not comparable.** Token counts: Aider rounds what it prints and the others count
differently; read them as orders of magnitude. Sessions and model calls describe the team's
internals, so the report shows them only for the team. Time includes each agent's start-up.
A pass rate from few runs is wide; the paired table counts per-task wins, not significance.

## Reading a report

`bench report [BATCH]` (a directory, or a name under `--out`; default the latest) prints Markdown
and writes `report.md`, `results.csv` (one row per run) and `results.json` into the batch.
`results.json` is one document for the team and the agents: `contenders` (pass rate with its Wilson
interval, cost, billing, models per strategy or agent), `paired` (per-task wins, losses and ties
between every two) and `runs` (every `result.json`); the site's leaderboard reads it.

- **Pass rate** is passed over *counted* runs (`passed`, `failed`, `timeout`) with a **Wilson 95 %
  interval**. The interval is the point: 3 of 3 is "somewhere between 44 % and 100 %", and two
  strategies whose intervals overlap are not distinguished by that batch. Reports with fewer than
  20 counted runs carry a small-sample warning.
- **Cost per successful task** is *all* spend, failed runs included, divided by the number of
  successes. A strategy that is cheap per run but rarely succeeds is not cheap.
- Tables by strategy or agent, by kind and split (dev against held out), and by task, with median
  time, repairs per run, tool failures and setup failures. With two or more contenders, a **paired
  comparison**: per pair, the tasks where one has the higher pass rate, and a task-by-contender grid
  of passed over counted runs.
- **Every failure is listed** with the criteria it missed and why. Nothing is averaged away.

## Results (2026-10-04)

One evaluation, on one developer laptop (macOS, Apple silicon), `engineering_team` 0.2.0, `crewai`
1.15.23, Python 3.12.12, `--sandbox local`, OpenAI models. The files behind every number are
committed in [`benchmarks/results/2026-10-04/`](../benchmarks/results/2026-10-04/): per batch a
`report.md`, `results.csv`, `batch.json` (options, versions, command, task digests) and one
`result.json` per run. **Costs are estimates from the price table (`pricing.toml`, checked
2026-10-02), not invoices;** the whole evaluation was estimated at $6.13 over 53 run records.

Which models ran: `single` used only `openai/gpt-6-luna` (the cheap worker tier); `pipeline` and
`hierarchical` used `openai/gpt-6.1-sol` for lead and reviewer work and `gpt-6-luna` for the rest
(tiers in [CONFIGURATION.md](CONFIGURATION.md#provider-presets)). So **`single` against `pipeline`
compares a cheap model working alone with a team that also has the stronger model, not only the
structure.** Anthropic and Google presets were not measured (no key was available for them).

### Strategies on the dev tasks

Five greenfield dev tasks, two repeats each (`notes-cli`, `csv-validator`, `url-shortener`,
`todo-web`, `markdown-html`). `markdown-html` counts at its amended request (see
[Failures](#every-failure-and-its-cause)); its first version was defective.

| Strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Batches |
|----------|--------|-------------------------|-----------------|----------------|-------------|---------|
| `single` | 9/10 | 60 %–98 % | $0.0048 | $0.0054 | 1.2 min | `screen-default`, `markdown-html-v2` |
| `pipeline`, architect on the `worker` tier (the preset before this task) | 7/8 (four tasks) | 53 %–98 % | $0.119 | $0.136 | 10.6 min | `screen-default` |
| `pipeline`, architect on the `reviewer` tier (the new default) | 10/10 | 72 %–100 % | $0.209 | $0.209 | 10.4 min | `screen-architect-sol`, `screen-architect-sol-url`, `markdown-html-v2` |
| `hierarchical` (the 0.1.0 crew) | 0/3 | 0 %–56 % | $0.69 | – (no success) | 30.2 min | `pilot-two-strategies`, `screen-hierarchical` |

What the table supports, and what it does not:

- **`hierarchical` is the only strategy the data separates from the others:** all three runs hit the
  30-minute limit (224 to 306 model calls and 316 to 390 tool calls, $0.58 to $0.88 each) without a finished project. That
  is a timeout, not a wrong answer: with no limit it might finish, at a higher cost.
- **`single` and `pipeline` are not distinguished on pass rate:** their intervals overlap almost
  entirely. On this suite the team structure bought no measurable correctness.
- **`pipeline` costs 25 to 39 times more per success and takes about nine times longer.** That is the
  measured price of staging, review, and verification on tasks this small.
- With the first request version of `markdown-html` the same batches read 7/10 (`single`), 7/10
  (`pipeline`, worker architect) and 8/10 (`pipeline`, reviewer architect): same ordering, wider
  spread, and the `markdown-html` failures are the task's, not the strategies'.

### Held-out and repository tasks

Too small to compare anything; they are here so the held-out tasks are not hidden. The architect
setting was chosen before these ran, but on the dev tasks only.

| Task | Strategy | Passed | Cost / run | Batch |
|------|----------|--------|------------|-------|
| `cron-scheduler` (held out) | `single` | 2/2 | $0.0047 | `heldout-cron` |
| `cron-scheduler` (held out) | `pipeline` | 2/2 | $0.096 | `heldout-cron` |
| `behaviour-refactor` (held out) | `pipeline` | 1/1 | $0.024 | `brownfield-pipeline` |
| `legacy-feature`, `seeded-bug`, `add-tests` (dev) | `pipeline` | 3/3 | $0.056 | `brownfield-pipeline` |

### Local models (smoke tests only)

One `notes-cli` run each with the `single` strategy, on a laptop that was busy with other
applications. These show that the plumbing works, not what the models can do.

| Model | Served by | Result | What happened |
|-------|-----------|--------|---------------|
| `llama3.2` (3B) | Ollama | failed, 41 s | One answer in plain text, no tool call, no files written |
| `gemma3:270m` | Ollama | failed, 27 s | Same |
| `llama-3.2-3b-instruct` | LM Studio (16K context) | timeout, 30 min | Two tool calls, both failed, then no progress (a full test suite was running at the same time, so this is inconclusive) |
| `deepseek-r1-distill-llama-8b-mlx` | LM Studio | not run end to end | A one-line prompt worked (270 of 275 tokens were reasoning); the model has no tool calling, so it cannot drive the team |

The configs are in [`benchmarks/configs/`](../benchmarks/configs/). LM Studio has no CrewAI provider of
its own; `hosted_vllm/<model>` with `VLLM_BASE_URL=http://localhost:1234/v1` reaches its
OpenAI-compatible server. The Ollama and LM Studio runs reported an unknown cost although a zero price override was
configured. Cause (found and fixed in 0.3.0): a model call made without an agent reached the run's log under its bare
name (`llama3.2`), which no `ollama/*` price matches; the log now takes the provider from the model that made the
call, so a zero price gives `$0.00`.

### Every failure and its cause

| Batch / run | Outcome | Cause |
|-------------|---------|-------|
| first pilot, `single` on `notes-cli` (not kept; $0) | failed in 6 s | **A bug, fixed in this task:** the OpenAI client rejected the `context_window_size` option the preset passed for GPT-6 models, so no model call ever worked. Regression test in `tests/test_crew.py` |
| `pilot-two-strategies`, `pipeline` `notes-cli` | failed, 80 s | The architect (worker tier) gave a work package `README.md`, which belongs to foundation and integrate; the plan was rejected twice |
| `screen-default`, `pipeline` `markdown-html` 1 and `url-shortener` 2 | failed, about 90 s each | Same plan rejection |
| `pilot-two-strategies` `hierarchical` `notes-cli`; `screen-hierarchical` `todo-web`, `csv-validator` | timeout, 30.2 min | Ran until the limit without finishing: 224 to 306 model calls and 39 to 87 failed tool calls per run |
| `screen-default`, `single` `csv-validator` 2 | failed | A real defect in the generated program: the check that a value equal to both bounds must pass failed (criterion `constraints`; the program reported "expected 1 fields, got 7") |
| `screen-default` (`single` 1 and 2, `pipeline` 2) and `screen-architect-sol` (1 and 2), `markdown-html` | failed | **A defect in the task, not the team.** All five failed the check that `snake_case_name` stays plain and four also failed the code-block check (no newline after the last line, quotes not escaped). The request stated none of it. It now states all three; the rerun in `markdown-html-v2` passed 4/4 |
| `screen-architect-sol`, `markdown-html` 1 and 2 | verification "partial" | The generated project's `.venv` had no `python` binary, so the controller's own test check could not run; cause not found |
| `screen-architect-sol`, `url-shortener` 1 and 2 | skipped | The batch budget could not reserve their per-run caps; rerun in `screen-architect-sol-url` (2/2). Likewise one `heldout-cron` run, resumed |
| local models | see above | |

### Decisions

| Setting | Before | Now | Evidence |
|---------|--------|-----|----------|
| `strategy` (for `new`) | `hierarchical` | **`pipeline`** | A product decision, not a cost result: the project's default must be an orchestrated strategy, and `pipeline` is the only one that finished the work (10/10, against 0/3 for `hierarchical`). It did **not** win on quality per cost: `single` passed 9/10 (overlapping intervals) at $0.0054 a success against $0.21, and stays available as `--strategy single` for a quick, cheap run. The repository modes (`feature`, `fix`, `maintain`) always use `pipeline` |
| `solution_architect` tier | `worker` | **`reviewer`** | Plan rejected in 3 of 11 pipeline runs with the worker tier, 0 of 15 with the reviewer tier (one-sided Fisher exact p = 0.06: suggestive, not conclusive). Costs about $0.09 more per run |
| `budget.max_repair_rounds` | 3 | 3 (kept) | No run used more than one repair round, so the cap was never the limit; nothing to tune |
| `parallel.max_parallel_agents` | 3 | 3 (kept) | **Not measured.** There was no budget to compare it |
| Tier models | | unchanged | Nothing was measured against other models |
| Default sandbox | `local` | `local` (kept) | **Not measured:** every run was local. Docker stays recommended in [SAFETY.md](SAFETY.md) |

The architect change was picked after seeing the default's plan failures, on the same dev tasks it
was then scored on, so its 10/10 is not independent evidence; the held-out tasks (three runs) did not
contradict it.

### Limits of this evaluation

- Ten dev runs per strategy and three held-out tasks: the intervals above are wide, and one more
  failure moves a rate by ten points.
- `single` and `pipeline` differ in models as well as structure (see the top of this section); a
  `single` run on `gpt-6.1-sol` was not measured.
- Tasks are small Python programs judged on behaviour only. Documentation, code quality, review
  findings, and the verification report, which the `pipeline` produces and `single` mostly does not,
  are not scored, so this says nothing for or against them.
- Models are non-deterministic; one machine, one day, one provider.

### Reproduce

```bash
uv run engineering-team bench run --tasks notes-cli,csv-validator,url-shortener,markdown-html,todo-web \
  --strategy single,pipeline --repeat 2 --provider openai \
  --config benchmarks/configs/architect-reviewer.toml --budget-usd 3 --run-budget-usd 0.6 --parallel 3
uv run engineering-team bench report benchmarks/results/2026-10-04/screen-default   # an existing batch
```

## Offline mode (`--fake`)

`bench run --fake` replaces the team with a scripted one: a real CrewAI `Agent` on a `ScriptedLLM`
that writes the task's reference solution with the real `Write Project File` tool bound to a real
`RunContext` (path rules, events and usage included). The run then goes through the same
preparation, the same hidden checks, the same records and the same report. It costs nothing, needs
no key, and is how CI proves the harness works:

- the **reference** solutions pass every check (`bench run --fake`),
- the **sabotaged** ones fail the criteria they break (`--fake-solution broken`, exit status 3), and
- the **untouched fixture** fails (`--fake-solution fixture`: the fake team writes nothing),

so a check that cannot tell a good solution from a bad one, or from no work at all, fails CI.
`tests/test_bench_fake.py` runs the three halves end to end on every task whose toolchain runs on
the machine (`--acceptance-sandbox local`; the Express and React tasks also need
`BENCH_NODE_MODULES`), and a `docker`-marked test runs the Node and Go tasks in their images. A fake report says so at the top: it validates the
harness and says nothing about the team's quality.

## Threat model

Benchmarks are only worth reading if the team cannot game them and the harness cannot hurt you.

**Hidden checks.**

- The checks live in the task directory and are run by the harness *outside* the workspace, on a copy.
  In a live run no run directory, command line, request, or environment variable names the task
  directory (the offline team is handed it, since it replays the reference). A fix
  task's trace is copied into the run directory first.
- The local execution backend is not an OS sandbox (see [SAFETY.md](SAFETY.md)): an agent that
  searches the whole disk could in principle find `benchmarks/tasks/`. For results you publish, run
  with `--sandbox docker` (only the workspace is mounted) or on a machine where the suite is not
  checked out beside the runs.
- A request states every behaviour that is checked, and the checks test behaviour, not structure,
  so there is nothing to gain from guessing implementation details. The two structural checks
  (function sizes in `behaviour-refactor`, unchanged source in `add-tests`) are stated in their
  requests.

**Held-out split.** Tasks with `split: heldout` are never used to tune defaults or the harness
(strategy, tier models, `max_repair_rounds`, parallelism, harness components). Tune on
`--split dev`; report held-out results separately; the report's kind and split table shows the
difference. A large gap between the two is over-fitting to the dev tasks. Nine held-out tasks are
still few: intervals are wide, which is why the report lists every run rather than averaging.

**Running generated code.** The team writes programs and the hidden checks *run them* on your
machine (acceptance checks run locally, with a scrubbed environment and timeouts, but with your
user's file permissions; the Node and Go tools they start run in a container with no network unless
you pass `--acceptance-sandbox local`). Treat a live batch like running any untrusted code: use a container or
VM, a disposable checkout, and a provider key with a spending limit. Fake runs execute only the
repository's own reference solutions.

**Secrets.** The harness never reads `.env` itself. The team's own process loads it as it always
does, which is how it finds an API key (its `ENGINEERING_*` settings are ignored, see above); fake
runs and every acceptance check get a scrubbed environment with no keys.

## Limits

- Most tasks are small; one fixture has 2,000 lines and none is a real-world repository (task 9
  imports those). TypeScript and Go have a few tasks each; UI polish and long-running work are not
  covered. Scoring a review, an answer or a recorded assumption by checks is keyword- and
  location-based: generous enough for other wording, but a check, not a judge.
- A passing run means the stated behaviours hold, not that the code is good: review quality, tests
  the team wrote, and documentation are only checked where a task says so.
- Models are non-deterministic: use `--repeat` and read the interval, not the point estimate.
- Costs come from the price table (`pricing.toml`); a provider change after the run date makes old
  costs stale. Reports show model names and versions so that can be judged.

## Files

| Path | Role |
|------|------|
| `src/engineering_team/bench/` | the harness (in the wheel): `tasks` (schema, loading), `selection` (filters), `plan` and `prepare` (what runs and how), `execution` (one run, and `judge`, the hidden-check verdict both kinds share), `agents/` and `agent_run` (external agents, [above](#external-agent-baselines---agent)), `compare` and `export` (paired comparison, `results.json`), `batch` (parallel, budget, resume), `acceptance`, `checklib` and `reviewlib` (the hidden checks' runner and helpers), `metrics` and `results` (what a run records), `report` and `stats`, `estimate` (`--dry-run`), `spend` (the release ledger), `fake_team` (offline mode) |
| `src/engineering_team/cli/bench_*.py` | `bench list` and `bench run` (`bench_command`), `bench report` and `--dry-run` (`bench_report_command`), `bench spend` (`bench_spend_command`), `--agent` options (`bench_agents`) |
| `benchmarks/` | the suite (repository only): `tasks/`, `images/` (Docker images for the Node and Go checks), `budget.toml` and `spend.jsonl` (the release's live spend), `configs/` (settings files for the measured variants), `results/` (committed results), and `.runs/` (working results, git-ignored) |
