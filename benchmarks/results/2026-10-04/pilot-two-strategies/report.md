# Benchmark report: pilot-two-strategies

- **Provider / profile:** openai / default
- **Strategies:** pipeline, hierarchical
- **Repeats per task:** 1
- **Created:** 2026-10-04T15:05:23+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.23, python 3.12.12, platform Darwin arm64

> **Small sample:** 2 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hierarchical | 0/1 | 0% (0%–79%) | $0.88 | – | 30.2min | 0.0 | 87 | 0 |
| pipeline | 0/1 | 0% (0%–79%) | $0.0058 | – | 80s | 0.0 | 2 | 0 |

## By task kind and subset

| Strategy / kind / subset | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hierarchical · greenfield · dev | 0/1 | 0% (0%–79%) | $0.88 | – | 30.2min | 0.0 | 87 | 0 |
| pipeline · greenfield · dev | 0/1 | 0% (0%–79%) | $0.0058 | – | 80s | 0.0 | 2 | 0 |

## By task

| Task / strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| notes-cli · hierarchical | 0/1 | 0% (0%–79%) | $0.88 | – | 30.2min | 0.0 | 87 | 0 |
| notes-cli · pipeline | 0/1 | 0% (0%–79%) | $0.0058 | – | 80s | 0.0 | 2 | 0 |

## Failures (2)

| Task | Strategy | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| notes-cli | hierarchical | 1 | timeout | – | exceeded the 1800s time limit |
| notes-cli | pipeline | 1 | failed | add-and-list, ids-not-reused, tag-filter-and-search, delete-and-errors, storage-file | criteria not met: add-and-list, ids-not-reused, tag-filter-and-search, delete-and-errors, storage-file (the team ended: Stage 'plan' failed: The plan cannot be run: WP-1 owns 'README.md', which covers shared file(s) README.md; shared files belong to foundation and integrate. Fix the plan so that every work package owns) |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
