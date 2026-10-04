# Benchmark report: brownfield-pipeline

- **Provider / profile:** openai / default
- **Strategies:** pipeline
- **Repeats per task:** 1
- **Created:** 2026-10-04T17:05:11+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.23, python 3.12.12, platform Darwin arm64

> **Small sample:** 4 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 4/4 | 100% (51%–100%) | $0.0477 | $0.0477 | 2.1min | 0.2 | 15 | 0 |

## By task kind and subset

| Strategy / kind / subset | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · brownfield · dev | 3/3 | 100% (44%–100%) | $0.0557 | $0.0557 | 2.1min | 0.3 | 13 | 0 |
| pipeline · brownfield · heldout | 1/1 | 100% (21%–100%) | $0.0238 | $0.0238 | 80s | 0.0 | 2 | 0 |

## By task

| Task / strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| add-tests · pipeline | 1/1 | 100% (21%–100%) | $0.0274 | $0.0274 | 2.0min | 1.0 | 0 | 0 |
| behaviour-refactor · pipeline | 1/1 | 100% (21%–100%) | $0.0238 | $0.0238 | 80s | 0.0 | 2 | 0 |
| legacy-feature · pipeline | 1/1 | 100% (21%–100%) | $0.10 | $0.10 | 4.0min | 0.0 | 8 | 0 |
| seeded-bug · pipeline | 1/1 | 100% (21%–100%) | $0.0396 | $0.0396 | 2.1min | 0.0 | 5 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
