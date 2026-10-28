# Benchmark report: perf-baseline

- **Provider / profile:** openai / smoke
- **Strategies:** single, pipeline
- **Repeats per task:** 1
- **Created:** 2026-10-28T16:18:53+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.31, python 3.12.12, platform Darwin arm64

> **Small sample:** 2 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 1/1 | 100% (21%–100%) | $0.0300 | $0.0300 | 6.4min | 0.0 | 11 | 0 |
| single | 1/1 | 100% (21%–100%) | $0.0047 | $0.0047 | 78s | 0.0 | 0 | 0 |

## Where the time and money went, by strategy

| Strategy | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| pipeline | 7.0 | 84.0 | 1,110,885 | 89% | $0.0300 | 6.4min |
| single | 1.0 | 15.0 | 142,177 | 87% | $0.0047 | 78s |

## By task kind and subset

| Strategy / kind / subset | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · greenfield · dev | 1/1 | 100% (21%–100%) | $0.0300 | $0.0300 | 6.4min | 0.0 | 11 | 0 |
| single · greenfield · dev | 1/1 | 100% (21%–100%) | $0.0047 | $0.0047 | 78s | 0.0 | 0 | 0 |

## By task

| Task / strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| notes-cli · pipeline | 1/1 | 100% (21%–100%) | $0.0300 | $0.0300 | 6.4min | 0.0 | 11 | 0 |
| notes-cli · single | 1/1 | 100% (21%–100%) | $0.0047 | $0.0047 | 78s | 0.0 | 0 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
