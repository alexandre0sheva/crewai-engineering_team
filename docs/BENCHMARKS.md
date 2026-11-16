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
uv run engineering-team bench triage                    # triage's signals and signals-only guess, offline
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

An imported real-repository task names a `repository` instead of a `fixture/`, its reference is
`reference/solution.patch`, and it needs no `sabotage` ([below](#real-repository-tasks-swe-bench)).

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

## Real-repository tasks (SWE-bench)

The suite's fixtures are small; `bench import-swe` adds real repositories from
[SWE-bench](https://www.swebench.com/), a public set of GitHub issues in Python projects, each with
the commit it was filed against, the fix that closed it, and the tests that judge a fix. Only
instances whose tests are pytest node ids are imported for now (Django's and SymPy's own test
runners are skipped with the reason). Entries such as `[100%]`, fragments of pytest's progress bar
that SWE-bench's log parser kept as tests, are dropped and listed in `swe.json` as
`ignored_tests`.

1. **Download the dataset yourself** (the harness never does): a JSON Lines file of instances, for
   example SWE-bench Lite's test split from Hugging Face (`princeton-nlp/SWE-bench_Lite`), saved with
   `datasets` as `load_dataset("princeton-nlp/SWE-bench_Lite", split="test").to_json("swe-lite.jsonl")`.
   Its rows need `instance_id`, `repo`, `base_commit`, `problem_statement`, `patch`, `test_patch`,
   `FAIL_TO_PASS` and `PASS_TO_PASS` (a list, or a JSON-encoded list as Hugging Face stores it).
2. **Import** the instances you want; `benchmarks/swe-starter.txt` lists 15 from small repositories
   (Flask, seaborn, pytest, pylint) whose tests need no network:

   ```bash
   uv run engineering-team bench import-swe --dataset swe-lite.jsonl --ids-file benchmarks/swe-starter.txt
   uv run engineering-team bench list --tasks-dir benchmarks/tasks-swe
   ```

   Each instance becomes `benchmarks/tasks-swe/<id>/` (git-ignored; `--out DIR` elsewhere, `--force`
   replaces): a `feature` task with intent `bugfix`, split `heldout` (`--split dev` to change it),
   `request.md` = the issue text (hints are not given), `repository` in `task.yaml` (name, URL, base
   commit and the repository's **license**: check it before you publish results; an unknown one is
   reported), `reference/solution.patch` = the instance's fix, and the hidden checks:
   `acceptance/test.patch` with `fail-to-pass` (every test the fix makes pass passes) and
   `pass-to-pass` (every test that passed still does).
3. **Pull the images** the checks run in, the SWE-bench harness's published evaluation images (the
   repository at the base commit, installed): `docker pull swebench/sweb.eval.x86_64.<key>:latest`,
   where `<key>` is the instance id with `__` written `_1776_` (`acceptance/swe.json` names it; a
   missing image is a check `error` that says the command). They are x86-64 images, emulated on an
   Arm machine; `--image-template` points the import at images you built. Each takes about 2 GB unpacked
   (images share a 2.2 GB base): remove them when you are done (`docker rmi <image>`), and the
   repository cache `benchmarks/.cache/` with them; they are fetched again when needed.
4. **Run** like any task, with `--tasks-dir`:

   ```bash
   uv run engineering-team bench run --tasks-dir benchmarks/tasks-swe --fake --fake-solution fixture
   ```

**The repository is fetched at run time**, never committed: the first run of an instance fetches
its base commit (`git fetch --depth 1`) into `<suite>/.cache/repos/` (`--repo-cache DIR` or
`BENCH_REPO_CACHE`; git-ignored) and later runs reuse it. The team's `repo/` is a copy of that tree
with one commit, so the upstream history (and the later fix) is not there to look up.

**The checks** run inside the instance's image with no network: the team's tree is laid over
`/testbed`, the files the test patch touches are put back as they were at the base commit and the
test patch is applied, then `pytest -rA` runs the *files* the group's tests are in and each listed
test must pass (or be an expected failure), as the SWE-bench harness does. Files, not test ids:
SWE-bench stores an id that holds a space cut at that space (`test_x[a b]` as `test_x[a`), and pytest
stops the whole run at an id it cannot find. A cut id is matched against the start of the reported
test name; when several tests share it, a failing one wins. A test patch that no longer applies to the team's tree
fails the criterion; Docker down or a missing image is an `error`, never a fail. A criterion may take
up to 15 minutes (`check_timeout_seconds` in `task.yaml`). `--acceptance-sandbox local` runs the
same steps on this machine over the cached checkout with this Python: only for repositories whose
tests run with your packages, such as the synthetic instance in `tests/test_bench_swe.py`.

**Checked on real instances** (2026-10-29, Docker Desktop on an Arm Mac, x86-64 images emulated):
`pytest-dev__pytest-7373`, `pytest-dev__pytest-5227`, `pallets__flask-4992`,
`mwaskom__seaborn-3010` and `pylint-dev__pylint-7114`, one per starter repository. With `--fake`
the instance's own fix passes both criteria and the untouched repository fails `fail-to-pass` only.
A criterion took 2 to 13 seconds; an image is 1.0 to 1.3 GB to download.

**Offline mode** replays `reference/solution.patch` (the fake team writes the patched files) and
`--fake-solution fixture` leaves the repository as it was; an imported task has no `sabotage`, so
`--fake-solution broken` refuses it. The fetch still needs the network the first time.

## How a run works

1. **Plan.** `--tasks` (ids, or `all`), the filters above, `--strategy` (`auto`, `pipeline`,
   `single`, `team`; repeat or comma-separate; default `pipeline`) and `--repeat N` give the list
   of runs, in the order task, strategy, repeat. A new project takes every strategy; `feature` and
   `fix` run a recipe (`auto`, `pipeline`, `team`); a maintenance task and a review run as written
   (`auto` runs them on its `pipeline` rung). A pair that cannot run is not planned, and the report
   lists the pairs left out. The repository modes have no `--strategy` option, so the run gets its
   strategy as `ENGINEERING_STRATEGY`.
2. **Prepare.** Each run gets its own directory `<out>/<batch>/<task>/<strategy>-<n>/` (default
   `<out>` is `benchmarks/.runs/`, git-ignored). A new project builds in `ws/app`; a brownfield task
   starts from `repo/`, a fresh Git repository on `main` with the fixture as its only commit. The
   request is copied to `request.md`; the task directory and its checks are not copied or named
   anywhere in the run. Each run also gets an empty configuration and experience library of its own
   (`XDG_CONFIG_HOME`, `XDG_DATA_HOME` in the run directory): it reads none of your lessons, and none
   an earlier run of the batch kept. `--library DIR` shares one on purpose (a learning on/off
   comparison; use `--parallel 1`).
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

An allocation can be split by the provider its batches bill (`T33-openai`, `T33-anthropic`, `T33`)
when one provider's balance is smaller than the rest: each batch is charged to its provider's
bucket, and as `--budget-usd` must fit it, that provider is never billed more than its bucket.

`--dry-run` prices the batch from the price table without running anything. The token counts behind
it are **priors**: about the 75th percentile of the committed live runs' tokens per strategy at task
`scale` 1.0, with half the prompt priced as cache reads where the model has a cached-input price
(refitted on 2026-11-16; `team` has no measured run yet); `auto` is priced
as the rung it starts on for the task's `size` (`ladder.start_*`), and an escalation is bounded by
`--run-budget-usd`, not priced. Read the figure as an order of magnitude and set `--budget-usd` as the real limit. `--resume --batch NAME` continues
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
`--agent-billing AGENT=api|subscription`. The bench process usually holds the team's keys (CrewAI
loads `.env`), so a plan run needs `--agent-billing claude-code=subscription` (or `codex=…`); such
a run is started without the agent's API-key variables, so it really uses the signed-in plan. A
plan's runs have **no `cost_usd`** and the report shows
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

## Triage (`bench triage`)

Triage ([ARCHITECTURE.md](ARCHITECTURE.md#triage)) is measured on its own, without running the
team: `benchmarks/triage/cases.yaml` holds 74 labelled requests covering all 14 intents (each at
least three times as the primary one), every size, 12 with no directory, two with an empty one,
and the rest on benchmark fixtures (some without Git), plus three with an explicit mode. Each case
has its label (`intents`, primary first, `size`, `target`, optionally `risk` and `clarify`) and
**the signals the deterministic layer must report** (Git, existing code, stack trace, failing
tests, change references, question form, paths, keyword groups).

```bash
uv run engineering-team bench triage                    # offline: signals checked, signals-only scored
uv run engineering-team bench triage --estimate --provider openai
uv run engineering-team bench triage --live --provider openai --allocation T10 \
  --out benchmarks/results/<date>/triage
```

Offline (the default, free, part of the gate) every case's repository is set up in a temporary
directory (the fixture copied and, unless `git: false`, made a Git repository), its signals are
compared with the expectations (any mismatch fails the command, exit 3), and the signals-only guess
is scored. `--live` adds one call per case on the triage model (the `cheap` tier); like a live
`bench run` it prints the estimate, needs `--allocation`, refuses what the ledger cannot afford,
stops once `--max-cost-usd` (default $0.40) is spent, and appends one ledger line. The scores:
primary-intent accuracy, size and target accuracy, how many of the labelled secondary intents were
found, whether it asked exactly when a case expects questions, signals-only fallbacks (calls that
failed or did not parse), and a confusion matrix of primary intents (`report.md`, `results.json`).

### Triage results (2026-10-29)

`bench triage --live --provider openai --allocation T10`, `openai/gpt-6-luna` (the `cheap` tier,
reasoning effort `none`), one run of the 74 cases: **$0.0109** (estimate $0.0195), no fallbacks.
Files: `benchmarks/results/2026-10-29/triage/` (`report.md` has the confusion matrix and every
miss).

| Triage | Primary intent | Size | Target | Secondary intents found | Asked when expected |
|--------|---------------:|-----:|-------:|------------------------:|--------------------:|
| `gpt-6-luna` + rules | 97.3 % (72/74) | 78.4 % | 100 % | 88.9 % | 1/1 |
| signals only | 83.8 % | 73.0 % | 100 % | 66.7 % | 0/1 |

The two primary-intent misses: "Make it better." on an existing project became `refactor` (with
questions, as the case expects), and a pasted Flask traceback with no directory became
`new_project`: the model's `bugfix` was dropped by the no-repository rule, whose fallback only
recognises the question form, not a trace. Sizes are the weak part: the model calls most plans,
audits and small greenfield requests `medium` where the labels say `small` (of 16 size misses, 12
are one step larger, 1 two steps larger, 3 one step smaller). One run on 74 hand-labelled cases, so read the figures with that sample size in
mind; the labels are the author's.

The signals-only row is the code of that day. Since 2026-11-01 the fallback ranks a named bug above the
tests or docs that come with its fix, a research word last (it is weak evidence outside a question), and
never makes tests, docs or CI the primary intent of a new project: offline it scores **88 %** primary intent
(65/74) with the same size, target and secondary figures (`bench triage`, no model).

## Model screen (2026-11-01)

Which models can drive the team, and at what cost: the evidence behind every
[provider preset](CONFIGURATION.md#provider-presets). Each candidate ran with every tier set to it
(`benchmarks/configs/screen/<model>.toml`): first `doctor --probe-tools`, then `notes-cli`
(greenfield) on `single` and `seeded-bug` (bugfix) and `legacy-feature` (brownfield feature) on
`auto`, the 0.3.0 default, since the repository modes do not run `single`. Two repeats where the
budget allowed, cheapest candidates first; the batches are in
[`benchmarks/results/2026-11-01/model-screen/`](../benchmarks/results/2026-11-01/model-screen/)
(`<model>-<task>/`, probes in `probes/`). `engineering_team` 0.2.0 plus the 0.3.0 work to date, `crewai` 1.15.31, macOS
arm64, `--sandbox local`. Costs are OpenRouter's reported cost for `openrouter/...` models and the
price table (checked 2026-11-01) for the others. The screen cost **$1.99** (allocation T25, $2.90
after $0.10 moved from the reserve with the user's OK for the Sonnet run).

| Model | Probe | Passed | Tasks (passed/runs) | Cost / success | Median time | Tool failures |
|---|---|---|---|---|---|---|
| `openai/gpt-6-luna` (effort low) | ok | **6/6** | notes 2/2, bugfix 2/2, feature 2/2 | **$0.0025** | 51 s | 12 |
| `anthropic/claude-haiku-5-5` | ok | 6/6 | 2/2, 2/2, 2/2 | $0.014 | 59 s | 6 |
| `openrouter/minimax/minimax-m3.1-flash` | ok | 6/6 | 2/2, 2/2, 2/2 | $0.017 | 121 s | 24 |
| `openrouter/deepseek/deepseek-v4.1-flash` | ok | 6/6 | 2/2, 2/2, 2/2 | $0.022 | 59 s | 24 |
| `openai/gpt-6.1-terra` (effort medium) | ok | 4/4 | 2/2, 1/1, 1/1 | $0.042 | 98 s | 4 |
| `openai/gpt-6.1-sol` (effort medium) | ok | 1/1 | notes 1/1 | $0.10 | 98 s | 0 |
| `openrouter/moonshotai/kimi-k2.7-code` | ok | 2/2 | notes 1/1, bugfix 1/1 | $0.11 | 165 s | 12 |
| `anthropic/claude-sonnet-5-5` | ok | 1/1 | notes 1/1 | $0.20 | 82 s | 0 |
| `gemini/gemini-3.8-flash` | ok | 2/4 | notes 2/4 | $0.32 | 214 s | 9 |
| `openrouter/qwen/qwen3-coder-next` | ok | 2/2 | notes 2/2 | $0.042 | 156 s | 19 |
| `openrouter/mistralai/devstral-2512` | ok | 2/2 | notes 2/2 | $0.066 | 105 s | 11 |
| `openrouter/z-ai/glm-5.3-flash` | ok | 1/2 | notes 1/2 | $0.092 | 389 s | 7 |
| `openrouter/z-ai/glm-5.3`, `gemini/gemini-4-argon` | ok | – | probe only (budget) | – | – | – |

What it shows, on a small sample (one to six runs per model, three easy-to-medium tasks):

- **Every candidate calls tools**: all 14 passed the probe, unlike the small local models of 0.2.0.
- **The cheapest model is the cheapest per success.** `gpt-6-luna` passed everything at a quarter
  of a cent per task, 6–9× less than the open-weight models on OpenRouter: OpenAI served 87 % of its
  prompt from cache at $0.01 per million, while OpenRouter's hosts cached less and charge more for
  the cached part. Open-weight models are not the cheap option for this workload.
- **Haiku 5.5** (new, $0.10/$0.50) passed everything, so Anthropic's workers no longer cost what
  its manager costs.
- **Gemini 3.8 Flash** works but is the dearest per success: its two runs at a $0.05 cap were
  stopped by the budget, the two at $0.25 passed at $0.20 and $0.28.
- Not carried on to the repository tasks: `glm-5.3-flash` (one failure, 4–9 minutes a run),
  `qwen3-coder-next` (19 tool failures, one run stopped by its cap) and `devstral-2512` (dearest of
  the cheap tier, both runs stopped by the cap after the checks passed).
- The lead models (Sol, Sonnet, Kimi) were screened as workers on greenfield work only; how well
  they plan and review is for the 0.3.0 evaluation (plan T33), and the presets are provisional
  until then.

Two harness lessons: a run cap below the ladder's own estimate of its first rung (built from the
token priors of [`bench/estimate.py`](../src/engineering_team/bench/estimate.py), 10–50× the measured
cost of these runs) makes `auto` refuse to start (those batches cost nothing and were discarded); and
the ledger checks a batch against the same priors, so expensive models were run one task at a time.
The priors were refitted to these runs afterwards (75th percentile tokens, cache reads priced): Luna
`single` on `notes-cli` is now estimated at $0.025 (was $0.085; measured $0.003-0.005), Sonnet at
$0.49 (measured $0.20).

## Harness tuning (`harness tune`)

The benchmark also drives an improvement loop: `harness tune` (Propose–Probe–Compose) changes a domain's
[harness](HARNESS.md#tuning) on the **dev** tasks of that domain and accepts the result only if the **held-out**
tasks do not get worse. Hidden checks judge every step; no model grades a model.

For each dev task and each of `--k` repeats:

1. **Base rollout.** The team runs the task with the bundled harness (as `bench run` does: its own process, an
   isolated workspace, the hidden checks after it). Lessons are off in every rollout (`learning.enabled = false`), so
   the harness alone is measured.
2. **Propose.** The INV (procedure) and DOM (knowledge) proposers each read that rollout's outcome and trajectory
   summary and propose a candidate. `--mode parallel` (the default): both read the base rollout. `--mode sequential`:
   DOM works on top of INV's candidate (when INV's probe did not do worse) and also reads INV's probe.
3. **Probe.** Each candidate is rolled out on the same task, installed as the team's user harness.
4. **Compose.** The composer merges, keeps one, or keeps the base. The controller overrules a decision the probes do
   not support (a candidate whose probe did worse than the base is never kept). In parallel mode the composed
   harness is probed too.

The controller then composes one harness from the slots' choices that gained on their own probe, best first, and
**evaluates** it: on every dev task (`k` runs), and base and composed on every held-out task (`k` runs each). It is
**adopted only if its dev pass rate is higher than the base's and its held-out pass rate is not lower**; anything else,
including a domain with no held-out task, is `KEEP_BASE`. When no slot gained, nothing is evaluated.

**Ledger and identity.** `ledger.jsonl` in the tuning directory has one line per rollout (`rollout:<role>:<task>:<n>`),
proposer or composer session (`session:<role>:<task>:<n>`) and evaluation run (`eval:<base|final>:<task>:<n>`), with
its cost. A label recorded twice stops the loop. A finished loop checks the identity per tuned task: **4k rollouts
and 3k sessions** in parallel mode, 3k and 3k in sequential mode (its composer picks among probed variants); a loop
whose counts differ is `KEEP_BASE` and exits 3.

**Cost.** `--dry-run` prints the plan (up to `dev·k·(4+1) + 2·held-out·k` rollouts and `3·dev·k` sessions in parallel
mode) and its estimate; a live tuning needs `--budget-usd`, is charged to `--allocation` (default `T33`, which holds
the 0.3.0 tuning share) and is refused by the [spend ledger](#live-spend-cap) like a live batch. Each rollout gets an
equal share of the budget after the sessions' worst case; when the budget cannot pay for the next rollout or session
the loop stops, keeps the base and exits 3. Proposers and the composer use the `lead` tier (the role `tune`).

**Offline.** `harness tune --fake` runs the whole loop with the fake team and proposers that change nothing (so it
keeps the base): it validates the loop, the ledger and the outputs, never the team. The tests drive the same loop with
scripted proposers whose skill the fake team "needs" (`tests/test_harness_tuning_runs.py`). Rollouts run under
`<suite>/.runs/tune-*` (not committed); the tuning directory under `benchmarks/tuning/` is the reviewable record.

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
  comparison**: per pair, the tasks where one has the higher pass rate, an exact two-sided **sign test**
  on those tasks (`p`), and a task-by-contender grid of passed over counted runs. "Better" or "worse" than
  another contender is claimed only where the intervals do not overlap or `p` is below 0.05; otherwise the pair
  is *not distinguishable at this sample size*, and cost and time are what to read. `scripts/compare_batches.py DIR`
  pools batches (a contender is a strategy and provider, or an agent; a batch run with `--config` or `--library` is a
  variant of its own) and prints the same tables across them.
- **Every failure is listed** with the criteria it missed and why. Nothing is averaged away.

## Results (2026-11-16): the 0.3.0 evaluation

One evaluation, on one developer laptop (macOS, Apple silicon), `engineering_team` 0.3.0 (development
version), `crewai` 1.15.41, Python 3.12.12, Docker for the TypeScript and Go checks. The files behind every
number are committed in [`benchmarks/results/2026-11-16/`](../benchmarks/results/2026-11-16/): 28 batches,
each with `report.md`, `results.csv`, `results.json`, `batch.json` (versions, commands, task digests) and one
`result.json` per run. **Costs are what the providers reported or the price table gives, not invoices.** The
whole evaluation made 235 counted runs: 155 billed to the API for **$4.12** (the release's live spend is $6.22
of its $15 cap; `bench spend`) and 80 by Claude Code and Codex on subscription plans, which cost no API money
(Claude Code reports an API-equivalent of $4.09 for its 40 runs; Codex reports tokens only).

**Who ran what.** The team's `auto` strategy on the four presets
([CONFIGURATION.md](CONFIGURATION.md#provider-presets)): `openai` (the work on `gpt-6-luna`, reviewer
`gpt-6.1-terra`), `anthropic` (Sonnet 5.5 and Haiku 5.5), `openrouter` (MiniMax-M3.1-flash, DeepSeek-V4.1-flash)
and `value` (the cheapest model per tier). A few runs of `single`, `pipeline` and `team` on `openai`, and best-of-3
(`attempts.count = 3`). External agents on their **own default settings and models** (Claude Code 2.1.287 on
Sonnet 5.5, Codex 0.162.1, both on a plan) and OpenCode 1.19.4 pinned to the OpenRouter model of the
`openrouter` preset's worker tier (API-billed). Aider is not installed on this machine: it was not measured.
Google's preset was not measured either (no key in this evaluation's budget). Because the agents were not
pinned to one model with the team, **this compares products, not harnesses**: Claude Code ran Sonnet 5.5, the
team's `openai` preset ran `gpt-6-luna`.

**The sample.** Held out: 8 of the 9 tasks, two repeats (the review task is left out because external agents
do not run reviews), 13–25 counted runs per contender, so a rate has an interval of about ±20 points. Dev: 12
tasks (the dev review task is left out the same way). The dev tasks chose 0.2.0's defaults, so they are not
independent evidence; the held-out ones are. Pooled by `scripts/compare_batches.py`.

### Held-out split (8 tasks)

| Contender | Passed | Pass rate (Wilson 95 %) | Cost / success | Median time |
|-----------|--------|-------------------------|----------------|-------------|
| `auto`, preset `openai` | 8/16 | 50 % (28–72 %) | $0.025 | 81 s |
| `auto`, preset `anthropic` | 8/14 | 57 % (33–79 %) | $0.088 | 2.2 min |
| `auto`, preset `openrouter` | 7/13 | 54 % (29–77 %) | $0.13 | 7.1 min |
| `auto`, preset `value` (5 easier tasks) | 8/10 | 80 % (49–94 %) | $0.0074 | 101 s |
| Claude Code (plan) | 10/16 | 62 % (39–82 %) | plan (about $0.10 a run) | 33 s |
| Codex (plan) | 10/16 | 62 % (39–82 %) | plan | 98 s |
| OpenCode (OpenRouter MiniMax) | 11/16 | 69 % (44–86 %) | $0.019 | 2.2 min |
| `pipeline`, `openai` | 2/7 | 29 % (8–64 %) | $0.071 | 107 s |
| `single`, `openai` (greenfield only) | 2/4 | 50 % (15–85 %) | $0.0056 | 67 s |

Passed over counted runs per task (the same numbers, split):

| Task | `auto` openai | `auto` anthropic | `auto` openrouter | Claude Code | Codex | OpenCode |
|------|---------------|------------------|-------------------|-------------|-------|----------|
| `ambiguous-export` | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 |
| `answer-questions` | 1/2 | 0/2 | 0/2 | 2/2 | 2/2 | 2/2 |
| `behaviour-refactor` | 2/2 | 2/2 | 1/1 | 2/2 | 2/2 | 2/2 |
| `cron-scheduler` | 1/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| `dependency-upgrade` | 0/2 | 0/2 | 0/1 | 0/2 | 0/2 | 1/2 |
| `go-cli-feature` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| `ledger-misleading-trace` | 0/2 | – | 0/1 | 0/2 | 0/2 | 0/2 |
| `react-component-bug` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |

**Dev split (12 tasks, two repeats):** `auto` on `openai` 24/25 (96 %, 80–99 %) at $0.016 a success; Claude
Code 22/24 and Codex 22/24 (92 %, 74–98 %); `single` 9/10 at $0.004 (greenfield only); `pipeline` 7/12 (58 %,
32–81 %) at $0.024.

### What the numbers support

- **No contender is distinguishable from another on pass rate.** Every pair's intervals overlap and no
  paired sign test comes below 0.05 (the closest on held-out: `auto`/`openai` against OpenCode, 0 wins to 3,
  p = 0.25; on dev: `auto` against `pipeline`, 5 wins to 0, p = 0.062). With eight tasks a pair has to differ on
  almost every task to be significant, which a suite with three tasks nobody passes cannot show. **The team is
  not shown to be better or worse than Claude Code, Codex or OpenCode at this sample size.**
- **Cost and time do differ, and not in the team's favour on time.** On the cheapest preset the team's
  successes cost $0.007–0.025 each, an order of magnitude below a run of Claude Code (about $0.10 in API terms),
  but those are different models, so it is the price of the models as much as of the harness. At the same model,
  OpenCode on MiniMax cost $0.019 a success with 11/16 passing, against $0.13 a success and 7/13 for the team on
  the `openrouter` preset (which uses MiniMax for its worker tier and DeepSeek for review): **the team is not
  cheaper than a plain agent on the same model.** On time the team is slower: Claude Code's median is 33 s, the
  team's 81 s on `openai` and 7 minutes on `openrouter`.
- **The staged `pipeline` did not help on small tasks and cost more** (dev: 58 % against 96 % for `auto`,
  `auto` winning five tasks and losing none; held-out 2/7). Starting small and escalating is the better default;
  a `pipeline` run failed for reasons the cheaper rung did not meet: `markdown-html`'s own `tests` check could
  not run in the generated project (the 0.2.0 `.venv` problem again), and `csv-validator`, `express-api-feature`
  and `legacy-feature` stopped at the controller's verification.
- **Everything the checks cannot see is not measured here:** budget caps, the verification report, review,
  resumability, isolation. A team that reaches the same pass rate with those is not worth less; it is not shown
  to be worth more by this table.

### Every failure and its cause

| Task | Contenders | Cause |
|------|-----------|-------|
| `ambiguous-export` | **All** (26 of 26 runs: every preset, every agent, `single`, `pipeline`, best-of-3) | 24 runs recorded no document that says which reading of "sorted by name" they chose (`assumption-recorded`; the team's own verification passes, so nothing reaches the ladder); two Anthropic runs stated one but the tool sorted the other way. The task scores a stated assumption that the tool follows; no contender did it unprompted |
| `ledger-misleading-trace` | All, including `pipeline` and `team` probes | Every fix put its regression test into the existing `tests/test_import.py`, and the check `others-unchanged` demands that file stays byte-identical. **This looks stricter than the request**, which asks for a regression test "in the suite" without saying where; both subscription agents failed it the same way. The task is kept as is until it is amended in 0.3.x, with every contender's run re-judged; it is reported, not corrected after the fact. (`unreadable-stops-import` also failed in one `pipeline` run: the genuine bug.) The ladder also sized it `small` (37 files, under the 40 that make a project `medium`), so `auto` started on `solo` and never escalated |
| `dependency-upgrade` | 14 of 15 runs (`auto`, `pipeline`, best-of-3 and the agents; OpenCode passed once) | `tests-pass` failed because `test_totals.py` was changed (the same shape as the row above: the upgrade needs the existing tests adapted, or a new test placed there) |
| `answer-questions` | `auto` (5 of 6 runs; all six agent runs passed) | All four answers and their citations were right; `code-unchanged` failed because the `maintain docs` route the task runs through also edited `README.md`. The route the team has for questions (`do --mode answer`, read-only) is not what this task exercises yet |
| `docs-cli-reference` | `auto` 1 of 2, Claude Code 2 of 2, Codex 2 of 2, `pipeline` 1 of 1 | `defaults-stated`: the global `--file` option's default `tasks.json` is not stated |
| `cron-scheduler` | `auto`/`openai` once, best-of-3 once | `dom-dow-rule` and `validation`: day-of-month/day-of-week and input validation misses in the generated scheduler |
| `csv-validator`, `markdown-html`, `express-api-feature`, `legacy-feature` | `pipeline` (one run each) | See above; `single` also failed one `markdown-html` run on `inline-formatting` |

### Harness runs of the evaluation

- **Learning on and off** (`learning-on`, `learning-off`; `auto` on `openrouter`, `ambiguous-export` and
  `react-component-bug`, three repeats each, one shared library for the *on* arm): 3/6 and 3/6, $0.205 and
  $0.210. **No effect is measurable, and the check could not have shown one on `ambiguous-export`**: the team's
  own verification passes there, so reflection stores no lesson (a lesson needs evidence of a failure). One lesson
  was stored (a tool-argument convention, from the first `react-component-bug` run) and the next two runs
  received it; they passed in both arms at the same cost. The domain it was filed under is `bugfix/python` for a
  TypeScript project, which is a stack-detection gap, not a measurement.
- **Harness tuning** (`harness tune --domain bugfix/python --k 1`, `openrouter`): **KEEP_BASE**, $0.08. The only
  dev task of the domain (`seeded-bug`) already passes, so no variant could beat the base, and the composer's
  reply hit the 4,000-token output cap on a reasoning model (`LengthFinishReasonError`); the decision did not
  depend on it, but the cap needs raising for reasoning models. Its ledger line said "unknown" and counted as the
  $2.03 estimate until corrected to the measured cost.
- **Best-of-3** (`attempts.count = 3`, `openai`): 1/6 on `ambiguous-export`, `cron-scheduler` and
  `dependency-upgrade` (plus 0/2 more on `dependency-upgrade` in its own batch) at $0.30 a success, against
  $0.025 for a single attempt. The attempts do not fix what the controller cannot see failing, and they cost
  about twelve times as much.
- **Ladder:** of the 104 `auto` runs, 90 went through the ladder (the other 14 are reviews and best-of-3, which do
  not climb it) and **none escalated**: each stayed on the rung it started on (`solo` for small tasks, `team` for
  `express-api-feature`, `pipeline` for medium ones), and the controller's own verification passed on all 29 that then
  failed a hidden check. The thresholds could not be tuned from this
  data, and the ladder's value as an *escalation* mechanism is not shown; its value as a *starting* rung is
  (see `pipeline` above).

### Decisions

Each default is kept or changed on a batch; "kept" means the evaluation gave no reason to move it.

| Setting | Before | Now | Evidence |
|---------|--------|-----|----------|
| `strategy` | `auto` (provisional) | **`auto`** | Dev: `auto` 24/25 against `pipeline` 7/12 (`openai-auto-dev`, `openai-pipeline-dev-a`, `-a2`, `-b`), five task wins to none, p = 0.062, at lower cost; held-out `pipeline` 2/7 (`openai-pipeline-heldout`, `openai-rungs-ledger`) |
| `ladder.start_*`, `max_rung` | provisional | **kept** (`solo`, `team`, `pipeline`; `pipeline`) | No run escalated in any batch; on dev `auto` never lost a task to `pipeline` (five wins); on held-out `pipeline` won one (`cron-scheduler`, 2/2 against 1/2); `ledger-misleading-trace` was passed by no rung (`pipeline`, `team`: `openai-rungs-ledger`) or agent |
| `lean` size thresholds (`MEDIUM_REPO_FILES = 40`) | provisional | **kept, with a known gap** | A 2,013-line, 37-file project is sized `small`; changing a size rule on one task that every contender fails would be tuning on the held-out split |
| `attempts.count`, `ladder_count`, `max_rung = "attempts"` | 1, 3, off | **kept** (off unless asked) | `openai-attempts3-heldout`, `openai-attempts3-dependency`: 1/8 at about twelve times the cost |
| `learning.enabled`, `learning.review` | on, `auto` | **kept** | `learning-on` against `learning-off`: 3/6 each; reflection added no measurable cost ($0.032 against $0.034 a run on `react-component-bug`); nothing here shows a gain either, so `review = "manual"` stays an option for teams that want to read a lesson before it is used |
| Presets (`openai`, `anthropic`, `openrouter`, `value`), default provider `openai` | provisional | **kept; costs per success above** | Held-out table. `value` was run on 5 easier tasks (8/10 at $0.0074) and is not comparable with the others on pass rate; on the same five tasks `openai` passed 7/10 |
| `mcp_serve.max_cost_usd` | 5.0 (provisional) | **5.0 kept as a ceiling** | The dearest of the 155 priced runs cost $0.19; delegated runs were not measured separately |
| Always-on text cap (1,000 characters), `acceptance.max_attempts`, `context.*`, `openrouter.quantizations`, the tune output cap | provisional | **still provisional** | Not measured by this evaluation; the tune cap needs raising (above) |

### Problems this evaluation found

- **OpenCode edited the suite's own fixtures.** An external agent inherited `PWD` from the bench process,
  which points at the checkout, so OpenCode (Bun) worked in the repository instead of its workspace: it fixed
  `benchmarks/tasks/react-component-bug/fixture/` and installed packages there. Fixed (`PWD` is set to the
  workspace for every agent; `tests/test_bench_agents.py`), the fixture was restored from Git, and the two
  affected probes are not in the results.
- **Never-started runs are not results.** 19 runs were refused at start by their run cap (`No rung fits the
  budget`, `Not started`, or skipped because `--budget-usd` was below parallelism × `--run-budget-usd`); they cost
  nothing, were counted as failures by the report, and were removed and rerun in new batches
  (`openai-auto-dev-b`, `openai-pipeline-dev-a2`, `openai-attempts3-dependency`, the `-b` batches). A batch's
  budget must be at least `--parallel` × `--run-budget-usd` plus what it has spent.
- **Cost accounting:** a tuning run's ledger line was `unknown` and counted at its $2.03 estimate; it is corrected
  to the measured $0.08 in `spend.jsonl`.

### Limits of this evaluation

- Eight held-out tasks, two repeats: the intervals are wide and one failure moves a rate ten points. A claim of
  "better" or "worse" needs non-overlapping intervals or a significant paired test; this evaluation has neither.
- Four tasks nobody passes (`ambiguous-export`, `ledger-misleading-trace`, `dependency-upgrade`, and
  `docs-cli-reference` for most) and two whose check looks stricter than the request: they say little about the
  contenders and a lot about the suite.
- External agents ran on their own models and settings and on plans; the team ran on cheap models. A fair
  harness comparison pins one model for both.
- One machine, one day, one provider per batch; costs are price-table or provider-reported figures.

### Reproduce

```bash
uv run engineering-team bench run --split heldout --strategy auto --repeat 2 --provider openai \
  --budget-usd 2 --run-budget-usd 0.5 --allocation T33-openai --allow-failures
uv run engineering-team bench run --split heldout --agent claude-code,codex \
  --agent-billing claude-code=subscription --agent-billing codex=subscription --repeat 2 \
  --budget-usd 0.5 --allocation T33 --out ../agents-out      # outside the checkout
uv run python scripts/compare_batches.py benchmarks/results/2026-11-16 --split heldout --exclude-intent review
```

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

- Most tasks are small; one fixture has 2,000 lines. Real-world repositories come from
  `bench import-swe` (pytest-based SWE-bench instances only, judged by the instance's own tests,
  which can be incomplete) and need Docker and the SWE-bench images. TypeScript and Go have a few tasks each; UI polish and long-running work are not
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
| `src/engineering_team/bench/` | the harness (in the wheel): `tasks` (schema, loading), `selection` (filters), `plan` and `prepare` (what runs and how), `execution` (one run, and `judge`, the hidden-check verdict both kinds share), `agents/` and `agent_run` (external agents, [above](#external-agent-baselines---agent)), `compare` and `export` (paired comparison, `results.json`), `batch` (parallel, budget, resume), `acceptance`, `checklib` and `reviewlib` (the hidden checks' runner and helpers), `metrics` and `results` (what a run records), `report` and `stats`, `estimate` (`--dry-run`), `spend` (the release ledger), `fake_team` (offline mode), `swe`, `repos`, `swe_check` and `swe_runner` (SWE-bench import, repository cache, and its checks), `triage_eval` (the triage cases and their scores) |
| `src/engineering_team/learning/tuning/`, `learning/ledger.py`, `cli/harness_tune_command.py` | `harness tune` and `harness adopt`: candidate trees and their validation (`tree`), rollouts through this harness (`rollouts`), the proposer and composer prompts and sessions (`prompts`, `sessions`), the loop (`loop`), the controller's composing and acceptance rules (`compose`), the tuning directory and adoption (`output`), the ledger and its identity (`ledger`) |
| `src/engineering_team/cli/bench_*.py` | `bench list` and `bench run` (`bench_command`), `bench report` and `--dry-run` (`bench_report_command`), `bench spend` (`bench_spend_command`), `bench import-swe` (`bench_swe_command`), `bench triage` (`bench_triage_command`), `--agent` options (`bench_agents`) |
| `benchmarks/` | the suite (repository only): `tasks/`, `triage/cases.yaml` (labelled requests for `bench triage`), `images/` (Docker images for the Node and Go checks), `budget.toml` and `spend.jsonl` (the release's live spend), `tuning/` ([harness tuning](#harness-tuning-harness-tune) results to review), `configs/` (settings files for the measured variants), `results/` (committed results), `swe-starter.txt` (SWE-bench instances to import), and `.runs/`, `.cache/` and `tasks-swe/` (working results, fetched repositories and imported tasks, git-ignored) |
| `scripts/compare_batches.py`, `scripts/build_leaderboard.py` | pooled per-contender comparison across batches (sign test); the leaderboard generator |
