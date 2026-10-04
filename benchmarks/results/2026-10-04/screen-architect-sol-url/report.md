# Benchmark report: screen-architect-sol-url

- **Provider / profile:** openai / default
- **Strategies:** pipeline
- **Repeats per task:** 2
- **Created:** 2026-10-04T16:59:29+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.23, python 3.12.12, platform Darwin arm64

> **Small sample:** 2 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 2/2 | 100% (34%–100%) | $0.24 | $0.24 | 17.6min | 1.0 | 37 | 0 |

## By task kind and subset

| Strategy / kind / subset | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · greenfield · dev | 2/2 | 100% (34%–100%) | $0.24 | $0.24 | 17.6min | 1.0 | 37 | 0 |

## By task

| Task / strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| url-shortener · pipeline | 2/2 | 100% (34%–100%) | $0.24 | $0.24 | 17.6min | 1.0 | 37 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
