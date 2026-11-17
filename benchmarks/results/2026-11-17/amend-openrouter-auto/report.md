# Benchmark report: amend-openrouter-auto

- **Provider / profile:** openrouter / default
- **Strategies and agents:** auto
- **Repeats per task:** 2
- **Created:** 2026-11-17T11:46:02+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 4 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 3/4 | 75% (30%–95%) | $0.13 | $0.17 | 11.4min | 0.2 | 64 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 6.5 | 70.8 | 994,937 | 85% | $0.13 | 11.4min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · heldout | 3/4 | 75% (30%–95%) | $0.13 | $0.17 | 11.4min | 0.2 | 64 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| answer-questions · auto | 2/2 | 100% (34%–100%) | $0.14 | $0.14 | 10.7min | 0.0 | 24 | 0 |
| dependency-upgrade · auto | 1/2 | 50% (9%–91%) | $0.12 | $0.24 | 16.9min | 0.5 | 40 | 0 |

## Failures (1)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| dependency-upgrade | auto | 2 | failed | tests-pass | criteria not met: tests-pass |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
