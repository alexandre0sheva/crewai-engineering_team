# Benchmark report: openai-auto-heldout-c

- **Provider / profile:** openai / default
- **Strategies and agents:** auto
- **Repeats per task:** 2
- **Created:** 2026-11-16T19:55:32+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 2 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 0/2 | 0% (0%–66%) | $0.0044 | – | 62s | 0.0 | 2 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 2.0 | 14.5 | 132,082 | 84% | $0.0044 | 62s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · heldout | 0/2 | 0% (0%–66%) | $0.0044 | – | 62s | 0.0 | 2 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ledger-misleading-trace · auto | 0/2 | 0% (0%–66%) | $0.0044 | – | 62s | 0.0 | 2 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| ledger-misleading-trace | auto | 1 | failed | others-unchanged | criteria not met: others-unchanged |
| ledger-misleading-trace | auto | 2 | failed | others-unchanged | criteria not met: others-unchanged |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
