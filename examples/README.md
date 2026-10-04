# Examples

Three small, runnable scenarios, one per way of working: a project from nothing, a feature in an
existing project, and a bug fix. Each folder has the **request** the team gets, what a successful run
should leave behind, and a committed **run report** (`report.html`: open it in a browser, it is one
self-contained file).

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

## About the committed reports

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
