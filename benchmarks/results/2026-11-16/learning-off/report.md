# Benchmark report: learning-off

- **Provider / profile:** openrouter / default
- **Strategies and agents:** auto
- **Repeats per task:** 3
- **Created:** 2026-11-16T20:56:08+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 6 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 3/6 | 50% (19%–81%) | $0.0350 | $0.0700 | 4.5min | 0.2 | 40 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 2.2 | 47.3 | 656,045 | 91% | $0.0350 | 4.5min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · heldout | 3/3 | 100% (44%–100%) | $0.0341 | $0.0341 | 4.3min | 0.0 | 26 | 0 |
| auto · greenfield · heldout | 0/3 | 0% (0%–56%) | $0.0359 | – | 4.8min | 0.3 | 14 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ambiguous-export · auto | 0/3 | 0% (0%–56%) | $0.0359 | – | 4.8min | 0.3 | 14 | 0 |
| react-component-bug · auto | 3/3 | 100% (44%–100%) | $0.0341 | $0.0341 | 4.3min | 0.0 | 26 | 0 |

## Failures (3)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| ambiguous-export | auto | 1 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| ambiguous-export | auto | 2 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| ambiguous-export | auto | 3 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
