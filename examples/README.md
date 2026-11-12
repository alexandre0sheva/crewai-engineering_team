# Examples

Three small, runnable scenarios, one per way of working: a project from nothing, a feature in an
existing project, and a bug fix. Each folder has the **request** the team gets, what a successful run
should leave behind, and a committed **run report** (`report.html`: open it in a browser, it is one
self-contained file); each has two: a live one (`report-live.html`) and a scripted one (`report.html`).

| Example | Mode | Starts from | Try it |
|---------|------|-------------|--------|
| [`greenfield-notes/`](greenfield-notes/) | `new` | nothing | a notes command-line tool |
| [`feature-on-legacy/`](feature-on-legacy/) | `feature` | [`repo/`](feature-on-legacy/repo): a small WSGI inventory service | a low-stock report endpoint |
| [`bugfix/`](bugfix/) | `fix` | [`repo/`](bugfix/repo): a shop-cart report with a crash, plus its stack trace | the crash on an empty cart |

They are the same tasks as `notes-cli`, `legacy-feature` and `seeded-bug` of the
[benchmark suite](../docs/BENCHMARKS.md), so what a live run costs and how often it succeeds is
measured, not guessed: see the [results](../benchmarks/results/2026-10-04/). Running one live uses your
model provider and costs real money (the measured cost per success of the default strategy on tasks
this size was about $0.21 with the OpenAI models of that evaluation; yours will differ).

The [`github-actions/`](github-actions/) folder is different: two workflows for CI, not scenarios (a pull-request
review and a fix on a label).

## The live reports

`report-live.html` in each example is the report of a real run with a model, made on 2026-11-12 with the
cheapest preset that passed the [model screen](../docs/BENCHMARKS.md#model-screen-2026-11-01) (every tier on
`openai/gpt-6-luna`, effort low), the local backend and a $0.30 cost cap. One run each, so they show what
that model wrote that day, not how often it succeeds (the [benchmark](../docs/BENCHMARKS.md) measures that).
"Verified" means the controller ran the project's own checks (and, for the fix, the red/green
reproduction) and they passed; the benchmark's hidden checks were not run on these.

| Example | Command | Model | Cost | Tokens | Time | Verdict |
|---------|---------|-------|------|--------|------|---------|
| [`greenfield-notes/`](greenfield-notes/report-live.html) | `do --mode new` | `openai/gpt-6-luna` | $0.0036 | 81,162 | 1m13s | verified (3 tests) |
| [`feature-on-legacy/`](feature-on-legacy/report-live.html) | `feature` | `openai/gpt-6-luna` | $0.0040 | 109,810 | 1m12s | verified (6 tests) |
| [`bugfix/`](bugfix/report-live.html) | `fix` | `openai/gpt-6-luna` | $0.0027 | 59,388 | 46s | verified (4 tests, reproduction red then green) |

Cost is the run's own estimate from the price table. The whole set, with one discarded first attempt that
ran on another model because of the machine's `.env`, cost about $0.06 against the $1.00 allowance
(`benchmarks/spend.jsonl`, task T32). They were made with `engineering_team` 0.2.0 plus the unreleased 0.3.0
work, `crewai` 1.15.31, macOS arm64; reproduce one with the commands in the example's README and
`--config benchmarks/configs/screen/luna.toml`.

## About the committed (scripted) reports

The reports are real reports from real runs of the controller (board, checks, verification, cost,
diff), **but the teammates were scripted**: the offline demo runner that powers `ui --demo` acted
them out, writing the benchmark suite's reference solution instead of a model's. So the reports show
what a report looks like and prove the pipeline works end to end without a key; they say nothing about
what a model would write, and their cost and token numbers are the script's. Regenerate them with

```bash
uv run python scripts/make_example_reports.py            # all three (about a minute, no key)
```

Machine-specific paths are replaced by `<workspace>`.

## Running one yourself

```bash
uv sync                      # once
cp .env.example .env         # add your provider key
uv run engineering-team doctor
```

Then follow the example's own README. A trial without a key: `uv run engineering-team ui --demo`
([USAGE.md](../docs/USAGE.md#demo-mode)).
