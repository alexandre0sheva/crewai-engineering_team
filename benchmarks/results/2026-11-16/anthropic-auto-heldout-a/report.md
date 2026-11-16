# Benchmark report: anthropic-auto-heldout-a

- **Provider / profile:** anthropic / default
- **Strategies and agents:** auto
- **Repeats per task:** 2
- **Created:** 2026-11-16T19:45:10+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 10 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 8/10 | 80% (49%–94%) | $0.0314 | $0.0393 | 114s | 0.2 | 13 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 1.9 | 12.4 | 140,086 | 46% | $0.0314 | 114s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · heldout | 6/6 | 100% (61%–100%) | $0.0403 | $0.0403 | 89s | 0.0 | 10 | 0 |
| auto · greenfield · heldout | 2/4 | 50% (15%–85%) | $0.0181 | $0.0362 | 2.2min | 0.5 | 3 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ambiguous-export · auto | 0/2 | 0% (0%–66%) | $0.0158 | – | 2.3min | 0.5 | 3 | 0 |
| cron-scheduler · auto | 2/2 | 100% (34%–100%) | $0.0204 | $0.0204 | 116s | 0.5 | 0 | 0 |
| go-cli-feature · auto | 2/2 | 100% (34%–100%) | $0.0269 | $0.0269 | 2.5min | 0.0 | 6 | 0 |
| react-component-bug · auto | 2/2 | 100% (34%–100%) | $0.0116 | $0.0116 | 89s | 0.0 | 3 | 0 |
| review-auth-change · auto | 2/2 | 100% (34%–100%) | $0.0823 | $0.0823 | 30s | 0.0 | 1 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| ambiguous-export | auto | 1 | failed | behaviour-matches-assumption | criteria not met: behaviour-matches-assumption |
| ambiguous-export | auto | 2 | failed | behaviour-matches-assumption | criteria not met: behaviour-matches-assumption |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
