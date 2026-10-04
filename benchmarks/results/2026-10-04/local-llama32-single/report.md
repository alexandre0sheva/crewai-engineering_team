# Benchmark report: local-llama32-single

- **Provider / profile:** ollama / default
- **Strategies:** single
- **Repeats per task:** 1
- **Created:** 2026-10-04T17:32:35+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.23, python 3.12.12, platform Darwin arm64

> **Small sample:** 1 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single | 0/1 | 0% (0%–79%) | unknown | – | 41s | 0.0 | 0 | 0 |

## By task kind and subset

| Strategy / kind / subset | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single · greenfield · dev | 0/1 | 0% (0%–79%) | unknown | – | 41s | 0.0 | 0 | 0 |

## By task

| Task / strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| notes-cli · single | 0/1 | 0% (0%–79%) | unknown | – | 41s | 0.0 | 0 | 0 |

## Failures (1)

| Task | Strategy | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| notes-cli | single | 1 | failed | add-and-list, ids-not-reused, tag-filter-and-search, delete-and-errors, storage-file | criteria not met: add-and-list, ids-not-reused, tag-filter-and-search, delete-and-errors, storage-file (the team ended: Stage 'build' failed: Required project artifacts are missing or incomplete: README.md (missing), docs/release-report.md (missing). Create or complete them with the project filesystem tools.) |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
