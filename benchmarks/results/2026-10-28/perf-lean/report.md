# Benchmark report: perf-lean

- **Provider / profile:** openai / smoke
- **Strategies:** pipeline
- **Repeats per task:** 1
- **Created:** 2026-10-28T18:22:35+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.31, python 3.12.12, platform Darwin arm64

> **Small sample:** 1 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 1/1 | 100% (21%–100%) | $0.0043 | $0.0043 | 80s | 0.0 | 3 | 0 |

## Where the time and money went, by strategy

| Strategy | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| pipeline | 2.0 | 15.0 | 77,575 | 83% | $0.0043 | 80s |

## By task kind and subset

| Strategy / kind / subset | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · greenfield · dev | 1/1 | 100% (21%–100%) | $0.0043 | $0.0043 | 80s | 0.0 | 3 | 0 |

## By task

| Task / strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| notes-cli · pipeline | 1/1 | 100% (21%–100%) | $0.0043 | $0.0043 | 80s | 0.0 | 3 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
