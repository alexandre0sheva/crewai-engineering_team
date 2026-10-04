# Benchmark report: screen-hierarchical

- **Provider / profile:** openai / default
- **Strategies:** hierarchical
- **Repeats per task:** 1
- **Created:** 2026-10-04T15:46:05+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.23, python 3.12.12, platform Darwin arm64

> **Small sample:** 2 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hierarchical | 0/2 | 0% (0%–66%) | $0.59 | – | 30.2min | 0.0 | 92 | 0 |

## By task kind and subset

| Strategy / kind / subset | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hierarchical · greenfield · dev | 0/2 | 0% (0%–66%) | $0.59 | – | 30.2min | 0.0 | 92 | 0 |

## By task

| Task / strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| csv-validator · hierarchical | 0/1 | 0% (0%–79%) | $0.58 | – | 30.2min | 0.0 | 39 | 0 |
| todo-web · hierarchical | 0/1 | 0% (0%–79%) | $0.61 | – | 30.2min | 0.0 | 53 | 0 |

## Failures (2)

| Task | Strategy | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| csv-validator | hierarchical | 1 | timeout | – | exceeded the 1800s time limit |
| todo-web | hierarchical | 1 | timeout | – | exceeded the 1800s time limit |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
