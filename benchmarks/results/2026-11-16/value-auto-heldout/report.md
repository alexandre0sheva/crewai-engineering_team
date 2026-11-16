# Benchmark report: value-auto-heldout

- **Provider / profile:** value / default
- **Strategies and agents:** auto
- **Repeats per task:** 2
- **Created:** 2026-11-16T21:32:29+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 10 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 8/10 | 80% (49%–94%) | $0.0059 | $0.0074 | 101s | 0.2 | 16 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 2.7 | 18.9 | 98,350 | 82% | $0.0059 | 101s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · heldout | 6/6 | 100% (61%–100%) | $0.0077 | $0.0077 | 2.0min | 0.0 | 14 | 0 |
| auto · greenfield · heldout | 2/4 | 50% (15%–85%) | $0.0032 | $0.0065 | 85s | 0.5 | 2 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ambiguous-export · auto | 0/2 | 0% (0%–66%) | $0.0026 | – | 85s | 0.0 | 1 | 0 |
| behaviour-refactor · auto | 2/2 | 100% (34%–100%) | $0.0146 | $0.0146 | 2.2min | 0.0 | 1 | 0 |
| cron-scheduler · auto | 2/2 | 100% (34%–100%) | $0.0039 | $0.0039 | 88s | 1.0 | 1 | 0 |
| go-cli-feature · auto | 2/2 | 100% (34%–100%) | $0.0035 | $0.0035 | 104s | 0.0 | 4 | 0 |
| react-component-bug · auto | 2/2 | 100% (34%–100%) | $0.0049 | $0.0049 | 2.1min | 0.0 | 9 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| ambiguous-export | auto | 1 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| ambiguous-export | auto | 2 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
