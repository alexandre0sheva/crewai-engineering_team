# Benchmark report: amend-claude-code

- **Provider / profile:** default / default
- **Strategies and agents:** claude-code
- **Repeats per task:** 2
- **Created:** 2026-11-17T11:13:54+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64
- **Agent claude-code:** 2.1.287 (Claude Code); model: claude-sonnet-5-5; billing: subscription

> **Small sample:** 6 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-code | 6/6 | 100% (61%–100%) | plan (≈$0.14) | unknown | 37s | 0.0 | 0 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| claude-code | – | – | 234,722 | 90% | plan (≈$0.14) | 37s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-code · brownfield · heldout | 6/6 | 100% (61%–100%) | plan (≈$0.14) | unknown | 37s | 0.0 | 0 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| answer-questions · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.0824) | unknown | 30s | 0.0 | 0 | 0 |
| dependency-upgrade · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.12) | unknown | 37s | 0.0 | 0 | 0 |
| ledger-misleading-trace · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.23) | unknown | 61s | 0.0 | 0 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
