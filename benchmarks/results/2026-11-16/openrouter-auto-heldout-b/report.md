# Benchmark report: openrouter-auto-heldout-b

- **Provider / profile:** openrouter / default
- **Strategies and agents:** auto
- **Repeats per task:** 2
- **Created:** 2026-11-16T19:22:41+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 2 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 0/2 | 0% (0%–66%) | $0.13 | – | 14.9min | 0.5 | 40 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 6.0 | 98.5 | 1,549,716 | 86% | $0.13 | 14.9min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · heldout | 0/2 | 0% (0%–66%) | $0.13 | – | 14.9min | 0.5 | 40 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| answer-questions · auto | 0/2 | 0% (0%–66%) | $0.13 | – | 14.9min | 0.5 | 40 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| answer-questions | auto | 1 | failed | code-unchanged | criteria not met: code-unchanged |
| answer-questions | auto | 2 | failed | code-unchanged | criteria not met: code-unchanged |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
