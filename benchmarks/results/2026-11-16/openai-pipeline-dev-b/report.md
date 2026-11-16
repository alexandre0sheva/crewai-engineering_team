# Benchmark report: openai-pipeline-dev-b

- **Provider / profile:** openai / default
- **Strategies and agents:** pipeline
- **Repeats per task:** 1
- **Created:** 2026-11-16T21:27:55+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 5 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 3/5 | 60% (23%–88%) | $0.0065 | $0.0108 | 97s | 0.4 | 6 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| pipeline | 3.2 | 22.8 | 140,505 | 84% | $0.0065 | 97s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · greenfield · dev | 3/5 | 60% (23%–88%) | $0.0065 | $0.0108 | 97s | 0.4 | 6 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| csv-validator · pipeline | 0/1 | 0% (0%–79%) | $0.0095 | – | 3.2min | 2.0 | 4 | 0 |
| markdown-html · pipeline | 0/1 | 0% (0%–79%) | $0.0044 | – | 77s | 0.0 | 0 | 0 |
| notes-cli · pipeline | 1/1 | 100% (21%–100%) | $0.0050 | $0.0050 | 85s | 0.0 | 1 | 0 |
| todo-web · pipeline | 1/1 | 100% (21%–100%) | $0.0055 | $0.0055 | 97s | 0.0 | 1 | 0 |
| url-shortener · pipeline | 1/1 | 100% (21%–100%) | $0.0081 | $0.0081 | 2.9min | 0.0 | 0 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| csv-validator | pipeline | 1 | failed | header-and-usage | criteria not met: header-and-usage |
| markdown-html | pipeline | 1 | failed | quotes-and-rules | criteria not met: quotes-and-rules (the team ended: Stage 'verify' failed: Not verified (partial): Required check 'tests' could not run (unavailable): Install .venv/bin/python and make sure it is on PATH.; Required check 'build' could not run (unavaila) |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
