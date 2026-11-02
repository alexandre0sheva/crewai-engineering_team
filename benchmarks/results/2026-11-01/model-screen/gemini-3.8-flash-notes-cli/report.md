# Benchmark report: gemini-3.8-flash-notes-cli

- **Provider / profile:** default / default
- **Strategies and agents:** single
- **Repeats per task:** 2
- **Created:** 2026-11-01T21:27:04+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.31, python 3.12.12, platform Darwin arm64

> **Small sample:** 2 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single | 0/2 | 0% (0%–66%) | $0.0796 | – | 2.2min | 0.0 | 5 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| single | 1.0 | 10.0 | 79,378 | 59% | $0.0796 | 2.2min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single · greenfield · dev | 0/2 | 0% (0%–66%) | $0.0796 | – | 2.2min | 0.0 | 5 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| notes-cli · single | 0/2 | 0% (0%–66%) | $0.0796 | – | 2.2min | 0.0 | 5 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| notes-cli | single | 1 | failed | add-and-list, ids-not-reused, tag-filter-and-search, delete-and-errors, storage-file | criteria not met: add-and-list, ids-not-reused, tag-filter-and-search, delete-and-errors, storage-file (the team ended: exit code 1) |
| notes-cli | single | 2 | failed | add-and-list, ids-not-reused, tag-filter-and-search, delete-and-errors, storage-file | criteria not met: add-and-list, ids-not-reused, tag-filter-and-search, delete-and-errors, storage-file (the team ended: exit code 1) |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
