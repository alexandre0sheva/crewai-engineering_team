# Benchmark report: glm-5.3-flash-notes-cli

- **Provider / profile:** default / default
- **Strategies and agents:** single
- **Repeats per task:** 2
- **Created:** 2026-11-01T21:08:22+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.31, python 3.12.12, platform Darwin arm64

> **Small sample:** 2 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single | 1/2 | 50% (9%–91%) | $0.0460 | $0.0920 | 6.5min | 0.0 | 7 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| single | 1.0 | 31.0 | 490,294 | 83% | $0.0460 | 6.5min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single · greenfield · dev | 1/2 | 50% (9%–91%) | $0.0460 | $0.0920 | 6.5min | 0.0 | 7 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| notes-cli · single | 1/2 | 50% (9%–91%) | $0.0460 | $0.0920 | 6.5min | 0.0 | 7 | 0 |

## Failures (1)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| notes-cli | single | 2 | failed | add-and-list, tag-filter-and-search | criteria not met: add-and-list, tag-filter-and-search |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
