# Benchmark report: heldout-cron

- **Provider / profile:** openai / default
- **Strategies:** single, pipeline
- **Repeats per task:** 2
- **Created:** 2026-10-04T17:09:23+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.23, python 3.12.12, platform Darwin arm64

> **Small sample:** 4 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 2/2 | 100% (34%–100%) | $0.0962 | $0.0962 | 11.1min | 0.0 | 17 | 0 |
| single | 2/2 | 100% (34%–100%) | $0.0047 | $0.0047 | 68s | 0.0 | 2 | 0 |

## By task kind and subset

| Strategy / kind / subset | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · greenfield · heldout | 2/2 | 100% (34%–100%) | $0.0962 | $0.0962 | 11.1min | 0.0 | 17 | 0 |
| single · greenfield · heldout | 2/2 | 100% (34%–100%) | $0.0047 | $0.0047 | 68s | 0.0 | 2 | 0 |

## By task

| Task / strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cron-scheduler · pipeline | 2/2 | 100% (34%–100%) | $0.0962 | $0.0962 | 11.1min | 0.0 | 17 | 0 |
| cron-scheduler · single | 2/2 | 100% (34%–100%) | $0.0047 | $0.0047 | 68s | 0.0 | 2 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
