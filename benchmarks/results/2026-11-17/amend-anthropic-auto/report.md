# Benchmark report: amend-anthropic-auto

- **Provider / profile:** anthropic / default
- **Strategies and agents:** auto
- **Repeats per task:** 2
- **Created:** 2026-11-17T10:28:25+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 4 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 4/4 | 100% (51%–100%) | $0.10 | $0.10 | 3.5min | 0.0 | 5 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 6.0 | 21.2 | 253,768 | 53% | $0.10 | 3.5min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · heldout | 4/4 | 100% (51%–100%) | $0.10 | $0.10 | 3.5min | 0.0 | 5 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| answer-questions · auto | 2/2 | 100% (34%–100%) | $0.13 | $0.13 | 3.5min | 0.0 | 2 | 0 |
| dependency-upgrade · auto | 2/2 | 100% (34%–100%) | $0.0764 | $0.0764 | 3.1min | 0.0 | 3 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
