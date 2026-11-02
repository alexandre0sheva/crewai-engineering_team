# Benchmark report: luna-repo

- **Provider / profile:** default / default
- **Strategies and agents:** auto
- **Repeats per task:** 2
- **Created:** 2026-11-01T21:02:00+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.31, python 3.12.12, platform Darwin arm64

> **Small sample:** 4 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 4/4 | 100% (51%–100%) | $0.0021 | $0.0021 | 46s | 0.0 | 7 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 1.5 | 11.0 | 54,928 | 87% | $0.0021 | 46s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · dev | 4/4 | 100% (51%–100%) | $0.0021 | $0.0021 | 46s | 0.0 | 7 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| legacy-feature · auto | 2/2 | 100% (34%–100%) | $0.0019 | $0.0019 | 37s | 0.0 | 2 | 0 |
| seeded-bug · auto | 2/2 | 100% (34%–100%) | $0.0024 | $0.0024 | 51s | 0.0 | 5 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
