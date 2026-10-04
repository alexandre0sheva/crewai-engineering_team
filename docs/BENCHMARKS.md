# Benchmarks

How the team is measured: a suite of tasks, each judged by **hidden behavioural checks** the team
never sees, run as isolated subprocesses, summarised with confidence intervals and an honest cost
per success. This file holds the **method** and the **measured results** ([below](#results-2026-10-04));
the offline mode says nothing about quality.

```bash
uv run engineering-team bench list                      # the tasks
uv run engineering-team bench run --fake                # offline: validates the harness, no model
uv run engineering-team bench run --dry-run --strategy pipeline,single --repeat 2 --provider openai
uv run engineering-team bench run --budget-usd 20 --strategy pipeline,single --repeat 2 --parallel 3
uv run engineering-team bench report                    # the latest batch (Markdown + CSV)
```

The suite lives in `benchmarks/` in the source checkout. It is **not** in the wheel or the sdist;
pass `--suite DIR` when running from elsewhere.

## The suite

Ten small tasks, each a request a person could write, with a fixture of at most ~300 lines for the
brownfield ones. Everything is Python and the standard library, so a check never needs a package
install or network access.

| Task | Kind | Mode | Subset | What the team is asked |
|------|------|------|--------|------------------------|
| `notes-cli` | greenfield | `new` | dev | A notes command-line tool with tags, search, and stable ids |
| `csv-validator` | greenfield | `new` | dev | Validate a CSV against a JSON schema (types, unique, bounds, patterns) |
| `url-shortener` | greenfield | `new` | dev | A URL-shortener HTTP API with redirects, stats, and a SQLite file |
| `markdown-html` | greenfield | `new` | dev | A Markdown to HTML converter as a library and a command |
| `todo-web` | greenfield | `new` | dev | A minimal todo web app (forms, JSON API, escaping) |
| `cron-scheduler` | greenfield | `new` | **held out** | Compute a cron expression's next run times |
| `legacy-feature` | brownfield | `feature` | dev | Add a low-stock report to a legacy WSGI inventory service |
| `seeded-bug` | brownfield | `fix` | dev | Fix a `ZeroDivisionError` from a stack trace; add a regression test |
| `add-tests` | brownfield | `maintain add-tests` | dev | Write tests for an untested module |
| `behaviour-refactor` | brownfield | `maintain refactor` | **held out** | Split a 70-line function without changing its behaviour |

`bench list --json` prints the same with each task's criteria.

### A task on disk

```text
benchmarks/tasks/<id>/
  task.yaml        id, title, kind, mode, subset, timeout_seconds, scale, criteria, sabotage
  request.md       the only text the team sees (it is the specification: names, exit codes, formats)
  acceptance/      checks.py: the hidden checks (never copied into a workspace)
  fixture/         brownfield only: the project the team starts from
  reference/       a known-good solution, written over the fixture (or an empty workspace)
  trace.txt        fix mode: the stack trace handed to `fix --trace-file`
```

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

To add a task, copy a similar one, keep to the rules above, and run both halves of
`bench run --fake --tasks <id>` (reference passes, `--fake-solution broken` fails the named
criteria).

## How a run works

1. **Plan.** `--tasks` (ids, or `all`), `--subset dev|heldout|all`, `--strategy` (`pipeline`,
   `hierarchical`, `single`; repeat or comma-separate) and `--repeat N` give the list of runs, in the
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
   directory, with a scrubbed environment (no API keys, a throwaway `HOME`) and a timeout. A check
   that crashes is a failed criterion, never a pass.
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

`--dry-run` prices the batch from the price table without running anything. The token counts behind
it are **priors** (typical tokens per strategy and task `scale`), not measurements: read the figure
as an order of magnitude and set `--budget-usd` as the real limit. `--resume --batch NAME` continues
an interrupted batch, keeping runs that finished (passed, failed, or timed out) and repeating the
rest; a Ctrl-C stops every team process and tells you the command.

## What a run records

`result.json` (schema version 1):

| Field | Meaning |
|-------|---------|
| `outcome` | `passed`, `failed`, `timeout`, `error` (the harness could not run it: not counted), or `skipped` (budget: not counted) |
| `reason`, `criteria` | why it did not pass; every criterion with its pass flag and the check's output |
| `team_status`, `team_verdict`, `exit_code` | what the team said about itself (never the evidence) |
| `duration_seconds` | wall clock of the team process |
| `tokens`, `model_calls`, `tool_calls`, `cost_usd`, `models` | from the team's summary, else rebuilt from its event log (so a crashed run still shows what it spent). `cost_usd` is `null` when a model has no price: unknown, never $0 |
| `repair_rounds` | `verify.repair` events: how often verification sent the team back to fix something |
| `tool_failures` | `tool.call` events with `ok: false` |
| `setup_failures` | setup checks (dependency install and the like) that failed or were unavailable |

## Reading a report

`bench report [BATCH]` (a directory, or a name under `--out`; default the latest) prints Markdown
and writes `report.md` and `results.csv` (one row per run) into the batch.

- **Pass rate** is passed over *counted* runs (`passed`, `failed`, `timeout`) with a **Wilson 95 %
  interval**. The interval is the point: 3 of 3 is "somewhere between 44 % and 100 %", and two
  strategies whose intervals overlap are not distinguished by that batch. Reports with fewer than
  20 counted runs carry a small-sample warning.
- **Cost per successful task** is *all* spend, failed runs included, divided by the number of
  successes. A strategy that is cheap per run but rarely succeeds is not cheap.
- Tables by strategy, by strategy, kind and subset (dev against held out), and by task and strategy,
  with median time, repairs per run, tool failures and setup failures.
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
OpenAI-compatible server. The Ollama and LM Studio runs report an unknown cost although a zero price override was
configured; why was not investigated.

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

- the **reference** solutions pass every check (`bench run --fake`), and
- the **sabotaged** ones fail the criteria they break (`--fake-solution broken`, exit status 3),

so a check that cannot tell a good solution from a bad one fails CI. `tests/test_bench_fake.py`
runs both halves end to end (about a minute each). A fake report says so at the top: it validates the
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

**Held-out subset.** Tasks with `subset: heldout` are never used to tune defaults (strategy, tier
models, `max_repair_rounds`, parallelism). Tune on `--subset dev`; report held-out results
separately; the report's kind and subset table shows the difference. A large gap between the two is
over-fitting to the dev tasks. Ten tasks is a small suite: intervals are wide and a held-out result
from two tasks is an anecdote, which is why the report prints them rather than averaging.

**Running generated code.** The team writes programs and the hidden checks *run them* on your
machine (acceptance checks always run locally, with a scrubbed environment and timeouts, but with
your user's file permissions). Treat a live batch like running any untrusted code: use a container or
VM, a disposable checkout, and a provider key with a spending limit. Fake runs execute only the
repository's own reference solutions.

**Secrets.** The harness never reads `.env` itself. The team's own process loads it as it always
does, which is how it finds an API key (its `ENGINEERING_*` settings are ignored, see above); fake
runs and every acceptance check get a scrubbed environment with no keys.

## Limits

- Tasks are small and Python-only (so checks are hermetic); they do not cover large codebases,
  other ecosystems, UI polish, or long-running work.
- A passing run means the stated behaviours hold, not that the code is good: review quality, tests
  the team wrote, and documentation are only checked where a task says so.
- Models are non-deterministic: use `--repeat` and read the interval, not the point estimate.
- Costs come from the price table (`pricing.toml`); a provider change after the run date makes old
  costs stale. Reports show model names and versions so that can be judged.

## Files

| Path | Role |
|------|------|
| `src/engineering_team/bench/` | the harness (in the wheel): `tasks` (schema, loading), `plan` and `prepare` (what runs and how), `execution` (one run), `batch` (parallel, budget, resume), `acceptance` and `checklib` (the hidden checks' runner and helpers), `metrics` and `results` (what a run records), `report` and `stats`, `estimate` (`--dry-run`), `fake_team` (offline mode) |
| `src/engineering_team/cli/bench_command.py` | `bench list`, `bench run`, `bench report` |
| `benchmarks/` | the suite (repository only), `benchmarks/configs/` (settings files for the measured variants), `benchmarks/results/` (committed results), and `benchmarks/.runs/` (working results, git-ignored) |
