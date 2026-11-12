# Examples

Three small scenarios, one per way of working: a project from nothing, a feature in an existing project,
and a bug fix. Each has the request the team gets, what a successful run leaves behind and run reports you
can open in the browser (each is one self-contained HTML file). The scenarios, their repositories and how
to run them yourself are in [`examples/`](../examples/README.md).

| Example | Mode | Request | Reports |
|---------|------|---------|---------|
| [Notes CLI](../examples/greenfield-notes/README.md) | `new` | [request](../examples/greenfield-notes/request.md) | [live](reports/greenfield-notes-live.html) · [scripted](reports/greenfield-notes.html) |
| [Low-stock report](../examples/feature-on-legacy/README.md) | `feature` | [request](../examples/feature-on-legacy/request.md) | [live](reports/feature-on-legacy-live.html) · [scripted](reports/feature-on-legacy.html) |
| [Empty-cart crash](../examples/bugfix/README.md) | `fix` | [request](../examples/bugfix/request.md) | [live](reports/bugfix-live.html) · [scripted](reports/bugfix.html) |

## Live and scripted reports

- **Live** reports come from a real run with a model: the model, the cost and the date are in the
  [table below](#the-live-runs) and on the report itself. They show what that model wrote on that day, one
  run each; they are not a benchmark (the [leaderboard](leaderboard.md) is).
- **Scripted** reports come from the offline demo runner behind `ui --demo`: the real pipeline, board,
  checks and report, but the teammates are a script that writes the benchmark suite's reference solution.
  They show what a report looks like and prove the pipeline works without a key; they say nothing about what
  a model writes, and their cost and token numbers are the script's. Anyone can regenerate them offline
  with `uv run python scripts/make_example_reports.py`.

## The live runs

Made on 2026-11-12 with the cheapest preset that passed the
[model screen](BENCHMARKS.md#model-screen-2026-11-01): every tier on `openai/gpt-6-luna` (effort low), the
local backend, a $0.30 cost cap, one run each. *Verified* means the controller ran the project's own checks
and they passed; the benchmark's hidden checks were not run on these, and one run says nothing about how
often the model succeeds.

| Example | Command | Model | Cost | Tokens | Time | Verdict |
|---------|---------|-------|------|--------|------|---------|
| Notes CLI | `do --mode new` | `openai/gpt-6-luna` | $0.0036 | 81,162 | 1m13s | verified (3 tests) |
| Low-stock report | `feature` | `openai/gpt-6-luna` | $0.0040 | 109,810 | 1m12s | verified (6 tests) |
| Empty-cart crash | `fix` | `openai/gpt-6-luna` | $0.0027 | 59,388 | 46s | verified (4 tests; reproduction red, then green) |

The three runs together cost $0.01 (about $0.06 with a discarded first attempt that ran on another model);
every live run of the release is in the spend ledger, `benchmarks/spend.jsonl`.
