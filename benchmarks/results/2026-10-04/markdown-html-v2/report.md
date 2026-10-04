# Benchmark report: markdown-html-v2

- **Provider / profile:** openai / default
- **Strategies:** single, pipeline
- **Repeats per task:** 2
- **Created:** 2026-10-04T17:19:21+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.23, python 3.12.12, platform Darwin arm64

> **Small sample:** 4 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 2/2 | 100% (34%–100%) | $0.16 | $0.16 | 8.8min | 0.5 | 29 | 0 |
| single | 2/2 | 100% (34%–100%) | $0.0039 | $0.0039 | 60s | 0.0 | 2 | 0 |

## By task kind and subset

| Strategy / kind / subset | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · greenfield · dev | 2/2 | 100% (34%–100%) | $0.16 | $0.16 | 8.8min | 0.5 | 29 | 0 |
| single · greenfield · dev | 2/2 | 100% (34%–100%) | $0.0039 | $0.0039 | 60s | 0.0 | 2 | 0 |

## By task

| Task / strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| markdown-html · pipeline | 2/2 | 100% (34%–100%) | $0.16 | $0.16 | 8.8min | 0.5 | 29 | 0 |
| markdown-html · single | 2/2 | 100% (34%–100%) | $0.0039 | $0.0039 | 60s | 0.0 | 2 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
