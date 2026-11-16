# Benchmark report: openrouter-auto-heldout-c

- **Provider / profile:** openrouter / default
- **Strategies and agents:** auto
- **Repeats per task:** 1
- **Created:** 2026-11-16T20:04:02+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 3 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 1/3 | 33% (6%–79%) | $0.0987 | $0.30 | 7.2min | 0.0 | 83 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 5.0 | 80.7 | 1,432,512 | 90% | $0.0987 | 7.2min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · heldout | 1/3 | 33% (6%–79%) | $0.0987 | $0.30 | 7.2min | 0.0 | 83 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| behaviour-refactor · auto | 1/1 | 100% (21%–100%) | $0.0620 | $0.0620 | 7.1min | 0.0 | 56 | 0 |
| dependency-upgrade · auto | 0/1 | 0% (0%–79%) | $0.0775 | – | 7.2min | 0.0 | 16 | 0 |
| ledger-misleading-trace · auto | 0/1 | 0% (0%–79%) | $0.16 | – | 17.6min | 0.0 | 11 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| dependency-upgrade | auto | 1 | failed | tests-pass | criteria not met: tests-pass |
| ledger-misleading-trace | auto | 1 | failed | others-unchanged | criteria not met: others-unchanged |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
