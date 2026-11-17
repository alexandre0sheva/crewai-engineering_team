# Benchmark report: amend-opencode

- **Provider / profile:** default / default
- **Strategies and agents:** opencode
- **Repeats per task:** 2
- **Created:** 2026-11-17T11:21:16+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64
- **Agent opencode:** 1.19.4; model: openrouter/minimax/minimax-m3.1-flash; billing: api

> **Small sample:** 6 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| opencode | 6/6 | 100% (61%–100%) | $0.0470 | $0.0470 | 2.7min | 0.0 | 0 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| opencode | – | – | 1,466,160 | 94% | $0.0470 | 2.7min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| opencode · brownfield · heldout | 6/6 | 100% (61%–100%) | $0.0470 | $0.0470 | 2.7min | 0.0 | 0 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| answer-questions · opencode | 2/2 | 100% (34%–100%) | $0.0067 | $0.0067 | 86s | 0.0 | 0 | 0 |
| dependency-upgrade · opencode | 2/2 | 100% (34%–100%) | $0.0339 | $0.0339 | 2.7min | 0.0 | 0 | 0 |
| ledger-misleading-trace · opencode | 2/2 | 100% (34%–100%) | $0.10 | $0.10 | 9.5min | 0.0 | 0 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
