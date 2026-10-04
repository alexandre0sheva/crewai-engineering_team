# Benchmark report: screen-architect-sol

- **Provider / profile:** openai / default
- **Strategies:** pipeline
- **Repeats per task:** 2
- **Created:** 2026-10-04T16:29:37+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.23, python 3.12.12, platform Darwin arm64

> **Small sample:** 8 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 6/8 | 75% (41%–93%) | $0.19 | $0.25 | 9.4min | 0.4 | 97 | 0 |

## By task kind and subset

| Strategy / kind / subset | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · greenfield · dev | 6/8 | 75% (41%–93%) | $0.19 | $0.25 | 9.4min | 0.4 | 97 | 0 |

## By task

| Task / strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| csv-validator · pipeline | 2/2 | 100% (34%–100%) | $0.25 | $0.25 | 10.7min | 0.5 | 35 | 0 |
| markdown-html · pipeline | 0/2 | 0% (0%–66%) | $0.0978 | – | 6.2min | 0.0 | 13 | 0 |
| notes-cli · pipeline | 2/2 | 100% (34%–100%) | $0.19 | $0.19 | 8.8min | 0.0 | 18 | 0 |
| todo-web · pipeline | 2/2 | 100% (34%–100%) | $0.21 | $0.21 | 10.4min | 1.0 | 31 | 0 |
| url-shortener · pipeline | 0/0 | – | unknown | – | – | – | 0 | 0 |

## Failures (4)

| Task | Strategy | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| markdown-html | pipeline | 1 | failed | inline-formatting, code-blocks | criteria not met: inline-formatting, code-blocks (the team ended: Stage 'verify' failed: Not verified (partial): Required check 'tests' could not run (unavailable): Install .venv/bin/python and make sure it is on PATH.; Required check 'tests:2' could not run (unavai) |
| markdown-html | pipeline | 2 | failed | inline-formatting, code-blocks | criteria not met: inline-formatting, code-blocks (the team ended: Stage 'verify' failed: Not verified (partial): Required check 'tests' could not run (unavailable): Install .venv/bin/python and make sure it is on PATH.. See docs/verification.md.) |
| url-shortener | pipeline | 1 | skipped | – | the batch budget was spent before this run could start |
| url-shortener | pipeline | 2 | skipped | – | the batch budget was spent before this run could start |

2 run(s) did not count towards pass rates (harness error or not started).

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
